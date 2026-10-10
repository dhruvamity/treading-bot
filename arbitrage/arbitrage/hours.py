"""The stock market's week as Arcus sees it: its stocks, indices and commodities have "regular hours" (Monday to Friday,
04:00 to 20:00 New York), and outside them Arcus asks more margin, fixes the funding and bands the price."""

from __future__ import annotations

import datetime as dt
import zoneinfo

NEW_YORK = zoneinfo.ZoneInfo("America/New_York")


def open_hours(t: float) -> bool:
    """Arcus's regular hours for stocks and ETFs: Monday to Friday, 04:00 to 20:00 New York (holidays not known)."""
    d = dt.datetime.fromtimestamp(t, NEW_YORK)
    return d.weekday() < 5 and 4 <= d.hour < 20


def weekend(t: float) -> bool:
    """Friday 20:00 to Monday 04:00 New York: one Arcus session, with one price band."""
    d = dt.datetime.fromtimestamp(t, NEW_YORK)
    return d.weekday() >= 5 or (d.weekday() == 4 and d.hour >= 20) or (d.weekday() == 0 and d.hour < 4)
