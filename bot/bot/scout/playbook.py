"""The autopilot's playbook: what each setup costs per session of the week and per regime, from hourly backtests.

Every hour of every recorded day (the last DAYS days) is backtested from flat for each playbook setup on the
candidate markets: the scout's simulator (bot/scout/sim.py), at the scan's sizes for the capital at each market's
maximum leverage, with the owner's position stop and no daily stop (the cost of the hour itself). Each hour is
labelled with its session (bot/scout/sessions.py) and the regime of the hour before it (bot/scout/regime.py); hours
inside an event window (CPI, NFP, FOMC, a stock's earnings: bot/core/calendar.py) are left out, since the autopilot
never trades them. Results are cached per market-day; a new day is added once it is complete.

The table (data/scout/playbook.json) gives, per market, setup, session and regime, the volume per hour and the cost per
dollar traded, shrunk towards the same setup's session-wide and overall numbers where a cell has few hours.

BTC and SPY days recorded before the scout kept their order book (their trades go back to June) use a book rebuilt
from trades: one tick around the last print. Checked against recorded books (2026-09-27): BTC Mid 0 within 1% of the
volume at 1.86 vs 1.68 bp (0.99 hourly correlation), SPY Mid 0 1.41 vs 1.18 bp; it overstates the cost of SPY's wider
spreads on weekends (Mid +1 1.58 vs 0.65 bp), which only makes those setups look worse than they are. Other markets
use recorded books only.
"""

from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from bot.common.sizing import Pct, bucket
from bot.core.calendar import MACRO_WINDOW_S, SINGLE_STOCK_WINDOW_S, TradingCalendar
from bot.scout import regime as rg
from bot.scout import sessions
from bot.scout.scan import (
    ALIVE_MARKET,
    SIM_VERSION,
    Scanner,
    ScanStopped,
    _gather,
    _halt,
    config_for,
    load_holidays,
    load_markets,
    lower_priority,
    market_meta,
    risk_key,
    session_mask,
)
from bot.scout.sim import MarketInfo, Risk, Sim, SimParams, Window
from bot.scout.tape import US_DAY, DayTape, TapeStore, day_start_us, day_str

S = 1_000_000
HOUR = 3600 * S
VERSION = f"{SIM_VERSION}.1"
SETUPS = ("Mid 0", "Mid +1", "Mid +2", "Mid +3", "Grid 0", "Grid +1", "Grid +3")
MARKETS = ("BTC-USD", "SPY-USD", "ETH-USD", "SOL-USD", "QQQ-USD", "NVDA-USD", "GLD-USD", "HYPE-USD")
SYNTH = ("BTC-USD", "SPY-USD")   # a book rebuilt from trades stands in for days without a recorded one
DAYS = 42
MIN_HOURS = 20          # a day counts once its data covers this many hours
SHRINK_H = 8.0          # pseudo-hours of the parent estimate mixed into each cell


def synth_book(tape: DayTape, tick: float, size: float) -> DayTape:
    """One-tick book around the last trade: a taker buy at p -> ask p, bid p - tick; a sell -> bid p, ask p + tick.
    Set 1 µs after the last print of each timestamp, so a print is judged against the book before it."""
    t = tape.trades
    if not len(t["ts"]):
        return tape
    ts = t["ts"]
    last = np.concatenate([ts[1:] != ts[:-1], [True]])
    px, buy = t["px"][last], t["buy"][last]
    n = int(last.sum())
    bbo = {"ts": ts[last] + 1, "bid": np.where(buy, px - tick, px), "ask": np.where(buy, px, px + tick),
           "bid_sz": np.full(n, size), "ask_sz": np.full(n, size)}
    return DayTape(tape.market, tape.day, bbo, t)


def _hours_covered(ts: np.ndarray, s0: int) -> float:
    if len(ts) < 2:
        return 0.0
    edges = np.concatenate([[s0], ts[(ts >= s0) & (ts < s0 + US_DAY)], [s0 + US_DAY]])
    gaps = np.diff(edges)
    return float(US_DAY - gaps[gaps > 10 * 60 * S].sum()) / HOUR


def _hours_traded(ts: np.ndarray, s0: int) -> int:
    """UTC hours of the day with at least one trade (a quiet book trades minutes apart: gaps are not outages)."""
    h = (ts[(ts >= s0) & (ts < s0 + US_DAY)] - s0) // HOUR
    return len(np.unique(h))


def _day_job(args: tuple[Any, ...]) -> list[list[Any]]:
    """One market-day: [[hour, setup, volume, pnl, session, regime, ratio], ...]; hours in `blocked` are skipped."""
    root, market, day, mi, rth, risk, synth, touch, usual_rv, blocked = args
    store = TapeStore(root)
    s0 = day_start_us(day)
    tape = store.load_range(market, s0 - 3 * HOUR, s0 + US_DAY)
    if synth:
        tape = synth_book(tape, mi["tick"], touch)
        alive = tape.bbo["ts"]
    else:
        alive = store.load_range(ALIVE_MARKET, s0 - 3 * HOUR, s0 + US_DAY).bbo["ts"]
    mids = rg.minute_mids(tape.bbo, tape.trades, s0 - 2 * HOUR, s0 + US_DAY)
    u = rg.Usual({(bool(k[0]), int(k[1])): v for k, v in usual_rv}, day)
    info = MarketInfo(**{**mi, "mmf": 0.0})
    r = Risk(**risk)
    holidays = load_holidays(full_only=True)
    out: list[list[Any]] = []
    for h in range(24):
        a = s0 + h * HOUR
        if any(x < a + HOUR and a < y for x, y in blocked):
            continue
        k = 120 + h * 60                        # mids[k] is the mid at the start of hour h
        before = rg.of(market, mids[k - 60:k + 1], a, u.at(a - 30 * 60 * S))
        w = Window(tape, a, a + HOUR, alive_ts=alive, rth=session_mask(rth, load_holidays()), holidays=holidays)
        if not w.ok[-3600:].any():
            continue
        sess = sessions.session_of(a / S + 1800)
        ratio = round(before.ratio, 3) if math.isfinite(before.ratio) else None
        for name in SETUPS:
            res = Sim(config_for(name), r, info, SimParams(queue=True)).run(w)
            out.append([h, name, round(res.maker_usd + res.taker_usd, 2), round(res.pnl, 4), sess, before.bucket,
                        ratio])
    return out


class Playbook:
    def __init__(self, root: Path, *, capital: float = 100.0, pct: Pct | None = None,
                 lev_caps: dict[str, float] | None = None, markets: tuple[str, ...] = MARKETS,
                 calendar: TradingCalendar | None = None) -> None:
        self.root = root                      # data/scout
        self.capital = bucket(capital)
        self.pct = pct or Pct()
        self.scanner = Scanner(root, capital=self.capital, pct=self.pct, lev_caps=lev_caps or {})
        self.markets = markets
        self.calendar = calendar or TradingCalendar()
        self.path = root / "playbook.json"

    @property
    def store(self) -> TapeStore:
        return TapeStore(self.root / "tape")

    def risk(self, market: str, days: list[str]) -> dict[str, Any] | None:
        mis, meta = load_markets(self.root / "markets.json"), market_meta(self.root / "markets.json")
        if market not in mis or market not in meta:
            return None
        rs = self.scanner.risks_for(meta[market], mis[market], self.scanner.order_max(market, days))
        rs = [r for r in rs if r["used_usd"] >= r["min_capital_usd"]]
        if not rs:
            return None
        return asdict(replace(Risk(**rs[0]), daily_stop_usd=1e12, kill_usd=1e12))   # the hour's own cost, no day stop

    def days(self, market: str, now_us: int) -> list[tuple[str, bool]]:
        """(day, synthetic book?) for the last DAYS complete days with enough data."""
        today = day_str(now_us)
        out = []
        for d in self.store.days(market, "trades" if market in SYNTH else "bbo")[-DAYS - 1:]:
            if d >= today:
                continue
            tape = self.store.load_day(market, d)
            s0 = day_start_us(d)
            if _hours_covered(tape.bbo["ts"], s0) >= MIN_HOURS:
                out.append((d, False))
            elif market in SYNTH and _hours_traded(tape.trades["ts"], s0) >= MIN_HOURS:
                out.append((d, True))
        return out[-DAYS:]

    def cache_path(self, market: str, day: str, rkey: str) -> Path:
        return self.root / "playbook" / f"v{VERSION}" / rkey / market / f"{day}.json"

    def blocked(self, market: str, day: str) -> list[tuple[int, int]]:
        s0 = day_start_us(day)
        base = market.split("-")[0].upper()
        out = []
        for e in self.calendar.events:
            if e.kind not in ("cpi", "nfp", "fomc", "earnings") or (e.symbol and e.symbol != base):
                continue
            w = (SINGLE_STOCK_WINDOW_S if e.symbol else MACRO_WINDOW_S) * S
            if e.ts_us - w < s0 + US_DAY and s0 < e.ts_us + w:
                out.append((e.ts_us - w, e.ts_us + w))
        return out

    def build(self, now_us: int | None = None, *, workers: int = 2, stop: threading.Event | None = None,
              budget_s: float = 1800.0) -> dict[str, Any]:
        """Backtest the days not cached yet (most recent first, at most budget_s), then write the table."""
        now_us = now_us or time.time_ns() // 1000
        mis = load_markets(self.root / "markets.json")
        meta = market_meta(self.root / "markets.json")
        jobs, where = [], []
        have: dict[str, list[str]] = {}
        risks: dict[str, dict[str, Any]] = {}
        for m in self.markets:
            if m not in mis or m not in meta or not self.store.days(m):
                continue
            days = self.days(m, now_us)
            r = self.risk(m, [d for d, syn in days if not syn] or [d for d, _ in days])
            if r is None or not days:
                continue
            risks[m] = r
            rk = risk_key(r)
            have[m] = []
            touch = self._touch_size(m)
            for d, syn in days:
                if self.cache_path(m, d, rk).exists():
                    have[m].append(d)
                    continue
                u = rg.usual(self.store, m, day_start_us(d))
                jobs.append((str(self.store.root), m, d, asdict(mis[m]), meta[m].get("regularTradingHours"), r, syn,
                             touch, [((int(k[0]), k[1]), v) for k, v in u.rv.items()], self.blocked(m, d)))
                where.append((m, d))
        order = sorted(range(len(jobs)), key=lambda i: where[i][1], reverse=True)
        jobs, where = [jobs[i] for i in order], [where[i] for i in order]
        done = 0
        if jobs:
            ex = ProcessPoolExecutor(max_workers=max(1, workers), initializer=lower_priority)
            try:
                deadline = time.monotonic() + budget_s if budget_s > 0 else None
                for i, rows in _gather(ex, jobs, stop, fn=_day_job, deadline=deadline):
                    m, d = where[i]
                    cp = self.cache_path(m, d, risk_key(risks[m]))
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_text(json.dumps(rows))
                    have[m].append(d)
                    done += 1
            except ScanStopped:
                _halt(ex)
                raise
            ex.shutdown(wait=True)
        table = self.table({m: sorted(ds) for m, ds in have.items()}, risks)
        table.update({"ts": now_us / S, "capital": self.capital, "built_days": done, "left_days": len(jobs) - done})
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(table, default=str))
        tmp.replace(self.path)
        return table

    def _touch_size(self, market: str) -> float:
        """Median size at the best bid and ask on recorded days (the rebuilt book's queue)."""
        for d in reversed(self.store.days(market)):
            b = self.store.load_day(market, d).bbo
            if len(b["ts"]) > 1000:
                return float(np.median(np.concatenate([b["bid_sz"], b["ask_sz"]])))
        return 0.0

    def table(self, days: dict[str, list[str]], risks: dict[str, dict[str, Any]]) -> dict[str, Any]:
        markets: dict[str, Any] = {}
        by_day: dict[str, dict[str, list[list[Any]]]] = {}
        for m, ds in days.items():
            rk = risk_key(risks[m])
            rows: list[list[Any]] = []
            for d in ds:
                try:
                    r = json.loads(self.cache_path(m, d, rk).read_text())
                except (OSError, ValueError):
                    continue
                rows += r
                by_day.setdefault(m, {})[d] = r
            if rows:
                markets[m] = {"days": len(ds), "first": ds[0], "last": ds[-1], "leverage": risks[m]["leverage"],
                              "setups": aggregate(rows)}
        pb = {"version": VERSION, "markets": markets}
        pb["ceilings"] = tune(pb, by_day)
        return pb


def aggregate(rows: list[list[Any]]) -> dict[str, dict[str, Any]]:
    """{setup: {"all": cell, "<session>": cell, "<session>|<regime>": cell}}, cell = [hours, volume, pnl]."""
    out: dict[str, dict[str, Any]] = {}
    for _h, name, vol, pnl, sess, reg, _ratio in rows:
        e = out.setdefault(name, {})
        for k in ("all", sess, f"{sess}|{reg}"):
            c = e.setdefault(k, [0, 0.0, 0.0])
            c[0] += 1
            c[1] += vol
            c[2] += pnl
    return out


def estimate(cells: dict[str, Any], session: str, regime: str) -> tuple[float, float, int]:
    """(volume per hour, cost in bp, hours behind the cell) for one setup, shrunk towards its parents."""
    vph, cost, n_used = math.nan, math.nan, 0
    for key in ("all", session, f"{session}|{regime}"):
        c = cells.get(key)
        if not c or c[0] <= 0 or c[1] <= 0:
            continue
        n, v, p = c
        c_bp = -p / v * 1e4
        if math.isnan(vph):
            vph, cost = v / n, c_bp
        else:
            w = n / (n + SHRINK_H)
            vph, cost = w * v / n + (1 - w) * vph, w * c_bp + (1 - w) * cost
        n_used = n
    return vph, cost, n_used


def options(pb: dict[str, Any], market: str, session: str, regime: str) -> list[dict[str, Any]]:
    """Every playbook setup on one market in this session and regime, fastest first."""
    m = (pb.get("markets") or {}).get(market)
    if not m:
        return []
    out = []
    for name, cells in m["setups"].items():
        vph, cost, n = estimate(cells, session, regime)
        if math.isfinite(vph) and math.isfinite(cost):
            out.append({"market": market, "setup": name, "vol_h": vph, "cost_bp": cost, "hours": n,
                        "leverage": m.get("leverage")})
    return sorted(out, key=lambda o: -o["vol_h"])


def week_plan(pb: dict[str, Any], max_cost_bp: float, start: float | None = None, hours: float = 24.0,
              regime: str = "normal") -> list[dict[str, Any]]:
    """What the autopilot would run in each coming session at a usual (`regime`) market: the fastest setup within
    max_cost_bp across the playbook's markets (None: wait)."""
    start = start or time.time()
    out = []
    for a, b, sess in sessions.spans(start, hours):
        opts = [o for m in (pb.get("markets") or {}) for o in options(pb, m, sess, regime) if o["cost_bp"] <= max_cost_bp]
        best = max(opts, key=lambda o: o["vol_h"]) if opts else None
        out.append({"from": a, "to": b, "session": sess, "pick": best})
    return out


def load(root: Path) -> dict[str, Any]:
    try:
        d: dict[str, Any] = json.loads((root / "playbook.json").read_text())
        return d
    except (OSError, ValueError):
        return {}


def age_days(pb: dict[str, Any], now: float | None = None) -> float:
    return ((now or time.time()) - float(pb.get("ts") or 0)) / 86400


TUNE_DAYS = 28
TUNE_BUDGETS = (1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)
TUNE_CEILINGS = tuple(round(0.8 + 0.1 * i, 1) for i in range(23))   # 0.8 .. 3.0 bp
POT_DAYS_TUNE = 7.0


Hour = list[tuple[float, float, float, float]]   # (estimated vol/h, estimated bp, real vol, real pnl), fastest first


def hours_of(pb: dict[str, Any], by_day: dict[str, dict[str, list[list[Any]]]], days: list[str]) -> list[Hour]:
    """Every hour of `days` with each market x setup's estimate (that hour's session and regime) and real result."""
    est: dict[tuple[str, str, str, str], tuple[float, float]] = {}
    out: list[Hour] = []
    for d in days:
        for h in range(24):
            hour: Hour = []
            for m, per in by_day.items():
                for hh, name, vol, pnl, sess, reg, _r in per.get(d, []):
                    if hh != h:
                        continue
                    k = (m, name, sess, reg)
                    if k not in est:
                        cells = ((pb.get("markets") or {}).get(m) or {}).get("setups", {}).get(name, {})
                        v_h, c_bp, _n = estimate(cells, sess, reg)
                        est[k] = (v_h, c_bp)
                    if math.isfinite(est[k][1]) and est[k][0] >= 2_000.0:
                        hour.append((*est[k], vol, pnl))
            out.append(sorted(hour, key=lambda x: -x[0]))
    return out


def replay(hours: list[Hour], ceiling: float, budget: float) -> tuple[float, float]:
    """(volume, loss) the autopilot's rule would have had, hour by hour: the fastest setup whose estimate is within
    `ceiling`, its real result that hour, out of a pot of `budget` a day (holding POT_DAYS_TUNE days)."""
    pot = vol_total = loss_total = 0.0
    for i, hour in enumerate(hours):
        if i % 24 == 0:
            pot = min(pot + budget, POT_DAYS_TUNE * budget)
        pick = next((x for x in hour if x[1] <= ceiling), None)
        if pick is None or pot < 0.5:
            continue
        vol, pnl = pick[2], pick[3]
        if pnl < 0 and -pnl > pot:   # the pot runs out inside the hour
            vol *= pot / -pnl
            pnl = -pot
        pot += pnl
        vol_total += vol
        loss_total -= pnl
    return vol_total, loss_total


def tune(pb: dict[str, Any], by_day: dict[str, dict[str, list[list[Any]]]]) -> dict[str, float]:
    """The cost ceiling that gave the most volume for each daily budget over the last TUNE_DAYS days
    ({"5": 1.4, ...}); the autopilot uses the one for its budget when the owner has not fixed a ceiling. Days
    without every market's data are left out, so a newly recorded market does not skew the replay."""
    days = sorted({d for per in by_day.values() for d in per})[-TUNE_DAYS:]
    if len(days) < 7:
        return {}
    hours = hours_of(pb, by_day, days)
    out = {}
    for b in TUNE_BUDGETS:
        res = [(replay(hours, c, b)[0], -c) for c in TUNE_CEILINGS]
        out[f"{b:g}"] = -max(res)[1]
    return out


def ceiling_for(pb: dict[str, Any], budget: float) -> float | None:
    """The tuned ceiling for a daily budget (the nearest tuned budget at or below it)."""
    c = pb.get("ceilings") or {}
    if not c:
        return None
    keys = sorted(float(k) for k in c)
    k = max([x for x in keys if x <= budget + 1e-9] or keys[:1])
    return float(c[f"{k:g}"])


def table_text(pb: dict[str, Any], max_cost_bp: float | None = None) -> str:
    """The table by eye: per market and session, each setup's volume per hour and cost (all regimes), then the
    regime split of the fastest one."""
    out = [f"playbook at ${float(pb.get('capital') or 0):,.0f} of capital, built "
           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(float(pb.get('ts') or 0)))}"]
    for m, e in (pb.get("markets") or {}).items():
        out.append(f"\n{m} · {e['days']} days {e['first']} to {e['last']} · {e['leverage']:g}x")
        out.append(f"  {'session':13s}" + "".join(f"{n:>17s}" for n in e["setups"]))
        for sess in sessions.SESSIONS:
            row = f"  {sessions.TITLES[sess]:13s}"
            for cells in e["setups"].values():
                c = cells.get(sess)
                row += f"{c[1] / c[0] / 1e3:8.1f}k {-c[2] / c[1] * 1e4 if c[1] else 0:5.2f}bp" if c and c[0] else \
                    f"{'-':>17s}"
            out.append(row)
    return "\n".join(out)
