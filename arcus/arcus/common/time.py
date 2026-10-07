"""One clock for the whole system.

Internal time is int64 microseconds UTC (prompt pack A7). Venue boundaries convert here and nowhere else:
Arcus signing timestamps are ns, Arcus query from/to and data timestamps are µs.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

US_PER_S = 1_000_000
NS_PER_US = 1_000

NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")


def now_us() -> int:
    return time.time_ns() // NS_PER_US


def monotonic_us() -> int:
    """For latency measurement only; never compare with wall-clock values."""
    return time.monotonic_ns() // NS_PER_US


def us_to_dt(us: int) -> datetime:
    return datetime.fromtimestamp(us / US_PER_S, tz=UTC)


def dt_to_us(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; attach a timezone")
    return int(dt.timestamp() * US_PER_S)


def utc_date_str(us: int) -> str:
    return us_to_dt(us).strftime("%Y-%m-%d")
