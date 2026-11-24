"""
Lightweight Single-Flight Async In-Memory TTL Cache for OpenKhazana API.

Eliminates redundant SQLite/PostgreSQL scans and Pandas/ta recomputations across
read-heavy market endpoints (`/overview`, `/heatmap`, `/stocks`, `/funds`,
`/funds/scatter`, `/screener/scan`, `/sectors`, `/news`, `/ipos`, `/calendar`,
`/downloads`, `/macro`, `/assets/index`).

Uses per-key `asyncio.Lock` coalescing so concurrent requests share one computation
rather than stampeding SQLite, and automatically bypasses/invalidates cleanly in
tests or after live scraper / PDF upload mutations.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.config import get_settings

T = TypeVar("T")


class MemoryTTLCache:
    def __init__(self, max_entries: int = 512) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._max_entries = max_entries

    def _is_enabled(self) -> bool:
        # Keep pytest isolated DBs deterministic unless explicitly toggled
        return get_settings().environment.strip().lower() != "test"

    def get(self, key: str) -> tuple[bool, Any]:
        if not self._is_enabled():
            return False, None
        entry = self._store.get(key)
        if entry is None:
            return False, None
        exp, val = entry
        if time.monotonic() >= exp:
            self._store.pop(key, None)
            return False, None
        return True, val

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        if not self._is_enabled() or ttl_seconds <= 0:
            return
        if len(self._store) >= self._max_entries:
            now = time.monotonic()
            expired = [k for k, (exp, _) in self._store.items() if exp <= now]
            for k in expired:
                self._store.pop(k, None)
            if len(self._store) >= self._max_entries:
                # Drop oldest inserted keys
                for k in list(self._store.keys())[: max(1, self._max_entries // 4)]:
                    self._store.pop(k, None)
        self._store[key] = (time.monotonic() + ttl_seconds, value)

    async def get_or_set(
        self,
        key: str,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        hit, val = self.get(key)
        if hit:
            return val
        if not self._is_enabled():
            return await loader()
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit2, val2 = self.get(key)
            if hit2:
                return val2
            fresh = await loader()
            self.set(key, fresh, ttl_seconds)
            return fresh

    def invalidate_prefix(self, prefix: str = "") -> int:
        if not prefix:
            count = len(self._store)
            self._store.clear()
            return count
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            self._store.pop(k, None)
        return len(keys)


api_cache = MemoryTTLCache(max_entries=512)
