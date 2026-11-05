"""A tiny in-process rate limiter for alerts, shared by every alert sender."""

from datetime import datetime, timedelta, timezone


class Throttle:
    """One message per key per window; the ones held back are counted so the
    next message can say how many it stands for."""

    def __init__(self, window: timedelta, clock=lambda: datetime.now(timezone.utc)):
        self._window = window
        self._clock = clock
        self._last_sent: dict[str, datetime] = {}
        self._held_back: dict[str, int] = {}

    def take(self, key: str) -> tuple[bool, int]:
        """(send now?, how many were held back since the last one sent)."""
        now = self._clock()
        last = self._last_sent.get(key)
        if last is not None and now - last < self._window:
            self._held_back[key] = self._held_back.get(key, 0) + 1
            return False, 0
        self._last_sent[key] = now
        return True, self._held_back.pop(key, 0)
