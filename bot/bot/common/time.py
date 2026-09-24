"""One clock for the whole system.

Internal time is int64 microseconds UTC (prompt pack A7). Venue boundaries convert here and nowhere else:
Arcus signing timestamps are ns, Arcus query from/to are µs, Lighter REST timestamps are s (some ms),
Lighter nonces/expiries are ms, Lighter `transaction_time` is µs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

US_PER_S = 1_000_000
US_PER_MS = 1_000
NS_PER_US = 1_000

NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")


class Clock:
    """Injectable clock. Tests and the simulator replace `now_us`; everything else reads through it."""

    def __init__(self, now_fn: Callable[[], int] | None = None) -> None:
        self._now_fn = now_fn or (lambda: time.time_ns() // NS_PER_US)

    def now_us(self) -> int:
        return self._now_fn()

    def now_ms(self) -> int:
        return self.now_us() // US_PER_MS

    def now_s(self) -> int:
        return self.now_us() // US_PER_S

    def now_ns(self) -> int:
        return self.now_us() * NS_PER_US


class FakeClock(Clock):
    def __init__(self, start_us: int = 1_790_000_000 * US_PER_S) -> None:
        self._t = start_us
        super().__init__(lambda: self._t)

    def set(self, t_us: int) -> None:
        self._t = t_us

    def advance(self, dt_us: int) -> None:
        self._t += dt_us


SYSTEM_CLOCK = Clock()


def now_us() -> int:
    return time.time_ns() // NS_PER_US


def monotonic_us() -> int:
    """For latency measurement only; never compare with wall-clock values."""
    return time.monotonic_ns() // NS_PER_US


def s_to_us(s: int | float) -> int:
    return int(s * US_PER_S)


def ms_to_us(ms: int) -> int:
    return int(ms) * US_PER_MS


def us_to_ms(us: int) -> int:
    return us // US_PER_MS


def us_to_s(us: int) -> int:
    return us // US_PER_S


def us_to_ns(us: int) -> int:
    return us * NS_PER_US


def ns_to_us(ns: int) -> int:
    return ns // NS_PER_US


def us_to_dt(us: int) -> datetime:
    return datetime.fromtimestamp(us / US_PER_S, tz=UTC)


def dt_to_us(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; attach a timezone")
    return int(dt.timestamp() * US_PER_S)


def utc_date_str(us: int) -> str:
    return us_to_dt(us).strftime("%Y-%m-%d")


def looks_like_unit(value: int) -> str:
    """Classify an epoch integer by magnitude (used by the data-quality report to catch unit bugs)."""
    v = abs(value)
    if v < 10**11:
        return "s"
    if v < 10**14:
        return "ms"
    if v < 10**17:
        return "us"
    return "ns"


def hour_floor_us(us: int) -> int:
    return us - (us % (3600 * US_PER_S))


def next_hour_us(us: int) -> int:
    return hour_floor_us(us) + 3600 * US_PER_S


def ntp_offset_us(server: str = "pool.ntp.org", timeout: float = 2.0) -> int | None:
    """Local clock minus NTP time, in µs. None when NTP is unreachable (logged by the caller)."""
    try:
        import ntplib  # local import: optional at runtime

        resp = ntplib.NTPClient().request(server, version=3, timeout=timeout)
        return int(-resp.offset * US_PER_S)
    except Exception:
        return None
