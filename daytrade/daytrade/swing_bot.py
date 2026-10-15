"""The daily SPY/QQQ rules that passed their two-year backtest (reports/swing/REPORT.md), paper-traded on Lighter's
prices. No order is sent.

Each rule decides once a day at 15:59 New York on that day's price then, as the minute-price backtest does: buy at
that price, sell at that price on a later day. The daily bars are built the same way every time, so a restart gives
the same answer:

- the days before Lighter's history: the ETF's daily bars from Yahoo (data/daily/yahoo_<sym>.csv; `daytrade fetch
  daily`), for the 200-day average and the rest of the warm-up;
- every New York trading day since: Lighter's own one-minute prices, 09:30 to 15:59 (high, low, and the close at
  15:59), once its 15:58 minute has closed.

The whole history is replayed through the backtest (swing.simulate_live) every time a new day is complete; trades
entered from the day the paper run began are the paper trades, and the position open at the end is what the rule
holds now. The paper bot (bot.py) calls `SwingMarket.update` every minute; it does real work only once a day.

`GoldMarket` does the same for gold's Asian-range breakout on hourly bars (swing_rules.AsianBreak, weekday entries):
Lighter's XAU minutes made into hours each time an hour completes, with Binance's gold perpetual (which Lighter's
gold follows at a 0.994 correlation) for the 50-day trend before Lighter's gold opened on 24 Aug 2026.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np

from . import STATE, WARMUP_DAYS, fetch
from . import bars as B
from . import swing as S
from .strategies import trading_day
from .swing_rules import AsianBreak, CumRsi, PercentB, Rsi2, RsiLevel, TripleRsi, ZScale

OPEN_S, DECIDE_S = 9 * 3600 + 1800, 15 * 3600 + 59 * 60
WARM_DAYS = 400                     # daily bars before the paper run's first day: the 200-day average and RSI


def default_rules() -> dict[str, list[S.Rule]]:
    """The main versions (fixed before any result) whose backtest made money after costs and funding on both venues
    over the last two years, in each of them (reports/swing/REPORT.md). SPY's Triple RSI did not (7 trades, -2 bp)."""
    return {"SPY": [Rsi2("SPY"), CumRsi("SPY"), RsiLevel("SPY", 2, 15, exit="high"), PercentB("SPY")],
            "QQQ": [ZScale("QQQ"), Rsi2("QQQ"), CumRsi("QQQ"), RsiLevel("QQQ", 2, 15, exit="high"), PercentB("QQQ"),
                    TripleRsi("QQQ")]}


def venue_days(b: B.Bars, now: int) -> list[tuple]:
    """(day, o, h, l, c) for each New York trading day in `b` whose 15:58 minute has closed by `now`: open at 09:30,
    high and low from 09:30 to 15:59, the close at 15:59 (the 15:58 minute's close)."""
    loc = B.local(b.t, B.NY)
    d, tod = loc // B.DAY, loc % B.DAY
    keep = (tod >= OPEN_S) & (tod < DECIDE_S) & trading_day(d)
    d, tod, t = d[keep], tod[keep], b.t[keep]
    o, h, lo, c = b.o[keep], b.h[keep], b.l[keep], b.c[keep]
    out = []
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    for a, z in zip(starts.tolist(), np.r_[starts[1:], len(d)].tolist(), strict=True):
        if tod[a] != OPEN_S or tod[z - 1] != DECIDE_S - 60 or int(t[z - 1]) + 60 > now:
            continue                                   # the day must run from 09:30 to its 15:58 minute, closed
        out.append((int(d[a]), float(o[a]), float(h[a:z].max()), float(lo[a:z].min()), float(c[z - 1])))
    return out


def yahoo_days(sym: str) -> list[tuple]:
    path = S.DAILY / f"yahoo_{sym}.csv"
    if not path.exists():
        fetch.yahoo_daily(sym)
    out = []
    for r in list(csv.reader(path.open()))[1:]:
        day = (dt.date.fromisoformat(r[0]) - dt.date(1970, 1, 1)).days
        out.append((day, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
    return out


def daily_bars(sym: str, venue_rows: list[tuple]) -> S.Coarse:
    """Yahoo's days before the venue's first day, then the venue's: one bar a day, each its own fine bar."""
    first = venue_rows[0][0] if venue_rows else 10**9
    rows = [r for r in yahoo_days(sym) if r[0] < first] + venue_rows
    day = np.array([r[0] for r in rows], dtype=np.int64)
    a = np.array([r[1:] for r in rows], dtype=np.float64)
    t_o = np.array([B.local_to_utc(int(x), OPEN_S, B.NY) for x in day], dtype=np.int64)
    t_c = np.array([B.local_to_utc(int(x), DECIDE_S, B.NY) for x in day], dtype=np.int64)
    fine = B.Bars(t_o, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.zeros(len(day)))
    idx = np.arange(len(day), dtype=np.int64)
    return S.Coarse(f"paper_{sym}", t_o, t_c, a[:, 0], a[:, 1], a[:, 2], a[:, 3], day, idx, idx + 1, fine, B.NY)


class SwingMarket:
    """The daily rules of one market on Lighter. `update` brings Lighter's prices up to date when a day may have
    completed (around 15:59 New York, and at the first call), replays every rule, and returns its status and the
    trades closed since the paper run began."""

    def __init__(self, sym: str, rules: list[S.Rule], state_dir: Path = STATE, mode: str = "paper"):
        self.sym, self.rules, self.dir, self.mode = sym, rules, state_dir, mode
        self.venue = S.VENUES[sym]["lighter"]
        self.start_path = state_dir / f"{mode}_swing_start.json"
        self.last_day: int | None = None
        self.cache: tuple[dict, list[dict]] = ({}, [])

    def key(self, rule: S.Rule) -> str:
        return f"{self.sym.lower()}-{rule.name}@lighter"

    def start_day(self, now: int) -> int:
        """The paper run's first day, written the first time (state/paper_swing_start.json): today, or the next day
        when today's 15:59 has passed (no decision is taken on a price that has gone by)."""
        d = json.loads(self.start_path.read_text()) if self.start_path.exists() else {}
        if self.sym not in d:
            loc = int(B.local(np.array([now]), B.NY)[0])
            d[self.sym] = B.day_str(loc // B.DAY + (1 if loc % B.DAY >= DECIDE_S else 0))
            self.dir.mkdir(parents=True, exist_ok=True)
            self.start_path.write_text(json.dumps(d, indent=1))
        return (dt.date.fromisoformat(d[self.sym]) - dt.date(1970, 1, 1)).days

    def due(self, now: int) -> bool:
        tod = int(B.local(np.array([now]), B.NY)[0]) % B.DAY
        return self.last_day is None or DECIDE_S <= tod < DECIDE_S + 30 * 60

    def update(self, now: int | None = None, fetch_prices: bool = True) -> tuple[dict, list[dict]]:
        now = int(time.time()) if now is None else now
        if not self.due(now):
            return self.cache
        if fetch_prices:
            fetch.lighter(self.sym)
        rows = venue_days(B.load(f"lighter_{self.sym}"), now)
        if rows and rows[-1][0] == self.last_day:
            return self.cache
        first = self.start_day(now)
        cb = daily_bars(self.sym, rows)
        i0 = int(np.searchsorted(cb.day, first))
        lo = max(0, i0 - WARM_DAYS)
        cb = _slice(cb, lo)
        status, closed = {}, []
        for rule in self.rules:
            trades, pos = S.simulate_live(rule, cb, self.venue, S.Funding(self.venue), start=i0 - lo)
            k = self.key(rule)
            closed += [trade_row(k, x) for x in trades]
            if pos is None:
                state = "flat"
            else:
                px = float(cb.c[-1])
                gain = pos.side * (px / pos.avg - 1) * 1e4
                state = (f"LONG {pos.frac:.0%} of full size from {pos.avg:.2f} since "
                         f"{B.day_str(int(B.local(np.array([pos.legs[0][0]]), B.NY)[0]) // B.DAY)}, "
                         f"{gain:+.0f} bp at {px:.2f}")
            last = (f"{B.day_str(int(cb.day[-1]))} {time.strftime('%H:%M', time.gmtime(int(cb.t_c[-1])))}"
                    if len(cb) else "-")                     # the last day's 15:59 New York, in UTC
            status[k] = {"state": state, "day": B.day_str(int(cb.day[-1])) if len(cb) else "-",
                         "last_bar_utc": last, "trades_since_start": len(trades)}
        self.last_day = rows[-1][0] if rows else None
        self.cache = (status, closed)
        return self.cache


def _slice(cb: S.Coarse, lo: int) -> S.Coarse:
    """The bars from `lo` on (each its own fine bar, so the fine bars are cut the same way)."""
    f = cb.fine
    fine = B.Bars(f.t[lo:], f.o[lo:], f.h[lo:], f.l[lo:], f.c[lo:], f.v[lo:])
    idx = np.arange(len(cb) - lo, dtype=np.int64)
    return S.Coarse(cb.name, cb.t_o[lo:], cb.t_c[lo:], cb.o[lo:], cb.h[lo:], cb.l[lo:], cb.c[lo:], cb.day[lo:], idx,
                    idx + 1, fine, cb.tz)


def trade_row(slot: str, x: S.STrade, tz=B.NY) -> dict:
    """One paper trade as bot.py writes it. A rule with a stop also gets R (the result over the stop's distance)."""
    f = lambda s: time.strftime("%Y-%m-%d %H:%M", time.gmtime(s))       # noqa: E731
    sd = x.stop_dist if x.stop_dist and x.stop_dist == x.stop_dist else 0.0
    return {"slot": slot, "day": B.day_str(int(B.local(np.array([x.t_in]), tz)[0]) // B.DAY),
            "side": "long" if x.side > 0 else "short", "tag": x.tag, "entry_utc": f(x.t_in), "entry": round(x.entry, 4),
            "stop": None, "target": None, "exit_utc": f(x.t_out), "exit": round(x.exit, 4), "reason": x.reason,
            "r": round(x.net / sd, 4) if sd else 0.0, "r_gross": round(x.gross / sd, 4) if sd else 0.0,
            "stop_bp": round(sd * 1e4, 2), "bp": round(x.net * 1e4, 2), "share": round(x.frac, 3),
            "funding_bp": round(x.fund * 1e4, 2), "days": round(x.hours / 24, 1)}


# ---- gold, hourly ----------------------------------------------------------------------------------------------------

GOLD_WARM_DAYS = 120                # hours before the paper run's first hour: the 50-day trend of weekday closes


class GoldMarket:
    """Gold's Asian-range breakout on Lighter's XAU. `update` makes hourly bars each time an hour completes (only
    complete hours) and replays the rule from the paper run's first hour; same interface as SwingMarket."""

    sym = "XAU"
    reference = "binance_XAUUSDT_perp"          # the hours before Lighter's first minute

    def __init__(self, rules: list[S.Rule] | None = None, state_dir: Path = STATE, mode: str = "paper"):
        self.rules = rules or [AsianBreak(days="weekdays")]
        self.dir = state_dir
        self.venue = S.VENUES["XAU"]["lighter"]
        self.start_path = state_dir / f"{mode}_swing_start.json"
        self.last_hour: int | None = None
        self.checked_reference = False
        self.cache: tuple[dict, list[dict]] = ({}, [])

    def key(self, rule: S.Rule) -> str:
        return f"xau-{rule.name}@lighter"

    def start_hour(self, now: int) -> int:
        """The paper run's first hour (UTC, state/paper_swing_start.json, key "XAU"): the hour under way when the run
        began. It is decided at its close, which is still ahead, so no decision is taken on a price gone by."""
        d = json.loads(self.start_path.read_text()) if self.start_path.exists() else {}
        if self.sym not in d:
            d[self.sym] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(now // 3600 * 3600))
            self.dir.mkdir(parents=True, exist_ok=True)
            self.start_path.write_text(json.dumps(d, indent=1))
        return int(dt.datetime.fromisoformat(d[self.sym]).replace(tzinfo=dt.UTC).timestamp())

    def hours(self, now: int, start: int) -> B.Bars:
        """Hourly bars of the complete hours before `now`: Lighter's minutes, and the reference's before them, from
        GOLD_WARM_DAYS before `start` (never more than the two years plus warm-up the backtests may read)."""
        end = now // 3600 * 3600
        lo = max(start - GOLD_WARM_DAYS * B.DAY, end - (730 + WARMUP_DAYS) * B.DAY)
        ven = B.load("lighter_XAU").between(lo, end)
        first = int(ven.t[0]) if len(ven) else end
        ref = B.load(self.reference).between(lo, first)
        both = B.concat(ref, ven)
        return B.resample(both, 60)[0] if len(both) else both

    def ensure_reference(self) -> None:
        """A new server has no gold perpetual history: fetch it once (Binance's public monthly files, about 25
        requests), else the 50-day trend waits for 50 weekdays of Lighter's own gold."""
        if self.checked_reference:
            return
        self.checked_reference = True
        ven = B.load("lighter_XAU")
        ref = B.load(self.reference)
        if len(ven) and (not len(ref) or int(ref.t[-1]) < int(ven.t[0])):
            fetch.binance("XAUUSDT", "futures", "2025-11")

    def update(self, now: int | None = None, fetch_prices: bool = True) -> tuple[dict, list[dict]]:
        now = int(time.time()) if now is None else now
        if now // 3600 == self.last_hour:
            return self.cache
        if fetch_prices:
            fetch.lighter(self.sym)
            self.ensure_reference()
        start = self.start_hour(now)
        h = self.hours(now, start)
        if not len(h):
            return self.cache
        cb = S.hourly(h, "paper_XAU")
        i0 = int(np.searchsorted(cb.t_o, start))
        status, closed = {}, []
        last = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(cb.t_c[-1])))
        for rule in self.rules:
            trades, pos = S.simulate_live(rule, cb, self.venue, S.Funding(self.venue), start=i0)
            k = self.key(rule)
            closed += [trade_row(k, x, B.LONDON) for x in trades]
            if pos is None:
                state = "flat"
            else:
                px = float(cb.c[-1])
                gain = pos.side * (px / pos.avg - 1) * 1e4
                state = (f"{'LONG' if pos.side > 0 else 'SHORT'} from {pos.avg:.2f} stop {pos.stop:.2f} since "
                         f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(pos.legs[0][0]))} UTC, {gain:+.0f} bp at "
                         f"{px:.2f}, out after {getattr(rule, 'hold', '?')} hours")
            status[k] = {"state": state, "day": B.day_str(int(cb.day[-1])), "last_bar_utc": last,
                         "trades_since_start": len(trades)}
        self.last_hour = now // 3600
        self.cache = (status, closed)
        return self.cache


def default_markets(state_dir: Path = STATE) -> list:
    """The daily SPY and QQQ rules and the hourly gold rule, as the paper bot runs them."""
    return [*(SwingMarket(sym, rules, state_dir) for sym, rules in default_rules().items()),
            GoldMarket(state_dir=state_dir)]

