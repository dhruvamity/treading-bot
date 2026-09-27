"""When not to trade: US macro releases (CPI, the jobs report, FOMC) for every market, and a stock's earnings for its
perp.

- config/calendars/events.csv: ts_et (New York time), kind, source, provisional. No new runs from 45 minutes before a
  release to 30 minutes after; a run going is closed 15 minutes before.
- Earnings: fetched daily from Nasdaq's public calendar for the next 21 days (state/calendars/earnings.csv). A stock's
  perp is left alone from 24 hours before its report to 24 hours after (reports move a stock several percent within a
  minute; at 20x that is a liquidation).
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import time
from pathlib import Path

import aiohttp

from lbot.config import Config
from lbot.log import Log

log = Log("calendar")
NY = __import__("zoneinfo").ZoneInfo("America/New_York")
URL = "https://api.nasdaq.com/api/calendar/earnings?date={day}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
           "Accept": "application/json"}
BEFORE_S, AFTER_S, CLOSE_S = 45 * 60, 30 * 60, 15 * 60
EARN_S = 24 * 3600


def events(cfg: Config) -> list[tuple[float, str]]:
    """(unix time, kind) of each release."""
    out = []
    p = cfg.root / "config" / "calendars" / "events.csv"
    try:
        with open(p) as fh:
            for r in csv.DictReader(fh):
                t = dt.datetime.strptime(r["ts_et"], "%Y-%m-%d %H:%M").replace(tzinfo=NY)
                out.append((t.timestamp(), r["kind"]))
    except (OSError, KeyError, ValueError):
        pass
    return out


def event_block(cfg: Config, now: float, *, closing: bool = False) -> str | None:
    """Why not to start now (or, with closing, why to close a run now), or None."""
    for t, kind in events(cfg):
        lo = t - (CLOSE_S if closing else BEFORE_S)
        if lo <= now <= t + AFTER_S:
            when = time.strftime("%H:%M UTC", time.gmtime(t))
            return f"{kind.upper()} at {when}"
    return None


def earnings_path(cfg: Config) -> Path:
    return cfg.state_dir / "calendars" / "earnings.csv"


def earnings(cfg: Config) -> dict[str, float]:
    """symbol -> report time (unix; before the open 09:00 NY, after the close 16:30 NY, unknown 12:00 NY)."""
    out: dict[str, float] = {}
    try:
        with open(earnings_path(cfg)) as fh:
            for r in csv.DictReader(fh):
                out.setdefault(r["symbol"], float(r["t"]))
    except (OSError, KeyError, ValueError):
        pass
    return out


def earnings_block(cfg: Config, market: str, now: float) -> str | None:
    t = earnings(cfg).get(market)
    if t is not None and t - EARN_S <= now <= t + EARN_S:
        return f"{market} reports earnings {time.strftime('%a %d %b %H:%M UTC', time.gmtime(t))}"
    return None


async def fetch_earnings(cfg: Config, symbols: set[str], days: int = 21) -> int:
    """Nasdaq's earnings calendar for the next `days` weekdays, for `symbols`. Returns rows kept."""
    rows: list[dict[str, str]] = []
    today = dt.datetime.now(dt.UTC).date()
    async with aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as s:
        for i in range(days + 1):
            d = today + dt.timedelta(days=i)
            if d.weekday() >= 5:
                continue
            try:
                async with s.get(URL.format(day=d.isoformat())) as r:
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                log.warn("earnings_fetch_failed", day=str(d), err=str(e))
                continue
            for row in ((data or {}).get("data") or {}).get("rows") or []:
                sym = str(row.get("symbol", "")).upper()
                if sym not in symbols:
                    continue
                hm = {"time-pre-market": (9, 0), "time-after-hours": (16, 30)}.get(str(row.get("time")), (12, 0))
                t = dt.datetime(d.year, d.month, d.day, *hm, tzinfo=NY).timestamp()
                rows.append({"symbol": sym, "t": str(t), "day": d.isoformat(), "session": str(row.get("time"))})
            await asyncio.sleep(0.3)
    await asyncio.to_thread(_write, earnings_path(cfg), rows)
    return len(rows)


def _write(p: Path, rows: list[dict[str, str]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "t", "day", "session"])
        w.writeheader()
        w.writerows(rows)
