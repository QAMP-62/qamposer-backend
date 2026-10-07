"""Simple in-process sliding-window rate limiter."""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record a hit for `key` and return whether it is within the limit."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            # Keep memory bounded: forget idle keys
            if len(self._hits) > 10_000:
                for k in [k for k, h in self._hits.items() if not h]:
                    del self._hits[k]
            return True
