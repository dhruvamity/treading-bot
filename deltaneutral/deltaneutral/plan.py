"""The rules the bot and its backtest share: how big a position is, which way round, when a venue is too close to
liquidation, and how much money to move between the venues. Pure functions: no venue, no clock.

The position. Two markets X and Y. With `way` +1, Arcus holds X long and Y short and Lighter holds X short and Y
long; with `way` -1 the reverse. Each market has the same number of units on both venues, so the whole is flat in
both markets (delta-neutral). Each venue on its own holds one market long and the other short, so a venue's result is
the difference between the two markets' moves, not the moves themselves: that is what lets each venue carry its leg
at high leverage without the other venue's money.

Margin (venue docs, 16 Oct 2026). Both venues use cross margin by default: a venue's two legs share its money.
- Arcus: initialMarginFraction while the stock market is open (Mon-Fri 04:00-20:00 New York), and 1.5 times that
  (offHoursInitialMarginFraction) while it is closed. Maintenance does not change at the close: a position opened at
  the in-session leverage stays open; only opening or adding needs the higher margin. So off-hours the bot opens
  smaller, and Lighter's legs follow Arcus's size.
- Lighter: the same margin at every hour.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

ARCUS, LIGHTER = "arcus", "lighter"
VENUES = (ARCUS, LIGHTER)


@dataclass(frozen=True)
class Rules:
    """One market's margin and order rules on one venue."""
    imf: float                 # initial margin fraction while the stock market is open (1 / max leverage)
    imf_off: float             # ... while it is closed (Lighter: the same)
    mmf: float                 # maintenance margin fraction: below it the venue liquidates
    taker_bp: float = 0.0      # taker fee
    maker_bp: float = 0.0
    min_notional: float = 0.0
    step: float = 0.0          # size step (units)
    liq_fee: float = 0.01      # what a liquidation costs, a fraction of the notional closed

    def imf_at(self, off_hours: bool) -> float:
        return self.imf_off if off_hours else self.imf

    def max_leverage(self, off_hours: bool) -> float:
        f = self.imf_at(off_hours)
        return 1 / f if f > 0 else 1.0


@dataclass(frozen=True)
class Pair:
    x: str
    y: str

    @property
    def name(self) -> str:
        return f"{self.x}/{self.y}"

    def symbols(self) -> tuple[str, str]:
        return self.x, self.y


def sides(way: int) -> dict[str, dict[str, int]]:
    """{venue: {"x": +1 long / -1 short, "y": ...}} for a way round."""
    return {ARCUS: {"x": way, "y": -way}, LIGHTER: {"x": -way, "y": way}}


@dataclass(frozen=True)
class Size:
    """What a cycle opens: dollars of each market, the same on both venues."""
    nx: float
    ny: float
    leverage: dict[str, float]     # each venue's (nx + ny) / its equity
    binding: str                   # which venue's margin set the size

    @property
    def gross(self) -> float:
        return self.nx + self.ny


def size(equity: dict[str, float], rx: dict[str, Rules], ry: dict[str, Rules], off_hours: bool,
         margin_use: float = 0.9, max_leverage: float = 0.0, ratio: float = 1.0) -> Size:
    """The largest position both venues can open now: dollars of X `nx` and of Y `ny = ratio * nx` on each venue,
    with each venue's initial margin (Arcus's off-hours one when the market is closed) within `margin_use` of its
    equity. `max_leverage` (0 = none) caps each venue's (nx + ny) / equity as well."""
    best, binding = math.inf, ARCUS
    for v in VENUES:
        e = max(0.0, equity.get(v, 0.0)) * margin_use
        per_x = rx[v].imf_at(off_hours and v == ARCUS) + ratio * ry[v].imf_at(off_hours and v == ARCUS)
        n = e / per_x if per_x > 0 else math.inf
        if max_leverage > 0:
            n = min(n, max_leverage * max(0.0, equity.get(v, 0.0)) / (1 + ratio))
        if n < best:
            best, binding = n, v
    nx = 0.0 if not math.isfinite(best) else best
    ny = ratio * nx
    lev = {v: (nx + ny) / equity[v] if equity.get(v, 0) > 0 else 0.0 for v in VENUES}
    return Size(nx, ny, lev, binding)


def maintenance(nx_now: float, ny_now: float, rx: Rules, ry: Rules) -> float:
    """What a venue must keep: each leg's value now times its maintenance fraction."""
    return abs(nx_now) * rx.mmf + abs(ny_now) * ry.mmf


def room(equity_now: float, nx_now: float, ny_now: float, rx: Rules, ry: Rules) -> float:
    """How far a venue is from liquidation: (equity - maintenance) / maintenance. 0 = being liquidated."""
    mm = maintenance(nx_now, ny_now, rx, ry)
    return (equity_now - mm) / mm if mm > 0 else math.inf


def spread_to_liquidation(equity: float, nx: float, ny: float, rx: Rules, ry: Rules) -> float:
    """How far the two markets can move apart (X against Y, a fraction of X's dollars) before the losing venue is
    liquidated, for a position just opened: (equity - maintenance) / nx."""
    return (equity - maintenance(nx, ny, rx, ry)) / nx if nx > 0 else math.inf


def way_from(diff_x: float, diff_y: float) -> int:
    """Which way round pays the funding: diff = Lighter's rate - Arcus's (a fraction an hour, + = longs pay). With
    way +1 the bot is short X on Lighter and short Y on Arcus, so it earns diff_x - diff_y an hour on each dollar."""
    return 1 if diff_x - diff_y >= 0 else -1


@dataclass(frozen=True)
class Forecast:
    way: int                                     # the way round the funding should pay
    edge: float                                  # what that way should earn an hour on each dollar of each market
    rates: dict[tuple[str, str], float]          # (venue, market): its hourly rate expected in the hold, + = longs pay


def forecast(history: dict[tuple[str, str], dict[int, float]], x: str, y: str, start: float, hold_h: float,
             is_open: Callable[[float], bool]) -> Forecast | None:
    """Which way round the funding should pay over a hold from `start`, from each venue's hourly funding history
    ({(venue, market): {hour start: rate}}). Each hour of the hold is expected to pay what that venue paid on average
    in the same kind of hour over the history: while the stock market is open (`is_open`) or while it is closed, when
    Arcus charges every market one fixed rate. So a hold over a weekend is judged on weekend hours. None when a
    venue has no history for a market."""
    hours = [(int(start) // 3600 + 1 + k) * 3600 for k in range(max(1, round(hold_h)))]
    rates: dict[tuple[str, str], float] = {}
    for v in VENUES:
        for sym in (x, y):
            h = history.get((v, sym)) or {}
            if not h:
                return None
            by: dict[bool, list[float]] = {True: [], False: []}
            for t, r in h.items():
                by[is_open(t)].append(r)
            every = sum(h.values()) / len(h)
            av = {k: sum(rs) / len(rs) if rs else every for k, rs in by.items()}
            rates[(v, sym)] = sum(av[is_open(t)] for t in hours) / len(hours)
    dx = rates[(LIGHTER, x)] - rates[(ARCUS, x)]
    dy = rates[(LIGHTER, y)] - rates[(ARCUS, y)]
    way = way_from(dx, dy)
    return Forecast(way, way * (dx - dy), rates)


@dataclass(frozen=True)
class Transfer:
    amount: float          # dollars to move, rounded down to the cent
    source: str            # the venue to take it from
    target: str
    share: float           # the poorer venue's share of the money before the move (0.5 = even)

    def text(self, names: dict[str, str] | None = None) -> str:
        n = names or {}
        if self.amount <= 0:
            return "The money is even on both venues: nothing to move."
        return (f"MOVE ${self.amount:,.2f} from {n.get(self.source, self.source.title())} to "
                f"{n.get(self.target, self.target.title())} (the poorer venue holds {self.share:.0%} of the money "
                f"now; after the move both hold the same)")


def transfer(equity: dict[str, float], min_usd: float = 0.01) -> Transfer:
    """What to move so both venues hold the same dollars."""
    a, b = equity.get(ARCUS, 0.0), equity.get(LIGHTER, 0.0)
    total = a + b
    amt = math.floor(abs(a - b) / 2 * 100) / 100
    src, dst = (ARCUS, LIGHTER) if a > b else (LIGHTER, ARCUS)
    share = min(a, b) / total if total > 0 else 0.5
    return Transfer(amt if amt >= min_usd else 0.0, src, dst, share)
