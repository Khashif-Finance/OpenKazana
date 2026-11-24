from __future__ import annotations

import json
import logging
import time
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings

logger = logging.getLogger("openkhazana.redis")

_redis_client: Redis | None = None
_redis_retry_after: float = 0.0
_REDIS_COOLDOWN_SECONDS = 120.0


async def get_redis() -> Redis | None:
    """Return a shared Redis client, or None immediately when Redis is in fail-fast cooldown."""
    global _redis_client, _redis_retry_after
    if _redis_client is not None:
        return _redis_client

    now = time.monotonic()
    if now < _redis_retry_after:
        return None

    settings = get_settings()
    client: Redis | None = None
    try:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.35,
            socket_timeout=0.5,
        )
        await client.ping()
        _redis_client = client
        _redis_retry_after = 0.0
        return _redis_client
    except Exception as exc:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
        _redis_retry_after = time.monotonic() + _REDIS_COOLDOWN_SECONDS
        logger.info("Redis unavailable (%s); continuing with SQL DB cache only for %.0fs.", exc, _REDIS_COOLDOWN_SECONDS)
        return None


async def _mark_redis_unavailable(client: Redis | None = None) -> None:
    global _redis_client, _redis_retry_after
    target = client or _redis_client
    _redis_client = None
    _redis_retry_after = time.monotonic() + _REDIS_COOLDOWN_SECONDS
    if target is not None:
        try:
            await target.aclose()
        except Exception:
            pass


async def cache_quote(symbol: str, payload: dict[str, Any], ttl_seconds: int = 600) -> None:
    """Store latest quote payload in Redis with a bounded TTL."""
    client = await get_redis()
    if client is None:
        return
    try:
        await client.set(f"quote:{symbol.upper()}", json.dumps(payload), ex=ttl_seconds)
    except Exception:
        await _mark_redis_unavailable(client)


async def get_cached_quote(symbol: str) -> dict[str, Any] | None:
    """Retrieve a cached quote payload from Redis if present."""
    client = await get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(f"quote:{symbol.upper()}")
        return json.loads(raw) if raw else None
    except Exception:
        await _mark_redis_unavailable(client)
        return None


async def set_cached_intraday_bars(symbol: str, bars: list[dict[str, Any]], ttl_seconds: int = 300) -> None:
    """Store 5M intraday bars in Redis with bounded TTL."""
    client = await get_redis()
    if client is None:
        return
    try:
        await client.set(f"intraday5m:{symbol.upper()}", json.dumps(bars), ex=ttl_seconds)
    except Exception:
        await _mark_redis_unavailable(client)


async def get_cached_intraday_bars(symbol: str) -> list[dict[str, Any]] | None:
    """Retrieve cached 5M intraday bars from Redis if present."""
    client = await get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(f"intraday5m:{symbol.upper()}")
        return json.loads(raw) if raw else None
    except Exception:
        await _mark_redis_unavailable(client)
        return None


async def close_redis() -> None:
    """Gracefully close Redis pool on shutdown."""
    global _redis_client, _redis_retry_after
    _redis_retry_after = 0.0
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
