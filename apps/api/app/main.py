from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import text, update

from app.api.admin import router as admin_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.calculators import router as calculators_router
from app.api.hub import router as hub_router
from app.api.market import router as market_router
from app.api.markets_v8 import router as markets_v8_router
from app.api.portfolio import router as portfolio_router
from app.api.watchlists import router as watchlists_router
from app.core.cache import api_cache
from app.core.config import get_settings
from app.core.database import Base, async_session_factory, engine
from app.core.redis_client import close_redis
from app.models.market import ScraperRun
from app.workers.arq_worker import sync_all_live_sources
from app.workers.seed import seed_if_empty

logger = logging.getLogger("openkhazana.main")


async def _periodic_live_sync_loop() -> None:
    """
    Lightweight non-blocking background scheduler:
    - Lets user HTTP traffic and startup cache warm-up finish cleanly first (35s grace).
    - Runs fast live quote, macro, and news refreshes every 15 minutes without triggering
      multi-minute full historical PDF/EOD backfills on the HTTP server event loop
      (heavy deep backfills remain manually triggerable from `/status` or ARQ worker).
    """
    try:
        await asyncio.sleep(35.0)
        while True:
            if get_settings().scrapers_enabled:
                logger.info("Scheduled light live sync: FX/Gold, SBP/PBS Macro, PSX Intraday, Crypto, News & IPOs...")
                await sync_all_live_sources(include_eod=False, include_deep_backfill=False, light_only=True)
                api_cache.invalidate_prefix("")
            await asyncio.sleep(900)  # Refresh live quotes, macro & news every 15 minutes
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

app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=5)
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

app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(hub_router)
app.include_router(markets_v8_router)
app.include_router(market_router)
app.include_router(analytics_router)
app.include_router(portfolio_router)
app.include_router(watchlists_router)
app.include_router(calculators_router)


@app.get("/health")
async def health():
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database health check failed: {exc}",
        ) from exc
    return {
        "status": "ok",
        "database": "connected",
        "service": "openkhazana-api",
        "version": settings.app_version,
        "auth_mode": settings.effective_auth_mode,
    }
