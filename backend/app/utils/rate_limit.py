"""Per-key sliding-window rate limiter for endpoints that cost real money or
CPU per call (speech-to-text uploads), keyed by authenticated user id.

In-process by design: it bounds abuse from one account against this
process, it is not a distributed quota. When the API runs as several
replicas each enforces its own window, so the effective ceiling is
limit x replicas -- still a hard bound, which is what matters for cost
control. Move to Redis (INCR + EXPIRE) only if a precise global quota is
ever needed; that must fail open like the rest of the Redis cache layer.
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.models.user import User


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


# 12 clips/minute/user: far above a person dictating, far below a script.
_transcribe_limiter = SlidingWindowLimiter(limit=12, window_seconds=60)


async def transcribe_rate_limit(current_user: User = Depends(get_current_user)) -> User:
    """Dependency for every speech-to-text upload endpoint. Also performs
    authentication, so an endpoint using it needs no separate
    get_current_user dependency."""
    if not _transcribe_limiter.allow(str(current_user.id)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many audio uploads -- please wait a moment")
    return current_user
