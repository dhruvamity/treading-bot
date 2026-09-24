"""Client-side rate accounting.

`TokenBucket`: continuous refill; `acquire(w)` waits until `w` tokens are available. Used for:
- Arcus per-IP weight bucket (1,500 capacity, 25/s refill; the recorder is capped at 50% via a smaller bucket).
- Lighter standard REST (60 requests / rolling minute) and sendTx (60 / minute).

`AdaptiveThrottle`: for Lighter REST whose effective semantics were uncertain (A4.4). Starts slow, speeds up
after clean streaks, halves the rate on 429/405 and holds a 60 s firewall cooldown. Learned rates can be
persisted by the caller.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


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
        self.debt = 0.0  # post-flight charges (Arcus list endpoints) can drive the bucket negative

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
    """Counts events in a rolling window (e.g. Lighter '60 requests per rolling minute')."""

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


@dataclass
class AdaptiveThrottle:
    """Per-host interval controller. `min_interval_s` starts conservative (1 call / 10 s per A4.4)."""

    interval_s: float = 10.0
    floor_s: float = 1.0
    ceiling_s: float = 60.0
    speedup_after: int = 30
    cooldown_s: float = 60.0
    _last: float = 0.0
    _ok_streak: int = 0
    _blocked_until: float = 0.0
    limit_events: list[tuple[float, int]] = field(default_factory=list)

    async def wait(self) -> None:
        now = time.monotonic()
        t = max(self._last + self.interval_s, self._blocked_until)
        if t > now:
            await asyncio.sleep(t - now)
        self._last = time.monotonic()

    def on_ok(self) -> None:
        self._ok_streak += 1
        if self._ok_streak >= self.speedup_after and self.interval_s > self.floor_s:
            self.interval_s = max(self.floor_s, self.interval_s * 0.8)
            self._ok_streak = 0

    def on_limited(self, status: int) -> None:
        self._ok_streak = 0
        self.interval_s = min(self.ceiling_s, self.interval_s * 2)
        self._blocked_until = time.monotonic() + self.cooldown_s
        self.limit_events.append((time.time(), status))
