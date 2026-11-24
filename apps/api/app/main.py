from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.calculators import router as calculators_router
from app.api.hub import router as hub_router
from app.api.market import router as market_router
from app.api.portfolio import router as portfolio_router
from app.api.watchlists import router as watchlists_router
from app.core.config import get_settings
from app.core.database import Base, async_session_factory, engine
from app.core.redis_client import close_redis
from app.models.market import ScraperRun
from app.workers.arq_worker import run_scraper_by_name, sync_all_live_sources
from app.workers.seed import seed_if_empty

logger = logging.getLogger("openkhazana.main")


async def _periodic_live_sync_loop() -> None:
    """In-process staged background scheduler: let initial HTTP requests complete first, then run non-blocking sync."""
    try:
        await asyncio.sleep(8.0)
        logger.info("Phase 1 boot sync: FX/Gold, SBP Macro, PSX Intraday, Crypto, All-AMC NAVs, Intl, News & IPOs...")
        await sync_all_live_sources(include_eod=False, include_deep_backfill=False)

        # Phase 2: progressive non-blocking trickle backfill (PSX EOD history, AMC NAV history, PUCARS PDFs)
        for step_name in ("psx_eod_history", "mufap_history", "psx_deep_fundamentals"):
            if not get_settings().scrapers_enabled:
                break
            try:
                await run_scraper_by_name(step_name, evaluate_after=False)
                await asyncio.sleep(3.0)
            except Exception as exc:
                logger.warning("Staged backfill '%s' warning: %s", step_name, exc)

        while True:
            await asyncio.sleep(900)  # Refresh live quotes, news & next backfill slice every 15 minutes
            if get_settings().scrapers_enabled:
                await sync_all_live_sources(include_eod=True, include_deep_backfill=False)
                await run_scraper_by_name("psx_deep_fundamentals", evaluate_after=False)
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("Background live sync loop error: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.validate_production_security()
    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    # Mark any stale RUNNING scraper runs left over from an unclean shutdown
    try:
        async with async_session_factory() as db:
            await db.execute(
                update(ScraperRun)
                .where(ScraperRun.status == "RUNNING")
                .values(
                    status="INTERRUPTED",
                    finished_at=datetime.now(timezone.utc),
                    error_detail="Interrupted by server restart.",
                )
            )
            await db.commit()
    except Exception:
        pass
    if settings.seed_demo_on_empty_db:
        try:
            async with async_session_factory() as db:
                await seed_if_empty(db)
        except Exception:
            # Allow app startup even before migrations are applied in isolated test contexts
            pass
    sync_task: asyncio.Task | None = None
    if settings.scrapers_enabled and settings.environment.lower() != "test":
        sync_task = asyncio.create_task(_periodic_live_sync_loop())
    try:
        yield
    finally:
        if sync_task is not None:
            sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sync_task
        await close_redis()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Open-source Pakistan financial market hub and multi-asset portfolio tracker API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

app.include_router(auth_router)
app.include_router(hub_router)
app.include_router(market_router)
app.include_router(analytics_router)
app.include_router(portfolio_router)
app.include_router(watchlists_router)
app.include_router(calculators_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "openkhazana-api", "version": settings.app_version}
