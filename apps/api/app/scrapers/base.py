from __future__ import annotations

import gzip
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.models.market import ScraperRun

logger = logging.getLogger(__name__)
PKT_OFFSET = timezone(timedelta(hours=5))


class ScraperParseError(RuntimeError):
    """Raised when an upstream page/feed fails parsing, preserving raw payload for DLQ replay."""

    def __init__(self, message: str, raw_payload: str | bytes = "") -> None:
        super().__init__(message)
        self.raw_payload = raw_payload


def is_psx_market_open(now_utc: datetime | None = None) -> bool:
    """Return True during official Pakistan Stock Exchange trading hours (PKT Mon-Fri)."""
    pkt_now = (now_utc or datetime.now(timezone.utc)).astimezone(PKT_OFFSET)
    weekday = pkt_now.weekday()
    if weekday >= 5:
        return False

    t = pkt_now.time()
    if weekday == 4:  # Friday split session
        return (dt_time(9, 15) <= t <= dt_time(12, 0)) or (dt_time(14, 30) <= t <= dt_time(16, 30))
    return dt_time(9, 30) <= t <= dt_time(15, 30)


def _dump_dlq_payload(scraper_name: str, raw_payload: str | bytes) -> tuple[str | None, str | None]:
    if not raw_payload:
        return None, None
    raw_bytes = raw_payload.encode("utf-8", errors="replace") if isinstance(raw_payload, str) else raw_payload
    payload_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        dlq_dir = Path(get_settings().scraper_dlq_dir)
        dlq_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = dlq_dir / f"{scraper_name}_{stamp}_{payload_hash[:10]}.raw.gz"
        with gzip.open(out_path, "wb") as fh:
            fh.write(raw_bytes)
        return payload_hash, str(out_path)
    except OSError as exc:
        logger.warning("Unable to persist scraper DLQ dump: %s", exc)
        return payload_hash, None


class BaseScraper(ABC):
    """Abstract base scraper recording telemetry, distributed locks, and DLQ dumps."""

    name: str = "base"

    @abstractmethod
    async def run(self, db: AsyncSession) -> int:
        """Execute scrape and persist updates. Returns number of records upserted."""

    async def execute_with_telemetry(self, db: AsyncSession, use_lock: bool = False) -> ScraperRun:
        redis = await get_redis() if use_lock else None
        lock_key = f"lock:scraper:{self.name}"
        if redis is not None:
            acquired = await redis.set(lock_key, "1", nx=True, ex=240)
            if not acquired:
                logger.info("Skipping scraper '%s'; distributed lock held by peer worker.", self.name)
                skipped = ScraperRun(
                    scraper_name=self.name,
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                    status="SKIPPED",
                    records_upserted=0,
                    latency_ms=0,
                    error_detail="Skipped due to active Redis distributed lock.",
                )
                return skipped

        run_log = ScraperRun(
            scraper_name=self.name,
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
        )
        db.add(run_log)
        await db.commit()
        await db.refresh(run_log)

        start = time.monotonic()
        try:
            count = await self.run(db)
            run_log.status = "SUCCESS"
            run_log.records_upserted = count
        except ScraperParseError as exc:
            logger.exception("Scraper '%s' parse failure: %s", self.name, exc)
            await db.rollback()
            p_hash, dlq_path = _dump_dlq_payload(self.name, exc.raw_payload)
            run_log.status = "FAILED"
            run_log.raw_payload_hash = p_hash
            run_log.dlq_path = dlq_path
            run_log.error_detail = str(exc)[:1000]
        except Exception as exc:
            logger.exception("Scraper '%s' failed: %s", self.name, exc)
            await db.rollback()
            run_log.status = "FAILED"
            run_log.error_detail = str(exc)[:1000]
        finally:
            run_log.finished_at = datetime.now(timezone.utc)
            run_log.latency_ms = int((time.monotonic() - start) * 1000)
            db.add(run_log)
            await db.commit()
            await db.refresh(run_log)
            if redis is not None:
                try:
                    await redis.delete(lock_key)
                except Exception:
                    pass

        return run_log
