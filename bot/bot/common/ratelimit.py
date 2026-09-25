"""Client-side rate accounting.

`TokenBucket`: continuous refill; `acquire(w)` waits until `w` tokens are available. Used for:
- Arcus per-IP weight bucket (1,500 capacity, 25/s refill; the bot and the scout run below it).

`RollingWindow`: events per rolling window (the WebSocket client's own message cap).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable


class TokenBucket:
    def __init__(self, capacity: float, refill_per_s: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if capacity <= 0 or refill_per_s <= 0:
            raise ValueError("capacity and refill must be positive")
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._tokens = float(capacity)
        self._clock = clock
        self._t = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._t) * self.refill_per_s)
        self._t = now

    def available(self) -> float:
        self._refill()
        return self._tokens

    def try_acquire(self, w: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= w:
            self._tokens -= w
            return True
        return False

    def wait_time(self, w: float = 1.0) -> float:
        self._refill()
        return 0.0 if self._tokens >= w else (w - self._tokens) / self.refill_per_s

    async def acquire(self, w: float = 1.0) -> float:
        """Waits until `w` tokens can be taken. Returns the seconds waited."""
        if w > self.capacity:
            raise ValueError(f"weight {w} exceeds bucket capacity {self.capacity}")
        waited = 0.0
        async with self._lock:
            while True:
                dt = self.wait_time(w)
                if dt <= 0:
                    self._tokens -= w
                    return waited
                await asyncio.sleep(dt)
                waited += dt

    def charge(self, w: float) -> None:
        """Post-flight charge (may go negative, as the venue's own bucket does)."""
        self._refill()
        self._tokens -= w

    def drain(self, seconds: float) -> None:
        """Force the bucket empty for `seconds` (used after a venue 429 with Retry-After)."""
        self._refill()
        self._tokens = min(self._tokens, -seconds * self.refill_per_s)


class RollingWindow:
    """Counts events in a rolling window (e.g. '1000 subscribe messages per minute')."""

    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: deque[float] = deque()

    def _trim(self) -> None:
        cutoff = self._clock() - self.window_s
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def count(self) -> int:
        self._trim()
        return len(self._events)

    def remaining(self) -> int:
        return max(0, self.limit - self.count())

    def try_take(self, n: int = 1) -> bool:
        self._trim()
        if len(self._events) + n > self.limit:
            return False
        now = self._clock()
        self._events.extend([now] * n)
        return True

    def wait_time(self, n: int = 1) -> float:
        self._trim()
        if len(self._events) + n <= self.limit:
            return 0.0
        idx = len(self._events) + n - self.limit - 1
        return max(0.0, self._events[idx] + self.window_s - self._clock())

    async def take(self, n: int = 1) -> None:
        while not self.try_take(n):
            await asyncio.sleep(max(0.05, self.wait_time(n)))

