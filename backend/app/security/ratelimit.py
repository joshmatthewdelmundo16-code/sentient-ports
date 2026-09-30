"""In-process sliding-window rate limiting — D26.

Correct for a single instance. With several instances each keeps its own window, so the
effective limit is multiplied by the instance count; a shared store (e.g. Redis) would be
needed for a strict global limit. That is documented rather than hidden.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked_for(self, key: str) -> float:
        """Seconds until `key` may try again (0 when allowed). Does not record a hit."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.limit:
                return 0.0
            return max(0.0, self.window_s - (now - q[0]))

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)

    def allow(self, key: str) -> bool:
        """Record a hit if allowed; False when over the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
