"""Trading calendar: macro event windows (CPI / FOMC / NFP +/- 30 min), single-stock earnings and ex-dividend
(+/- 24 h), NYSE holidays, session labels, Arcus RTH boundaries (04:00, 09:30, 16:00, 20:00 ET) and optional
IST time-slot windows from the owner's research.

Coverage matters: an empty calendar silently allows trading through CPI. `coverage_days()` feeds an alert.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from bot.common.time import KOLKATA, NEW_YORK, US_PER_S, dt_to_us, us_to_dt

MACRO_WINDOW_S = 30 * 60
SINGLE_STOCK_WINDOW_S = 24 * 3600
EQUITY_CATEGORIES = {"EQUITIES", "INDICES", "COMMODITIES", "FOREX"}
ARCUS_BOUNDARIES_ET = (time(4, 0), time(9, 30), time(16, 0), time(20, 0))


@dataclass(frozen=True, slots=True)
class Event:
    ts_us: int
    kind: str
    symbol: str | None = None
    provisional: bool = False


@dataclass
class TradingCalendar:
    events: list[Event] = field(default_factory=list)
    holidays: dict[date, str | None] = field(default_factory=dict)  # date -> early close "HH:MM" or None

    @classmethod
    def load(cls, root: Path | str = "config/calendars") -> TradingCalendar:
        root = Path(root)
        cal = cls()
        p = root / "events.csv"
        if p.exists():
            for r in csv.DictReader(p.open()):
                ts = datetime.strptime(r["ts_et"], "%Y-%m-%d %H:%M").replace(tzinfo=NEW_YORK)
                cal.events.append(Event(dt_to_us(ts), r["kind"].lower(), None, r.get("provisional", "") == "true"))
        for fname, kind in (("earnings.csv", "earnings"), ("exdiv.csv", "ex_dividend")):
            p = root / fname
            if not p.exists():
                continue
            for r in csv.DictReader(p.open()):
                d = r.get("date") or r.get("ex_date")
                if not d:
                    continue
                hh = 16 if r.get("session", "").lower() == "amc" else 9
                ts = datetime.strptime(d, "%Y-%m-%d").replace(hour=hh, minute=30 if hh == 9 else 0, tzinfo=NEW_YORK)
                cal.events.append(Event(dt_to_us(ts), kind, r["symbol"].upper()))
        p = root / "nyse_holidays.csv"
        if p.exists():
            for r in csv.DictReader(p.open()):
                cal.holidays[date.fromisoformat(r["date"])] = r.get("early_close_et") or None
        cal.events.sort(key=lambda e: e.ts_us)
        return cal

    # ---------------------------------------------------------------- windows
    def active_events(self, ts_us: int, symbol: str | None = None, skip: set[str] | None = None) -> list[Event]:
        out = []
        for e in self.events:
            if skip is not None and e.kind not in skip:
                continue
            w = SINGLE_STOCK_WINDOW_S if e.symbol else MACRO_WINDOW_S
            if abs(ts_us - e.ts_us) <= w * US_PER_S and (e.symbol is None or e.symbol == (symbol or "").upper()):
                out.append(e)
        return out

    def in_event_window(self, ts_us: int, symbol: str | None = None, skip: set[str] | None = None) -> bool:
        return bool(self.active_events(ts_us, symbol, skip))

    def next_event(self, ts_us: int, kinds: set[str] | None = None, symbol: str | None = None) -> Event | None:
        for e in self.events:
            if e.ts_us > ts_us and (kinds is None or e.kind in kinds) and (e.symbol is None or e.symbol == symbol):
                return e
        return None

    def coverage_days(self, ts_us: int, kind: str) -> float:
        last = max((e.ts_us for e in self.events if e.kind == kind), default=0)
        return max(0.0, (last - ts_us) / (86400 * US_PER_S))

    # ---------------------------------------------------------------- sessions
    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and (d not in self.holidays or self.holidays[d] is not None)

    def session_label(self, ts_us: int) -> str:
        """rth (09:30-16:00 ET), pre (04:00-09:30), post (16:00-20:00), overnight (20:00-04:00 on weekdays),
        weekend (Sat, Sun, and holidays)."""
        et = us_to_dt(ts_us).astimezone(NEW_YORK)
        d, t = et.date(), et.time()
        if not self.is_trading_day(d):
            # Sunday after 20:00 ET behaves like Monday overnight.
            if et.weekday() == 6 and t >= time(20, 0):
                return "overnight"
            return "weekend"
        close = time(16, 0)
        early = self.holidays.get(d)
        if early:
            hh, mm = map(int, early.split(":"))
            close = time(hh, mm)
        if time(9, 30) <= t < close:
            return "rth"
        if time(4, 0) <= t < time(9, 30):
            return "pre"
        if close <= t < time(20, 0):
            return "post"
        if et.weekday() == 4 and t >= time(20, 0):
            return "weekend"
        return "overnight"

    def arcus_boundary_near(self, ts_us: int, within_s: int = 300) -> bool:
        et = us_to_dt(ts_us).astimezone(NEW_YORK)
        for b in ARCUS_BOUNDARIES_ET:
            bd = et.replace(hour=b.hour, minute=b.minute, second=0, microsecond=0)
            if abs((et - bd).total_seconds()) <= within_s:
                return True
        return False

    def next_arcus_boundary_us(self, ts_us: int) -> int:
        et = us_to_dt(ts_us).astimezone(NEW_YORK)
        for add in range(0, 8):
            d = (et + timedelta(days=add)).date()
            for b in ARCUS_BOUNDARIES_ET:
                cand = datetime.combine(d, b, tzinfo=NEW_YORK)
                if cand > et:
                    return dt_to_us(cand)
        raise RuntimeError("no boundary within a week")


def in_ist_windows(ts_us: int, windows: list[str]) -> bool:
    """True when `windows` is empty (no restriction) or ts falls in any 'HH:MM-HH:MM' IST window (wraps midnight)."""
    if not windows:
        return True
    t = us_to_dt(ts_us).astimezone(KOLKATA).time()
    for w in windows:
        a, _, b = w.partition("-")
        ah, am = (int(x) for x in a.split(":"))
        bh, bm = (int(x) for x in b.split(":"))
        ta, tb = time(ah, am), time(bh, bm)
        if (ta <= t < tb) if ta <= tb else (t >= ta or t < tb):
            return True
    return False
