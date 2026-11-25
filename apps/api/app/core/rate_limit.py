"""
Lightweight sliding-window IP rate limiter for SaaS / multi-user VPS deployments.
Zero external dependencies; honors `X-Forwarded-For` behind Caddy/Nginx reverse proxies.
Automatically bypassed in `ENVIRONMENT=test` unless explicitly tested directly.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: float) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        dq = self._hits[key]
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = max(1, int(window_seconds - (now - dq[0])))
            return False, retry_after
        dq.append(now)
        # Occasional pruning to prevent unbounded memory growth under bot scans
        if len(self._hits) > 5000:
            stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
            for k in stale[:1000]:
                self._hits.pop(k, None)
        return True, 0

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = SlidingWindowRateLimiter()


def client_ip_from_request(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: float = 60.0) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled or settings.environment.strip().lower() == "test":
        return
    ip = client_ip_from_request(request)
    allowed, retry_after = rate_limiter.check(f"{bucket}:{ip}", limit=limit, window_seconds=window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {bucket}. Please retry in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )
