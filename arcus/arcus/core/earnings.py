"""Earnings dates for the stock perps, fetched once a day from Nasdaq's public earnings calendar (no key).

Research (2026-09-27): on the stock perps an earnings day moves 2.5x a normal day (median over 18 reports, July to
September), with one-minute jumps of 3-6x the usual; AMD moved 9% in one minute on 2026-08-04, a liquidation at 20x.
The bot stays flat on a stock from 24 h before its report to 24 h after (arcus/core/calendar.py), and the autopilot
leaves it out; config/calendars/earnings.csv only held what the owner typed, and was empty.

Writes state/calendars/earnings.csv (symbol,date,session,source), the next DAYS_AHEAD days plus the last KEEP_DAYS,
which TradingCalendar.load reads alongside config/calendars/earnings.csv. Session: bmo (before the open), amc (after the
close) or "" when Nasdaq does not say, which the calendar treats as after the close (its window then also covers a
report before the next open).
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

from arcus.common.logging import Log

log = Log("earnings")
URL = "https://api.nasdaq.com/api/calendar/earnings?date={day}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36", "Accept": "application/json"}
DAYS_AHEAD = 21
KEEP_DAYS = 45
FILE = "earnings.csv"
SESSIONS = {"time-pre-market": "bmo", "time-after-hours": "amc"}
PAUSE_S = 0.3   # between requests: one a day per weekday ahead, gently


def auto_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "calendars" / FILE


def stock_symbols(markets_json: Path) -> set[str]:
    """The underlying tickers of the Arcus equity perps (category EQUITIES; ETFs have no earnings but are cheap to
    ask about)."""
    try:
        ms = json.loads(markets_json.read_text()).get("markets") or []
    except (OSError, ValueError):
        return set()
    out = set()
    for m in ms:
        if str(m.get("category") or "").upper() == "EQUITIES" and m.get("status") == "ONLINE":
            out.add(str(m.get("marketDisplayName") or "").split("-")[0].upper())
    return {s for s in out if s}


def rows_of(payload: dict[str, Any], day: str, symbols: set[str]) -> list[dict[str, str]]:
    rows = ((payload or {}).get("data") or {}).get("rows") or []
    return [{"symbol": r["symbol"].upper(), "date": day, "session": SESSIONS.get(str(r.get("time")), ""),
             "source": "nasdaq.com earnings calendar"} for r in rows
            if isinstance(r, dict) and str(r.get("symbol") or "").upper() in symbols]


async def fetch(symbols: set[str], start: dt.date, days: int = DAYS_AHEAD,
                session: aiohttp.ClientSession | None = None) -> tuple[list[dict[str, str]], int]:
    """(rows, days that failed) for the weekdays in [start, start + days)."""
    own = session is None
    s = session or aiohttp.ClientSession(headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20))
    out: list[dict[str, str]] = []
    failed = 0
    try:
        for i in range(days):
            d = start + dt.timedelta(days=i)
            if d.weekday() >= 5:
                continue
            try:
                async with s.get(URL.format(day=d.isoformat())) as r:
                    r.raise_for_status()
                    out += rows_of(await r.json(content_type=None), d.isoformat(), symbols)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                failed += 1
                log.warning("earnings_fetch_failed", reason=f"{d}: {type(e).__name__}")
            await asyncio.sleep(PAUSE_S)
    finally:
        if own:
            await s.close()
    return out, failed


def merge(old: list[dict[str, str]], new: list[dict[str, str]], start: dt.date, days: int = DAYS_AHEAD,
          keep_days: int = KEEP_DAYS) -> list[dict[str, str]]:
    """The fetched window replaces what the file had for it; older rows are kept for KEEP_DAYS."""
    end = start + dt.timedelta(days=days)
    cut = start - dt.timedelta(days=keep_days)
    kept = [r for r in old if cut.isoformat() <= r["date"] < start.isoformat() or r["date"] >= end.isoformat()]
    seen: set[tuple[str, str]] = set()
    out = []
    for r in sorted(kept + new, key=lambda r: (r["date"], r["symbol"])):
        k = (r["symbol"], r["date"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def read(p: Path) -> list[dict[str, str]]:
    try:
        with p.open() as f:
            return [dict(r) for r in csv.DictReader(f)]
    except OSError:
        return []


def write(p: Path, rows: list[dict[str, str]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "date", "session", "source"])
        w.writeheader()
        w.writerows(rows)
    tmp.replace(p)


def age_h(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 3600
    except OSError:
        return float("inf")


async def refresh(state_dir: Path | str, markets_json: Path, *, today: dt.date | None = None,
                  session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
    """Fetch the coming DAYS_AHEAD days and update state/calendars/earnings.csv. Keeps the old file when every
    request failed (Nasdaq down): a stale calendar beats an empty one."""
    symbols = stock_symbols(markets_json)
    today = today or dt.datetime.now(dt.UTC).date()
    p = auto_path(state_dir)
    if not symbols:
        return {"ok": False, "why": "no equity markets in markets.json"}
    rows, failed = await fetch(symbols, today, session=session)
    asked = sum(1 for i in range(DAYS_AHEAD) if (today + dt.timedelta(days=i)).weekday() < 5)
    if failed >= asked:
        return {"ok": False, "why": "every request failed", "failed": failed}
    write(p, merge(read(p), rows, today))
    return {"ok": True, "found": len(rows), "failed": failed, "symbols": len(symbols)}
