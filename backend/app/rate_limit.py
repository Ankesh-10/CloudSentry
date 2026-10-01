"""In-process sliding-window rate limiting.

Two layers:
  * RateLimitMiddleware — a coarse per-client-IP ceiling on every request,
    including the unauthenticated health probes.
  * rate_limit(name, per_minute) — a per-user dependency for expensive or
    sensitive endpoints (discovery, config writes, action execution).

Limits are per process: N replicas allow up to N x the limit. That is enough
to stop a single runaway client or script; put a shared limiter (gateway,
Redis) in front if you need a global cap. Behind a reverse proxy, run uvicorn
with --proxy-headers and FORWARDED_ALLOW_IPS so the client IP is the real one.
"""
import threading
import time
from collections import deque
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.config import settings

WINDOW_SECONDS = 60.0
# Bound memory under a flood of distinct keys (spoofed IPs, many users).
_MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """Record a request. Returns 0 if allowed, else seconds until retry."""
        now = self._clock()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= _MAX_KEYS:
                    self._evict(now)
                q = self._hits[key] = deque()
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(self.window - (now - q[0]), 0.001)
            q.append(now)
            return 0.0

    def _evict(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]:
            del self._hits[k]
        if len(self._hits) >= _MAX_KEYS:
            self._hits.clear()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiters: dict[str, SlidingWindowLimiter] = {}
_limiters_lock = threading.Lock()


def get_limiter(name: str, per_minute: int) -> SlidingWindowLimiter:
    with _limiters_lock:
        limiter = _limiters.get(name)
        if limiter is None or limiter.limit != per_minute:
            limiter = _limiters[name] = SlidingWindowLimiter(per_minute)
        return limiter


def reset_all() -> None:
    with _limiters_lock:
        for limiter in _limiters.values():
            limiter.reset()


def _too_many(retry_after: float) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Rate limit exceeded",
        headers={"Retry-After": str(int(retry_after) + 1)},
    )


def rate_limit(name: str, per_minute: int):
    """Per-user limit for one endpoint group. Use after an auth dependency."""
    from backend.app.auth import get_current_user

    def _check(user: dict = Depends(get_current_user)) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        wait = get_limiter(f"user:{name}", per_minute).hit(str(user["id"]))
        if wait:
            raise _too_many(wait)

    return Depends(_check)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.RATE_LIMIT_ENABLED and request.method != "OPTIONS":
            ip = request.client.host if request.client else "unknown"
            wait = get_limiter("ip", settings.RATE_LIMIT_PER_MINUTE).hit(ip)
            if wait:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(int(wait) + 1)},
                )
        return await call_next(request)
