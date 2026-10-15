"""Positions held overnight or for days: the data, the venues' funding, the fills and the account.

The day-trading backtest (sim.py) closes everything by the end of the session. These strategies hold through the
night, the weekend or a trend, so three more things matter: funding (a perpetual's long pays its shorts every hour,
about 1-2 bp a day on Lighter and Arcus), gaps (the price can jump past a stop while the stock market is shut) and
liquidation (at 50x on Lighter a 0.8% move against the position closes it).

A strategy sees "coarse" bars (a day, four hours, an hour) and answers at each bar's close; its stops and targets are
checked on the finer bars inside (one-minute or one-hour), in time order:

- `Enter`/`Exit` at "close" fill at the bar's close (the price the decision was taken on: for stocks the price at
  15:59 New York, a minute before the bell, which a perpetual can be traded at); at "open" they fill at the next
  bar's open (09:30 New York for stocks).
- A stop fills at its price, or at the open of a bar that opens past it (a gap); a target fills at its price. When one
  bar reaches both, the stop counts.
- Costs per side in bp of the position: a market order `cost_bp`, a stop `stop_bp`, a target (a resting limit) 0.
- Funding: every hour mark the position is held through, the venue's own hourly rate where the venue has published
  it (since late June 2026), its average rate before.

A trade's result is a fraction of the position's value; `equity` turns trades into an account at a fixed leverage or
at a fixed risk per trade, and closes a trade at a loss of the margin when the worst price touched reaches the
liquidation price.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np

from . import DATA
from . import bars as B

DAILY = DATA / "daily"
FUNDING = DATA / "funding"
HOUR = 3600


# ---- venues --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Venue:
    name: str
    max_lev: float          # the venue's highest leverage on this market
    mm: float               # maintenance margin, a share of the position's value (below it: liquidated)
    cost_bp: float          # a market order: fee + half the spread + a little depth
    stop_bp: float          # a stop's market order, with slippage while the price runs
    funding_bp_h: float     # what a long paid an hour on average (measured on the venue, Jun-Oct 2026)
    funding_file: str       # data/funding/<file>.csv: the venue's hourly rates


# Costs as in core.MARKETS (spreads measured on the recorded books; Arcus's 2.25 bp taker fee). Margins from the venues'
# market details (Oct 2026): Lighter SPY/QQQ 2% initial (50x) and 1.2% maintenance, XAU 4% (25x) and 2.4%; Arcus SPY 2%
# (3% outside its hours) and 1.33%, QQQ 4% and 2.67%. Funding: the venues' average hourly rate, 26 Jun-15 Oct 2026.
VENUES = {
    "SPY": {"lighter": Venue("lighter", 50, 0.012, 0.65, 1.15, 0.050, "lighter_SPY"),
            "arcus": Venue("arcus", 33, 0.01333, 2.85, 3.35, 0.078, "arcus_SPY")},
    "QQQ": {"lighter": Venue("lighter", 50, 0.012, 0.75, 1.25, 0.061, "lighter_QQQ"),
            "arcus": Venue("arcus", 25, 0.02667, 3.2, 3.7, 0.103, "arcus_QQQ")},
    "XAU": {"lighter": Venue("lighter", 25, 0.024, 0.55, 1.05, 0.096, "lighter_XAU")},
}


class Funding:
    """What a long pays between two moments (a fraction of the position's value): one payment per hour mark in
    between, at the venue's published rate for that hour, or its average where it published none. `scale` multiplies
    every rate (0 = no funding, 2 = twice as much)."""

    def __init__(self, venue: Venue, scale: float = 1.0, series: bool = True):
        self.mean = venue.funding_bp_h * 1e-4 * scale
        self.h0 = self.h1 = 0
        self.cum = np.zeros(1)
        path = FUNDING / f"{venue.funding_file}.csv"
        if series and path.exists():
            rows = [(int(r[0]) // HOUR, float(r[1])) for r in list(csv.reader(path.open()))[1:]]
            if rows:
                self.h0, self.h1 = rows[0][0], rows[-1][0] + 1
                rate = np.full(self.h1 - self.h0, self.mean)
                for h, r in rows:
                    rate[h - self.h0] = r * scale
                self.cum = np.r_[0.0, np.cumsum(rate)]

    def __call__(self, t0: int, t1: int) -> float:
        """Sum of the rates at the hour marks h*3600 with t0 < h*3600 <= t1."""
        a, b = t0 // HOUR + 1, t1 // HOUR + 1          # marks a .. b-1
        if b <= a:
            return 0.0
        lo, hi = max(a, self.h0), min(b, self.h1)
        inside = (self.cum[hi - self.h0] - self.cum[lo - self.h0]) if hi > lo else 0.0
        return inside + self.mean * ((b - a) - max(0, hi - lo))


# ---- coarse bars over fine bars ------------------------------------------------------------------------------------

@dataclass
class Coarse:
    """The bars a strategy decides on, each tied to the fine bars its path is checked on.

    Bar i opens at t_o[i] with price o[i] (the fill of an order placed "open") and closes at t_c[i] with price c[i]
    (the fill of an order placed "close"). The fine bars [fc[i-1], fo[i]) are the time between the previous close and
    this open (the night, for stocks), and [fo[i], fc[i]) the bar itself."""
    name: str
    t_o: np.ndarray
    t_c: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray           # noqa: E741
    c: np.ndarray
    day: np.ndarray         # the local day the bar belongs to (a day number: B.day_str turns it into a date)
    fo: np.ndarray
    fc: np.ndarray
    fine: B.Bars
    tz: ZoneInfo = B.NY

    def __len__(self) -> int:
        return len(self.c)


def _groups(fine: B.Bars, key: np.ndarray, name: str, tz: ZoneInfo, day: np.ndarray) -> Coarse:
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(fine)]
    step = int(np.median(np.diff(fine.t))) if len(fine) > 1 else 60
    return Coarse(name, fine.t[starts], fine.t[ends - 1] + step, fine.o[starts],
                  np.maximum.reduceat(fine.h, starts), np.minimum.reduceat(fine.l, starts), fine.c[ends - 1],
                  day[starts], starts.astype(np.int64), ends.astype(np.int64), fine, tz)


def by_hours(fine: B.Bars, hours: int, name: str, tz: ZoneInfo = B.NY, roll: str = "17:00") -> Coarse:
    """Bars of `hours` hours (24 = a day) on New York's 17:00 roll, the day forex and gold close on: four-hour bars
    open at 17:00, 21:00, 01:00, 05:00, 09:00 and 13:00 New York. The bar's day is the day it closes."""
    roll_s = _hm(roll)
    loc = B.local(fine.t, tz) - roll_s
    key = loc // (hours * HOUR)
    day = (loc // B.DAY + 1).astype(np.int64)
    return _groups(fine, key, name, tz, day)


def hourly(fine: B.Bars, name: str, tz: ZoneInfo = B.LONDON) -> Coarse:
    """Each fine (one-hour) bar its own coarse bar; `day` is the local day on `tz`'s clock."""
    return _groups(fine, fine.t // HOUR, name, tz, (B.local(fine.t, tz) // B.DAY).astype(np.int64))


def sessions(fine: B.Bars, name: str, tz: ZoneInfo = B.NY, open_at: str = "09:30", close_at: str = "15:59",
             end_at: str = "20:00") -> Coarse:
    """One bar a weekday from fine bars that run into the night: it opens with the first fine bar at or after
    `open_at` (its open price) and closes on the first fine bar at or after `close_at`, at that bar's OPEN (for
    stocks: the price at 15:59, a minute before the bell). High and low are the session's in between. Days missing
    either end are left out."""
    loc = B.local(fine.t, tz)
    d, tod = loc // B.DAY, loc % B.DAY
    o_s, c_s, e_s = _hm(open_at), _hm(close_at), _hm(end_at)
    t_o, t_c, o, h, lo, c, day, fo, fc = ([] for _ in range(9))
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    ends = np.r_[starts[1:], len(fine)]
    for a, b in zip(starts.tolist(), ends.tolist(), strict=True):
        if B.weekday(int(d[a])) >= 5:
            continue
        td = tod[a:b]
        i = a + int(np.searchsorted(td, o_s))
        j = a + int(np.searchsorted(td, c_s))
        if i >= b or j >= b or tod[i] >= c_s or tod[j] >= e_s or j <= i:
            continue
        t_o.append(fine.t[i])
        t_c.append(fine.t[j])
        o.append(fine.o[i])
        c.append(fine.o[j])
        h.append(fine.h[i:j].max())
        lo.append(fine.l[i:j].min())
        day.append(d[a])
        fo.append(i)
        fc.append(j)
    arr = lambda x, ty=np.float64: np.array(x, dtype=ty)      # noqa: E731
    return Coarse(name, arr(t_o, np.int64), arr(t_c, np.int64), arr(o), arr(h), arr(lo), arr(c), arr(day, np.int64),
                  arr(fo, np.int64), arr(fc, np.int64), fine, tz)


def yahoo(sym: str, until_day: int | None = None, since: int = 0) -> Coarse:
    """The daily history from data/daily/yahoo_<sym>.csv from `since` (a UTC second): opens 09:30 and closes 16:00
    New York (the official prices). Each day is its own fine bar, so a gap shows only as the open. Today's unfinished
    day is left out."""
    rows = list(csv.reader((DAILY / f"yahoo_{sym}.csv").open()))[1:]
    today = int(B.local(np.array([int(time.time())]), B.NY)[0]) // B.DAY
    until = until_day if until_day is not None else today
    day = np.array([(dt.date.fromisoformat(r[0]) - dt.date(1970, 1, 1)).days for r in rows], dtype=np.int64)
    keep = (day < until) & (day >= since // B.DAY)
    day = day[keep]
    a = np.array([[float(x) for x in r[1:5]] for r in rows], dtype=np.float64)[keep]
    t_o = np.array([B.local_to_utc(int(x), _hm("09:30"), B.NY) for x in day], dtype=np.int64)
    t_c = np.array([B.local_to_utc(int(x), _hm("16:00"), B.NY) for x in day], dtype=np.int64)
    fine = B.Bars(t_o, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.zeros(len(day)))
    idx = np.arange(len(day), dtype=np.int64)
    return Coarse(f"yahoo_{sym}", t_o, t_c, a[:, 0], a[:, 1], a[:, 2], a[:, 3], day, idx, idx + 1, fine, B.NY)


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * HOUR + int(m) * 60


# ---- orders and positions ------------------------------------------------------------------------------------------

@dataclass
class Enter:
    side: int
    at: str = "close"               # "close": this bar's close; "open": the next bar's open
    stop: float | None = None       # a price, or ...
    stop_dist: float | None = None  # ... a distance from the fill
    target_r: float | None = None   # target at this many stop distances from the fill
    frac: float = 1.0               # share of the full size (scaling in: 1/3, then more with Add)
    tag: str = ""


@dataclass
class Add:
    frac: float
    at: str = "close"


@dataclass
class Exit:
    at: str = "close"
    reason: str = "rule"


@dataclass
class SetStop:
    price: float


@dataclass
class Pos:
    side: int
    legs: list[tuple[int, float, float, float]]     # (time, price, share of full size, cost bp)
    i_in: int
    stop: float | None = None
    target: float | None = None
    stop0: float | None = None
    worst: float = 0.0              # the worst price touched while held
    best: float = 0.0               # the best close while held
    bars: int = 0                   # coarse bars closed while held
    tag: str = ""
    fund: float = 0.0               # funding paid so far (a share of the full size's value at entry)
    t_fund: int = 0                 # paid up to here
    data: dict = field(default_factory=dict)

    @property
    def frac(self) -> float:
        return sum(x[2] for x in self.legs)

    @property
    def avg(self) -> float:
        return sum(x[1] * x[2] for x in self.legs) / self.frac


@dataclass
class STrade:
    side: int
    t_in: int
    t_out: int
    entry: float                    # average entry
    exit: float
    frac: float                     # share of the full size held at the end (1 unless it scaled in partly)
    legs: int
    gross: float                    # the price move, a fraction of the full size's value
    cost: float                     # fees, spread and slippage, both sides
    fund: float                     # funding paid (negative: received)
    mae: float                      # the worst price touched, as a fraction against the average entry (>= 0)
    stop_dist: float                # the first stop's distance as a fraction of the entry (nan: no stop)
    reason: str
    tag: str = ""

    @property
    def net(self) -> float:
        return self.gross - self.cost - self.fund

    @property
    def hours(self) -> float:
        return (self.t_out - self.t_in) / HOUR


class Rule:
    """A swing strategy: `prepare` once on the coarse bars, then `decide` at each bar's close."""
    name = "rule"
    market = "SPY"

    def prepare(self, cb: Coarse) -> None:
        self.cb = cb

    def decide(self, i: int, pos: Pos | None) -> list:
        return []


def simulate(rule: Rule, cb: Coarse, venue: Venue, funding: Funding, start: int = 0, end: int | None = None
             ) -> list[STrade]:
    """Every trade `rule` makes on `cb`, decisions from coarse bar `start` (earlier bars only warm the indicators)
    until bar `end`; a position still open at the end is closed at the last close."""
    return _simulate(rule, cb, venue, funding, start, end, live=False)[0]


def simulate_live(rule: Rule, cb: Coarse, venue: Venue, funding: Funding, start: int = 0
                  ) -> tuple[list[STrade], Pos | None]:
    """The same for the paper bot (swing_bot.py), whose last bar is today: the rule decides at the last bar too (an
    order at "close" fills at its close), and a position still open is returned as it stands, not closed."""
    return _simulate(rule, cb, venue, funding, start, None, live=True)


def _simulate(rule: Rule, cb: Coarse, venue: Venue, funding: Funding, start: int, end: int | None, live: bool
              ) -> tuple[list[STrade], Pos | None]:
    rule.prepare(cb)
    f = cb.fine
    n = len(cb) if end is None else min(end, len(cb))
    out: list[STrade] = []
    pos: Pos | None = None
    pending: list = []

    def fill(a, px: float, t: int, i: int) -> None:
        nonlocal pos
        if isinstance(a, Enter):
            if pos is not None:
                return
            stop = a.stop if a.stop is not None else (px - a.side * a.stop_dist if a.stop_dist else None)
            target = (px + a.side * a.target_r * abs(px - stop)) if (a.target_r and stop is not None) else None
            pos = Pos(a.side, [(t, px, a.frac, venue.cost_bp)], i, stop, target, stop, px, px, 0, a.tag, 0.0, t)
        elif isinstance(a, Add) and pos is not None:
            accrue(t, px)
            pos.legs.append((t, px, a.frac, venue.cost_bp))
        elif isinstance(a, Exit) and pos is not None:
            close(px, t, venue.cost_bp, a.reason)
        elif isinstance(a, SetStop) and pos is not None:
            pos.stop = a.price

    def accrue(t: int, px: float) -> None:
        """Funding from the last payment to `t`, on the position's value at `px` (it grows with the price)."""
        if t > pos.t_fund:
            pos.fund += funding(pos.t_fund, t) * pos.side * sum(s * px / lp for _, lp, s, _ in pos.legs)
            pos.t_fund = t

    def close(px: float, t: int, cost_bp: float, reason: str) -> None:
        nonlocal pos
        p = pos
        accrue(t, px)
        fr = p.frac
        gross = sum(s * p.side * (px / lp - 1.0) for _, lp, s, _ in p.legs)
        cost = sum(s * c for _, _, s, c in p.legs) * 1e-4 + fr * cost_bp * 1e-4
        fund = p.fund
        avg = p.avg
        mae = max(0.0, (1.0 - p.worst / avg) if p.side > 0 else (p.worst / avg - 1.0))
        sd = abs(p.legs[0][1] - p.stop0) / p.legs[0][1] if p.stop0 is not None else math.nan
        out.append(STrade(p.side, p.legs[0][0], t, avg, px, fr, len(p.legs), gross, cost, fund, mae, sd, reason, p.tag))
        pos = None

    def walk(a: int, b: int) -> None:
        """The fine bars [a, b) with a position on: stop first, then target; the worst price touched."""
        if pos is None or b <= a:
            return
        lo, hi = f.l[a:b], f.h[a:b]
        s, tg, side = pos.stop, pos.target, pos.side
        k_s = k_t = b - a
        if s is not None:
            hit = (lo <= s) if side > 0 else (hi >= s)
            if hit.any():
                k_s = int(np.argmax(hit))
        if tg is not None:
            hit = (hi > tg) if side > 0 else (lo < tg)
            if hit.any():
                k_t = int(np.argmax(hit))
        k = min(k_s, k_t)
        last = b if k == b - a else a + k + 1
        if side > 0:
            pos.worst = min(pos.worst, float(f.l[a:last].min()))
        else:
            pos.worst = max(pos.worst, float(f.h[a:last].max()))
        if k == b - a:
            return
        op = float(f.o[a + k])
        if k_s <= k_t:
            px = min(s, op) if side > 0 else max(s, op)
            if side > 0:
                pos.worst = min(pos.worst, px)
            else:
                pos.worst = max(pos.worst, px)
            close(px, int(f.t[a + k]), venue.stop_bp, "stop")
        else:
            close(tg, int(f.t[a + k]), 0.0, "target")

    for i in range(start, n):
        if i > start:
            walk(int(cb.fc[i - 1]), int(cb.fo[i]))
        for a in pending:
            fill(a, float(cb.o[i]), int(cb.t_o[i]), i)
        pending = []
        walk(int(cb.fo[i]), int(cb.fc[i]))
        if i == n - 1 and not live:
            break
        if pos is not None:
            pos.bars += 1
            pos.best = max(pos.best, cb.c[i]) if pos.side > 0 else min(pos.best, cb.c[i])
            accrue(int(cb.t_c[i]), float(cb.c[i]))
        for a in rule.decide(i, pos):
            if getattr(a, "at", "close") == "close":
                fill(a, float(cb.c[i]), int(cb.t_c[i]), i)
            else:
                pending.append(a)
    if pos is not None and not live:
        close(float(cb.c[n - 1]), int(cb.t_c[n - 1]), venue.cost_bp, "end")
    return out, pos


# ---- the account ---------------------------------------------------------------------------------------------------

@dataclass
class Account:
    final: float
    worst_dd: float                 # the deepest fall from a high, counting the worst price inside trades
    liquidations: int
    trades: int


def equity(trades: list[STrade], venue: Venue, lev: float | None = None, risk: float | None = None,
           start: float = 1000.0) -> Account:
    """The account after `trades`, each sized on the account as it then stood: the full size at `lev` times the
    account, or so that the first stop costs `risk` of it (never above 90% of the venue's highest leverage). A trade
    that scaled in holds `frac` of the full size at the end; its result (`net`) is already a share of the full size.
    A trade whose worst price reaches the liquidation price (where the account falls to the maintenance margin) is
    closed there, and half the maintenance margin goes in fees and the liquidator's discount."""
    e = peak = start
    dd = 0.0
    liq = 0
    for tr in trades:
        if e <= 1e-6:
            break
        if lev is not None:
            full = lev * e
        else:
            if not tr.stop_dist or math.isnan(tr.stop_dist):
                continue
            full = min(risk * e / tr.stop_dist, venue.max_lev * 0.9 * e)
        held = full * tr.frac                      # what was on at the end, for the liquidation price
        L = held / e
        room = 1.0 / L - venue.mm
        if tr.mae >= room:
            liq += 1
            trough = e = max(0.0, held * venue.mm * 0.5)
        else:
            trough = e * (1.0 - L * tr.mae)
            e = max(0.0, e + full * tr.net)
        dd = max(dd, 1.0 - min(trough, e) / peak)
        peak = max(peak, e)
    return Account(e, dd, liq, len(trades))
