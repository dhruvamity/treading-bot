"""`deltaneutral backtest`: the bot's 24-hour cycle replayed on the venues' own five-minute prices and hourly funding.

Each cycle opens at `open_ny` (New York time), holds 24 hours and closes; the next one opens at once. At the open
each market gets the same dollars on both venues, as much as the margin of the venue with less room allows (Arcus's
off-hours margin when the stock market is closed: then the position is smaller, and Lighter's legs are as small as
Arcus's).
Every five minutes each venue's equity is checked against its maintenance margin; every hour both venues pay or
receive their funding. After each close the money is made even again by a transfer (`transfers`).

Fills (`execution`). "chase", the bot's default: every entry and exit rests limit orders on both venues that follow
the best price; the measured share of them that fills in time (costs.maker_fills, per venue, market and session)
costs `maker_bp` and no fee, the rest is taken. "arcus": only Arcus's legs rest, Lighter's take. "take": both take.
Taking costs what that many dollars cost on the venue's book (half the spread and the levels behind it: costs.py)
plus its fee. While one venue's limit order waits for the other's, a leg is briefly unhedged: that adds noise, not
an expected cost, and is not modelled; an entry that would not finish in time is counted as finished.

Stops (`stop_share`, 0.75). At each open, each venue's room = its money less its maintenance margin (Lighter: the
6% where its partial liquidation starts). The bot closes everything once a venue has used 75% of its room; and each
leg carries a stop and a take-profit on its venue, the same two prices on both venues, 75% of the way to where that
market alone would liquidate the weaker venue. Whichever venue's leg triggers, the other's triggers at the same
price, so the two stay matched; the bot then closes the other market. Stops and guard closes take.

What this cannot see: the order books of the past month (the costs are a recorded month's typical ones), queue
position, a venue refusing an order, and Arcus's off-hours price band refusing a fill at its edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from arbitrage.hours import open_hours as arcus_open

from . import plan as P
from .costs import Costs
from .plan import ARCUS, LIGHTER, VENUES, Pair
from .series import FIVE, Market, blocked, cash_session, next_open

HOUR = 3600
DAY = 86_400


@dataclass(frozen=True)
class Settings:
    open_ny: float = 10.0            # the daily open, New York time (10.0 = 10:00, half an hour after the cash open)
    hold_h: float = 24.0
    margin_use: float = 0.9          # share of a venue's equity its initial margin may use
    max_leverage: float = 0.0        # cap on each venue's (gross / equity); 0 = the venues' highest
    ratio: str = "equal"             # "equal": the same dollars of X and Y; "beta": Y's dollars = beta * X's
    way: str = "auto"                # "auto": the way the funding should pay over the hold (plan.forecast, as the
                                     # bot); "+1" / "-1": fixed
    funding_days: float = 7.0        # "auto" judges on each venue's hourly funding over this many days
    offhours: str = "keep"           # "keep": a position stays at the close; "cut": cut to Arcus's off-hours size
    stop_share: float = 0.75         # close at this share of the way to liquidation (the bot's guard and the venues'
                                     # stops); 0 = only the venues' own liquidation
    venue_stops: bool = True         # each leg's stop and take-profit resting on its venue
    execution: str = "chase"         # "chase": both venues rest limit orders; "arcus": Arcus rests, Lighter takes;
                                     # "take": both take
    maker_bp: float = 0.5            # what a resting fill costs against the mid (it gives up its half spread to
                                     # prices that run on: adverse selection)
    maker_fill: float = -1.0         # share of resting orders filled in time; -1 = measured (costs.json, per venue,
                                     # market and session)
    transfers: str = "each"          # "each": money made even after every close; "never"
    weekends: bool = True            # open on Saturday and Sunday too
    skip: tuple[str, ...] = ()       # report dates: nothing held from 16:00 New York the day before to the day after


@dataclass
class Leg:
    units: float = 0.0               # + long, - short
    entry: float = 0.0


@dataclass
class Book:
    """One venue's account in the replay."""
    cash: float                      # money, with realised results, costs and funding
    legs: dict[str, Leg] = field(default_factory=dict)

    def value(self, px: dict[str, float]) -> float:
        return self.cash + sum(g.units * (px[s] - g.entry) for s, g in self.legs.items() if g.units)

    def notional(self, px: dict[str, float]) -> dict[str, float]:
        return {s: abs(g.units) * px[s] for s, g in self.legs.items()}


@dataclass
class Cycle:
    opened: int
    closed: int = 0
    way: int = 1
    nx: float = 0.0
    ny: float = 0.0
    leverage: dict[str, float] = field(default_factory=dict)
    off_hours: bool = False
    end: str = ""                    # "time", "guard", "liquidated arcus", ...
    start_equity: dict[str, float] = field(default_factory=dict)
    end_equity: dict[str, float] = field(default_factory=dict)
    price: dict[str, float] = field(default_factory=lambda: {v: 0.0 for v in VENUES})
    funding: dict[str, float] = field(default_factory=lambda: {v: 0.0 for v in VENUES})
    costs: dict[str, float] = field(default_factory=lambda: {v: 0.0 for v in VENUES})
    volume: float = 0.0
    transfer: float = 0.0
    transfer_from: str = ""
    min_room: float = math.inf


@dataclass
class Result:
    pair: Pair
    settings: Settings
    start: int
    end: int
    start_equity: dict[str, float]
    equity: dict[str, float]
    cycles: list[Cycle]
    volume: float
    oi_hours: float                 # dollars of open interest held x hours, both venues and both markets
    liquidations: int
    guards: int
    transfers: list[tuple[int, float, str]]
    stops: int = 0

    @property
    def days(self) -> float:
        return (self.end - self.start) / DAY

    @property
    def total(self) -> float:
        return sum(self.equity.values())

    @property
    def pnl(self) -> float:
        return self.total - sum(self.start_equity.values())

    def parts(self) -> dict[str, float]:
        out = {"price": 0.0, "funding": 0.0, "costs": 0.0}
        for c in self.cycles:
            for v in VENUES:
                out["price"] += c.price[v]
                out["funding"] += c.funding[v]
                out["costs"] += c.costs[v]
        return out

    @property
    def avg_oi(self) -> float:
        h = (self.end - self.start) / HOUR
        return self.oi_hours / h if h > 0 else 0.0


def beta(x: np.ndarray, y: np.ndarray) -> float:
    """Dollars of Y per dollar of X that leave a venue's two legs least exposed: cov(X, Y) / var(Y) of five-minute
    returns (bars where both moved), held between 0.25 and 4."""
    rx, ry = np.diff(np.log(x)), np.diff(np.log(y))
    ok = np.isfinite(rx) & np.isfinite(ry) & (rx != 0) & (ry != 0)
    if ok.sum() < 50:
        return 1.0
    c = np.cov(rx[ok], ry[ok])
    return float(min(4.0, max(0.25, c[0, 1] / c[1, 1]))) if c[1, 1] > 0 else 1.0


class Replay:
    def __init__(self, pair: Pair, mx: Market, my: Market, costs: Costs, st: Settings,
                 equity: dict[str, float] | None = None) -> None:
        self.pair, self.mx, self.my, self.costs, self.st = pair, mx, my, costs, st
        self.t = mx.t
        self.start_equity = dict(equity or {ARCUS: 100.0, LIGHTER: 100.0})
        self.books = {v: Book(self.start_equity[v]) for v in VENUES}
        self.cycles: list[Cycle] = []
        self.cur: Cycle | None = None
        self.volume = 0.0
        self.oi_hours = 0.0
        self.liquidations = 0
        self.guards = 0
        self.transfers: list[tuple[int, float, str]] = []
        self.size_off = False          # the open position is at Arcus's off-hours size ("cut")
        self.stops = 0
        self.room0: dict[str, float] = {}                    # each venue's room at the open
        self.levels: dict[str, tuple[float, float]] = {}     # each market's stop and take-profit prices

    # ---- prices
    def px(self, v: str, i: int) -> dict[str, float]:
        return {self.pair.x: float(self.mx.px[v][i]), self.pair.y: float(self.my.px[v][i])}

    def rules(self, sym: str) -> dict[str, P.Rules]:
        return self.mx.rules if sym == self.pair.x else self.my.rules

    # ---- fills
    def cost_bp(self, v: str, sym: str, i: int, usd: float, urgent: bool = False) -> float:
        sess = cash_session(float(self.t[i]))
        take = self.costs.take_bp(v, sym, sess, usd) + self.rules(sym)[v].taker_bp
        rests = self.st.execution == "chase" or (self.st.execution == "arcus" and v == ARCUS)
        if rests and not urgent:
            p = self.st.maker_fill if self.st.maker_fill >= 0 else self.costs.maker_fill(sym, sess, v)
            return p * (self.st.maker_bp + self.rules(sym)[v].maker_bp) + (1 - p) * take
        return take

    def trade(self, v: str, sym: str, units: float, i: int, urgent: bool = False) -> None:
        """Buy (+) or sell (-) `units` of `sym` on venue `v` at bar i's price, paying the fill cost."""
        if units == 0:
            return
        b = self.books[v]
        g = b.legs.setdefault(sym, Leg())
        p = self.px(v, i)[sym]
        usd = abs(units) * p
        cost = usd * self.cost_bp(v, sym, i, usd, urgent) / 1e4
        b.cash -= cost
        self.volume += usd
        if self.cur is not None:
            self.cur.costs[v] -= cost
            self.cur.volume += usd
        new = g.units + units
        if g.units == 0 or (g.units > 0) == (units > 0):           # opening or adding: average the entry
            g.entry = (g.entry * abs(g.units) + p * abs(units)) / abs(new) if new else 0.0
        else:                                                       # reducing (never flips here)
            closed = min(abs(units), abs(g.units))
            res = closed * (p - g.entry) * (1 if g.units > 0 else -1)
            b.cash += res
            if self.cur is not None:
                self.cur.price[v] += res
        g.units = new if abs(new) > 1e-12 else 0.0

    def close_all(self, i: int, skip: str = "", urgent: bool = False, only: str = "") -> None:
        for v in VENUES:
            if v == skip:
                continue
            for sym, g in list(self.books[v].legs.items()):
                if g.units and (not only or sym == only):
                    self.trade(v, sym, -g.units, i, urgent)

    def room(self, v: str, i: int) -> float:
        """Venue v's money less its maintenance margin now: how much it can still lose."""
        px = self.px(v, i)
        b = self.books[v]
        n = b.notional(px)
        return b.value(px) - P.maintenance(n.get(self.pair.x, 0.0), n.get(self.pair.y, 0.0), self.mx.rules[v],
                                           self.my.rules[v])

    def protect(self, i: int) -> None:
        """At the open: each venue's room, and each market's stop and take-profit prices (the same on both venues):
        `stop_share` of the way to where that market alone would use up the weaker venue's room."""
        self.room0 = {v: self.room(v, i) for v in VENUES}
        self.levels = {}
        if self.st.stop_share <= 0:
            return
        for sym in self.pair.symbols():
            dist = math.inf
            for v in VENUES:
                g = self.books[v].legs.get(sym)
                if g is not None and g.units:
                    dist = min(dist, self.room0[v] / (abs(g.units) * self.px(v, i)[sym]))
            if math.isfinite(dist) and dist > 0:
                ref = sum(self.px(v, i)[sym] for v in VENUES) / len(VENUES)
                d = self.st.stop_share * dist
                self.levels[sym] = (ref * (1 - d), ref * (1 + d))

    # ---- the cycle
    def target(self, i: int, off: bool, eq: dict[str, float]) -> tuple[float, float, P.Size]:
        st = self.st
        ratio = 1.0
        if st.ratio == "beta":
            lo = max(0, i - 7 * DAY // FIVE)
            ratio = beta(self.mx.px[LIGHTER][lo:i + 1], self.my.px[LIGHTER][lo:i + 1])
        s = P.size(eq, self.mx.rules, self.my.rules, off, st.margin_use, st.max_leverage, ratio)
        return s.nx, s.ny, s

    def way_at(self, i: int) -> int:
        if self.st.way in ("+1", "1"):
            return 1
        if self.st.way == "-1":
            return -1
        lo = max(0, i - int(self.st.funding_days * DAY) // FIVE)
        hours = [k for k in range(lo, i) if int(self.t[k]) % HOUR == 0]
        hist = {(v, mk.symbol): {int(self.t[k]): float(mk.funding[v][k]) for k in hours}
                for mk in (self.mx, self.my) for v in VENUES}
        f = P.forecast(hist, self.mx.symbol, self.my.symbol, float(self.t[i]), self.st.hold_h, arcus_open)
        return f.way if f is not None else 1

    def open(self, i: int) -> None:
        t = float(self.t[i])
        off = not arcus_open(t)
        eq = {v: self.books[v].value(self.px(v, i)) for v in VENUES}
        nx, ny, s = self.target(i, off, eq)
        way = self.way_at(i)
        c = Cycle(opened=int(self.t[i]), way=way, nx=nx, ny=ny, leverage=s.leverage, off_hours=off,
                  start_equity=eq)
        self.cur = c
        self.cycles.append(c)
        self.size_off = off
        sd = P.sides(way)
        refx, refy = float(self.mx.px[LIGHTER][i]), float(self.my.px[LIGHTER][i])
        ux, uy = nx / refx, ny / refy
        for v in VENUES:
            self.trade(v, self.pair.x, sd[v]["x"] * ux, i)
            self.trade(v, self.pair.y, sd[v]["y"] * uy, i)
        self.protect(i)

    def resize(self, i: int, off: bool) -> None:
        """Cut to Arcus's off-hours size at the close, or grow back at the reopen ("cut")."""
        eq = {v: self.books[v].value(self.px(v, i)) for v in VENUES}
        nx, ny, _ = self.target(i, off, eq)
        for sym, n in ((self.pair.x, nx), (self.pair.y, ny)):
            ref = float((self.mx if sym == self.pair.x else self.my).px[LIGHTER][i])
            want = n / ref
            for v in VENUES:
                g = self.books[v].legs.get(sym)
                if g is None or g.units == 0:
                    continue
                d = (want - abs(g.units)) * (1 if g.units > 0 else -1)
                if (off and abs(want) < abs(g.units)) or (not off and abs(want) > abs(g.units)):
                    self.trade(v, sym, d, i)
        self.size_off = off
        self.protect(i)

    def end_cycle(self, i: int, why: str) -> None:
        c = self.cur
        if c is None:
            return
        c.closed, c.end = int(self.t[i]), why
        c.end_equity = {v: self.books[v].cash for v in VENUES}
        self.cur = None
        if self.st.transfers == "each":
            tr = P.transfer(c.end_equity)
            if tr.amount > 0:
                self.books[tr.source].cash -= tr.amount
                self.books[tr.target].cash += tr.amount
                c.transfer, c.transfer_from = tr.amount, tr.source
                self.transfers.append((int(self.t[i]), tr.amount, tr.source))

    def liquidate(self, v: str, i: int) -> None:
        """Venue v closes both its legs at the mark and keeps its liquidation fee; the bot then closes the other
        venue's legs (they are no longer hedged)."""
        b = self.books[v]
        px = self.px(v, i)
        for sym, g in b.legs.items():
            if not g.units:
                continue
            res = g.units * (px[sym] - g.entry)
            fee = abs(g.units) * px[sym] * self.rules(sym)[v].liq_fee
            b.cash += res - fee
            if self.cur is not None:
                self.cur.price[v] += res
                self.cur.costs[v] -= fee
            g.units = 0.0
        b.cash = max(0.0, b.cash)
        self.liquidations += 1
        self.close_all(min(i + 1, len(self.t) - 1), skip=v)
        self.end_cycle(i, f"liquidated {v}")

    def run(self, start: int, end: int) -> Result:
        st = self.st
        t = self.t
        i0 = int(np.searchsorted(t, start))
        i1 = int(np.searchsorted(t, end))
        next_open = self.first_open(i0)
        close_at = 0
        prev_open = arcus_open(float(t[i0]))
        for i in range(i0, i1):
            ti = int(t[i])
            # funding at the top of each hour, on the position held through it
            if self.cur is not None and ti % HOUR == 0:
                for v in VENUES:
                    pay = 0.0
                    for sym, mk in ((self.pair.x, self.mx), (self.pair.y, self.my)):
                        g = self.books[v].legs.get(sym)
                        if g is not None and g.units:
                            pay -= g.units * float(mk.px[v][i]) * float(mk.funding[v][i])
                    self.books[v].cash += pay
                    self.cur.funding[v] += pay
            if self.cur is not None:
                # liquidation and the guard, venue by venue
                gone = False
                for v in VENUES:
                    px = self.px(v, i)
                    b = self.books[v]
                    eqv = b.value(px)
                    notional = b.notional(px)
                    rm = P.room(eqv, notional.get(self.pair.x, 0), notional.get(self.pair.y, 0),
                                self.mx.rules[v], self.my.rules[v])
                    self.cur.min_room = min(self.cur.min_room, rm)
                    if rm <= 0:
                        self.liquidate(v, i)
                        gone = True
                        break
                if not gone and st.venue_stops and self.levels:       # a leg's stop or take-profit on its venue
                    for sym, (lo, hi) in self.levels.items():
                        if any(not lo < self.px(v, i)[sym] < hi for v in VENUES):
                            self.close_all(i, urgent=True, only=sym)
                            self.close_all(i, urgent=True)
                            self.stops += 1
                            self.end_cycle(i, f"stop {sym}")
                            gone = True
                            break
                if not gone and st.stop_share > 0 and any(
                        self.room0.get(v, 0) > 0 and self.room(v, i) <= (1 - st.stop_share) * self.room0[v]
                        for v in VENUES):
                    self.close_all(i, urgent=True)
                    self.guards += 1
                    self.end_cycle(i, "guard")
                    gone = True
                if gone:
                    next_open = self.next_open_after(ti)
                    continue
                self.oi_hours += sum(sum(self.books[v].notional(self.px(v, i)).values()) for v in VENUES) * FIVE / HOUR
                # the stock market's close and reopen (Arcus's margin changes)
                now_open = arcus_open(float(ti))
                if st.offhours == "cut" and now_open != prev_open:
                    self.resize(i, off=not now_open)
                prev_open = now_open
                if st.skip and blocked(float(ti), float(ti) + 1800, st.skip) is not None:
                    self.close_all(i)
                    self.end_cycle(i, "skip date")
                    next_open = self.next_open_after(ti)
                elif ti >= close_at:
                    self.close_all(i)
                    self.end_cycle(i, "time")
            else:
                prev_open = arcus_open(float(ti))
            if self.cur is None and ti >= next_open and i < i1 - 1:
                hit = blocked(float(ti), float(ti) + st.hold_h * HOUR, st.skip) if st.skip else None
                if hit is not None:
                    next_open = int(next_open_from(hit[1], st))
                    continue
                if all(math.isfinite(x) for v in VENUES for x in self.px(v, i).values()):
                    self.open(i)
                    close_at = ti + int(st.hold_h * HOUR)
                    next_open = self.next_open_after(ti)
        if self.cur is not None:
            self.close_all(i1 - 1)
            self.end_cycle(i1 - 1, "end of data")
        return Result(self.pair, st, int(t[i0]), int(t[i1 - 1]), self.start_equity,
                      {v: self.books[v].cash for v in VENUES}, self.cycles, self.volume, self.oi_hours,
                      self.liquidations, self.guards, self.transfers, self.stops)

    def first_open(self, i0: int) -> int:
        return int(next_open(float(self.t[i0]), self.st.open_ny, self.st.weekends))

    def next_open_after(self, ti: int) -> int:
        """The next daily open time after ti (Saturdays and Sundays skipped unless `weekends`)."""
        return int(next_open(float(ti) + 1, self.st.open_ny, self.st.weekends))


def next_open_from(t: float, st: Settings) -> float:
    return next_open(t, st.open_ny, st.weekends)


def run(pair: Pair, mx: Market, my: Market, costs: Costs, st: Settings, start: int, end: int,
        equity: dict[str, float] | None = None) -> Result:
    return Replay(pair, mx, my, costs, st, equity).run(start, end)


def variants(base: Settings, **grid: list[Any]) -> list[Settings]:
    out = [base]
    for k, vals in grid.items():
        out = [replace(s, **{k: v}) for s in out for v in vals]
    return out
