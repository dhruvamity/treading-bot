"""Trading sessions of the week, by the local clocks of Tokyo, London and New York (so they follow each city's
daylight-saving changes). The autopilot and its playbook (arcus/scout/autopilot.py, playbook.py) judge every setup per
session, since the same setup costs different amounts at different times of the week (research note 2026-09-27).

- weekend:  New York Friday 17:00 (US futures close) to Sunday 18:00 (they reopen); traditional markets are closed
- asia:     Tokyo 09:00 to London 08:00
- london:   London 08:00 to New York 09:30
- us_open:  New York 09:30 to 12:00
- us_pm:    New York 12:00 to 16:00
- late:     New York 16:00 to Tokyo 09:00 (with Sunday evening, when the week starts)
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from arcus.common.time import KOLKATA, NEW_YORK

TOKYO = ZoneInfo("Asia/Tokyo")
LONDON = ZoneInfo("Europe/London")

SESSIONS = ("weekend", "asia", "london", "us_open", "us_pm", "late")
TITLES = {"weekend": "Weekend", "asia": "Asia", "london": "London", "us_open": "US open", "us_pm": "US afternoon",
          "late": "US evening"}


def _t(h: int, m: int = 0) -> dt.time:
    return dt.time(h, m)


def session_of(ts: float | dt.datetime) -> str:
    """The session a moment belongs to (ts: UNIX seconds, or an aware datetime)."""
    t = ts if isinstance(ts, dt.datetime) else dt.datetime.fromtimestamp(ts, dt.UTC)
    ny = t.astimezone(NEW_YORK)
    wd, tod = ny.weekday(), ny.time()
    if (wd == 4 and tod >= _t(17)) or wd == 5 or (wd == 6 and tod < _t(18)):
        return "weekend"
    if wd < 5 and _t(9, 30) <= tod < _t(12):
        return "us_open"
    if wd < 5 and _t(12) <= tod < _t(16):
        return "us_pm"
    # the rest belongs to the New York day that opens next: late until Tokyo opens, then Asia, then London
    day = ny.date() if tod < _t(9, 30) else ny.date() + dt.timedelta(days=1)
    if t >= dt.datetime.combine(day, _t(8), LONDON):
        return "london"
    if t >= dt.datetime.combine(day, _t(9), TOKYO):
        return "asia"
    return "late"


def next_change(ts: float, step_s: int = 300, horizon_s: int = 3 * 86400) -> tuple[float, str]:
    """When the session next changes after ts (to the step), and to which."""
    now = session_of(ts)
    t = ts - ts % step_s + step_s
    while t < ts + horizon_s:
        s = session_of(t)
        if s != now:
            return t, s
        t += step_s
    return ts + horizon_s, now


def spans(start: float, hours: float, step_s: int = 300) -> list[tuple[float, float, str]]:
    """[(from, to, session)] covering [start, start + hours)."""
    out: list[tuple[float, float, str]] = []
    end = start + hours * 3600
    t = start
    while t < end:
        nxt, _ = next_change(t, step_s)
        out.append((t, min(nxt, end), session_of(t)))
        t = nxt
    return out


def ist(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).astimezone(KOLKATA).strftime("%a %H:%M")


def utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%H:%M")
