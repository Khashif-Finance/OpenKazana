from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import delete, select

from app.core.cache import api_cache
from app.core.config import get_settings
from app.core.database import apply_rls_context, async_session_factory
from app.models.market import ScraperRun
from app.models.user import User
from app.scrapers.amc_navs import MufapHistoricalNavScraper
from app.scrapers.base import ScraperParseError, _dump_dlq_payload, is_psx_market_open
from app.scrapers.brokerage_research import BrokerageResearchScraper
from app.scrapers.crypto import CryptoSpotScraper
from app.scrapers.financial_reports import PsxFinancialReportsScraper
from app.scrapers.fmr_parser import FmrReportScraper
from app.scrapers.fx_gold import FxGoldScraper
from app.scrapers.intl_stocks import IntlStocksScraper
from app.scrapers.mufap import MufapNavScraper
from app.scrapers.pk_news import PakistanNewsScraper
from app.scrapers.psx import (
    PsxEodScraper,
    PsxFundamentalsAndPucarsScraper,
    PsxIntradayScraper,
)
from app.scrapers.psx_deep import PsxDeepFundamentalsScraper
from app.scrapers.psx_ipos import PsxIpoAndListingsScraper
from app.scrapers.sbp_macro import SbpMacroScraper
from app.services.alerts import evaluate_alerts
from app.workers.seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("openkhazana.arq_worker")

SCRAPER_REGISTRY = {
    "psx_intraday": PsxIntradayScraper,
    "psx_eod_history": PsxEodScraper,
    "psx_fundamentals": PsxFundamentalsAndPucarsScraper,
    "psx_deep_fundamentals": PsxDeepFundamentalsScraper,
    "psx_reports": PsxFinancialReportsScraper,
    "mufap_nav": MufapNavScraper,
    "mufap_history": MufapHistoricalNavScraper,
    "fmr_reports": FmrReportScraper,
    "sbp_macro": SbpMacroScraper,
    "fx_gold": FxGoldScraper,
    "crypto_spot": CryptoSpotScraper,
    "intl_stocks": IntlStocksScraper,
    "pk_news": PakistanNewsScraper,
    "psx_ipos": PsxIpoAndListingsScraper,
    "brokerage_research": BrokerageResearchScraper,
}


async def on_startup(ctx: dict) -> None:
    """Bootstrap reference catalog and run initial live market sync on worker boot."""
    settings = get_settings()
    if settings.seed_demo_on_empty_db:
        async with async_session_factory() as db:
            await seed_if_empty(db)
    logger.info("ARQ Worker initialized.")


async def sync_all_live_sources(
    include_eod: bool = False,
    include_deep_backfill: bool = False,
    light_only: bool = False,
) -> list[ScraperRun]:
    """Run core live market scrapers sequentially and optionally append EOD/deep backfill steps."""
    if light_only:
        pipeline = ["fx_gold", "sbp_macro", "psx_intraday", "crypto_spot", "pk_news", "psx_ipos", "brokerage_research"]
    else:
        pipeline = ["fx_gold", "sbp_macro", "psx_intraday", "crypto_spot", "mufap_nav", "intl_stocks", "pk_news", "psx_ipos", "brokerage_research", "psx_fundamentals"]
    if include_eod:
        pipeline.append("psx_eod_history")
    if include_deep_backfill:
        pipeline.extend(["mufap_history", "psx_deep_fundamentals"])
    runs: list[ScraperRun] = []
    for name in pipeline:
        try:
            run = await run_scraper_by_name(name, evaluate_after=False)
            runs.append(run)
        except Exception as exc:
            logger.warning("Live sync step '%s' encountered an error: %s", name, exc)
    api_cache.invalidate_prefix("")
    try:
        async with async_session_factory() as db:
            await apply_rls_context(db, bypass=True)
            await evaluate_alerts(db)
    except Exception:
        pass
    return runs


async def run_scraper_by_name(scraper_name: str, existing_run_id: str | None = None, evaluate_after: bool = True) -> ScraperRun:
    """Execute a registered scraper against a DB session (updating a QUEUED ScraperRun if supplied)."""
    scraper_cls = SCRAPER_REGISTRY.get(scraper_name)
    if scraper_cls is None:
        raise ValueError(f"Unknown scraper '{scraper_name}'.")
    scraper = scraper_cls()

    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        if not existing_run_id:
            run = await scraper.execute_with_telemetry(db, use_lock=False)
            api_cache.invalidate_prefix("")
            if evaluate_after and scraper_name in {"psx_intraday", "psx_fundamentals", "crypto_spot", "fx_gold"}:
                await evaluate_alerts(db)
            return run

        run_log = (
            await db.execute(select(ScraperRun).where(ScraperRun.id == existing_run_id))
        ).scalar_one_or_none()
        if run_log is None:
            run = await scraper.execute_with_telemetry(db, use_lock=False)
            api_cache.invalidate_prefix("")
            if evaluate_after and scraper_name in {"psx_intraday", "psx_fundamentals", "crypto_spot", "fx_gold"}:
                await evaluate_alerts(db)
            return run

        run_log.status = "RUNNING"
        run_log.started_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(run_log)

        start = time.monotonic()
        try:
            count = await scraper.run(db)
            run_log.status = "SUCCESS"
            run_log.records_upserted = count
            api_cache.invalidate_prefix("")
            if evaluate_after and scraper_name in {"psx_intraday", "psx_fundamentals", "crypto_spot", "fx_gold"}:
                await evaluate_alerts(db)
        except ScraperParseError as exc:
            logger.exception("Scraper '%s' parse failure: %s", scraper_name, exc)
            await db.rollback()
            p_hash, dlq_path = _dump_dlq_payload(scraper_name, exc.raw_payload)
            run_log.status = "FAILED"
            run_log.raw_payload_hash = p_hash
            run_log.dlq_path = dlq_path
            run_log.error_detail = str(exc)[:1000]
        except Exception as exc:
            logger.exception("Scraper '%s' failed: %s", scraper_name, exc)
            await db.rollback()
            run_log.status = "FAILED"
            run_log.error_detail = str(exc)[:1000]
        finally:
            run_log.finished_at = datetime.now(timezone.utc)
            run_log.latency_ms = int((time.monotonic() - start) * 1000)
            db.add(run_log)
            await db.commit()
            await db.refresh(run_log)
        return run_log


async def run_scraper_job(ctx: dict, scraper_name: str, run_id: str | None = None) -> None:
    """ARQ background task entrypoint for manually triggered scrapers."""
    await run_scraper_by_name(scraper_name, existing_run_id=run_id, evaluate_after=True)


async def task_psx_intraday(ctx: dict) -> None:
    if not get_settings().scrapers_enabled or not is_psx_market_open(datetime.now(timezone.utc)):
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await PsxIntradayScraper().execute_with_telemetry(db, use_lock=True)
        await evaluate_alerts(db)


async def task_psx_eod(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await PsxEodScraper().execute_with_telemetry(db, use_lock=True)


async def task_psx_fundamentals(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await PsxFundamentalsAndPucarsScraper().execute_with_telemetry(db, use_lock=True)
        await evaluate_alerts(db)


async def task_mufap_nav(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await MufapNavScraper().execute_with_telemetry(db, use_lock=True)


async def task_fx_gold(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await FxGoldScraper().execute_with_telemetry(db, use_lock=True)
        await evaluate_alerts(db)


async def task_crypto_spot(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await CryptoSpotScraper().execute_with_telemetry(db, use_lock=True)
        await evaluate_alerts(db)


async def task_intl_stocks(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await IntlStocksScraper().execute_with_telemetry(db, use_lock=True)
        await evaluate_alerts(db)


async def task_sbp_macro(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await SbpMacroScraper().execute_with_telemetry(db, use_lock=True)


async def task_mufap_history(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await MufapHistoricalNavScraper().execute_with_telemetry(db, use_lock=True)


async def task_psx_deep_fundamentals(ctx: dict) -> None:
    if not get_settings().scrapers_enabled:
        return
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await PsxDeepFundamentalsScraper().execute_with_telemetry(db, use_lock=True)


async def task_cleanup_expired_demos(ctx: dict) -> None:
    """Purge expired ephemeral demo users created via /auth/demo (preserving permanent template demo user)."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        await apply_rls_context(db, bypass=True)
        await db.execute(
            delete(User).where(
                User.is_demo.is_(True),
                User.demo_expires_at.is_not(None),
                User.demo_expires_at < now,
            )
        )
        await db.commit()


class WorkerSettings:
    """ARQ Worker configuration with Redis-locked Pakistan & Global market cron schedules (UTC)."""

    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    functions = [
        run_scraper_job,
        task_psx_intraday,
        task_psx_eod,
        task_psx_fundamentals,
        task_mufap_nav,
        task_mufap_history,
        task_sbp_macro,
        task_psx_deep_fundamentals,
        task_fx_gold,
        task_crypto_spot,
        task_intl_stocks,
        task_cleanup_expired_demos,
    ]
    cron_jobs = [
        # Every 5 minutes for PSX market-watch (guarded inside by is_psx_market_open PKT check)
        cron(task_psx_intraday, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        # Crypto every 15 minutes
        cron(task_crypto_spot, minute={2, 17, 32, 47}),
        # FX, Gold & Silver hourly
        cron(task_fx_gold, minute=10),
        # SBP Policy Rate, KIBOR, T-Bills & PIB Yields twice daily
        cron(task_sbp_macro, hour={6, 13}, minute=12),
        # International stocks & Global ETFs (yfinance) every 2 hours on weekdays
        cron(task_intl_stocks, weekday={0, 1, 2, 3, 4}, hour={14, 16, 18, 20, 22}, minute=20),
        # PSX Fundamentals & PUCARS announcements twice daily weekdays (08:45 & 12:30 UTC)
        cron(task_psx_fundamentals, weekday={0, 1, 2, 3, 4}, hour={8, 12}, minute=35),
        # PSX Deep PUCARS PDFs (Balance Sheet/CF) incrementally twice daily
        cron(task_psx_deep_fundamentals, weekday={0, 1, 2, 3, 4}, hour={9, 14}, minute=40),
        # PSX EOD weekdays 17:15 PKT = 12:15 UTC
        cron(task_psx_eod, weekday={0, 1, 2, 3, 4}, hour=12, minute=15),
        # MUFAP & AMC daily NAV 20:30 PKT = 15:30 UTC
        cron(task_mufap_nav, hour=15, minute=30),
        # AMC Historical NAV backfill daily at 21:00 PKT = 16:00 UTC
        cron(task_mufap_history, hour=16, minute=0),
        # Ephemeral demo user TTL cleanup hourly
        cron(task_cleanup_expired_demos, minute=50),
    ]
