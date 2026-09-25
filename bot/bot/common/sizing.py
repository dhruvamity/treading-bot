"""Capital-based sizing, shared by the backtest (scout), the pilot's session files, the live engine and the doctor.

Every dollar figure is a fixed share of the capital the bot trades with, so the same setup works on $20 or $20,000:
- at leverage L the position may reach capital x L (Arcus's limit); the inventory cap is capital x L / 1.25 (the risk
  engine's hard cap is 1.25x it) and each order is half the cap. RWA perps outside the underlying's session use the
  off-hours leverage (their initial margin is 1.5x), which shrinks the cap and the orders;
- the stops are percentages of that capital (config/app.yaml `sizing`): an open position down 1% is closed, a day
  down 2% stops until 00:00 UTC, equity 10% below its peak flattens and stops.

Two limits bound the sizes:
- floor (min_capital): the smallest order, the off-hours one, must stay at least 1.2x the Arcus minimum order
  (max($5, minimum size x price)). Below that the venue minimum, not the capital, would set the size;
- ceiling (order_max): one order never exceeds what the market's takers trade (the 99th percentile of taker-order
  notional, measured by the scout). Past it the sizes stop growing and the stops are taken on the capital actually
  used, which is then less than the capital given; the rest is idle margin.

Capital is rounded down to a fixed series (bucket: about 20 steps per decade), so the backtest and the live bot size
from exactly the same number, and cached backtests stay valid while equity moves a little.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.common.config import MMSession, SizingCfg

INV_BUFFER = 1.25    # inventory cap = capital x leverage / 1.25; order = cap / 2
MIN_ORDER_X = 1.2    # every order at least 1.2x the venue minimum (bot/strategies/quoting.py)
COVER_X = 1.25       # live sizes may exceed the last backtested capital by at most this factor
STEPS = (1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8, 3.2, 3.6, 4.0, 4.5, 5.0, 5.6, 6.3, 7.1, 8.0, 9.0)


@dataclass(frozen=True)
class Pct:
    """Stops and GO thresholds, in % of the capital the sizes use."""

    position_stop: float = 1.0   # the open position is down this much: close it (maker, then taker), cool down
    daily_stop: float = 2.0      # the day is down this much: close, no new orders until 00:00 UTC
    kill: float = 10.0           # equity this far below its peak: flatten and stop until a manual resume
    go_pnl_day: float = 0.25     # scout GO: average day (and the last 24 h) not worse than -this
    go_tail_pnl: float = 0.50    # scout GO: the last 6 h not worse than -this


@dataclass(frozen=True)
class Sizes:
    capital: float      # the capital the sizes and stops are taken on (below the capital given if order_max binds)
    order: float
    cap: float
    cap_off: float
    pos_stop: float
    daily_stop: float
    kill: float


def bucket(x: float) -> float:
    """Round down to the series 1, 1.1, 1.25 ... 9 x 10^k (never above x)."""
    if not x or x <= 0 or not math.isfinite(x):
        return 0.0
    e = math.floor(math.log10(x))
    if 10.0 ** (e + 1) <= x:
        e += 1
    elif 10.0 ** e > x:
        e -= 1
    m = x / 10.0 ** e
    step = max(s for s in STEPS if s <= m * (1 + 1e-9))
    return round(step * 10.0 ** e, 6)


def sizes(capital: float, lev: float, lev_off: float | None = None, *, pct: Pct | None = None,
          order_max: float | None = None) -> Sizes:
    pct = pct or Pct()
    lev_off = lev if lev_off is None else min(lev_off, lev)
    used = capital
    if order_max and lev > 0 and capital * lev / (2 * INV_BUFFER) > order_max:
        used = order_max * 2 * INV_BUFFER / lev
    cap = used * lev / INV_BUFFER
    return Sizes(used, cap / 2, cap, used * lev_off / INV_BUFFER, used * pct.position_stop / 100,
                 used * pct.daily_stop / 100, used * pct.kill / 100)


def venue_min_usd(min_notional: float, min_size: float, price: float) -> float:
    return max(min_notional, min_size * price)


def min_capital(venue_min: float, lev_off: float) -> float:
    """The smallest capital whose smallest order (off-hours on RWA perps) is still 1.2x the venue minimum."""
    return MIN_ORDER_X * venue_min * 2 * INV_BUFFER / lev_off if lev_off > 0 else math.inf


def target_capital(equity: float, *, frac: float = 1.0, max_capital: float | None = None,
                   covered: float | None = None) -> float:
    """The capital to size from: equity x frac, at most max_capital and COVER_X x the last backtested capital, then
    bucketed."""
    c = equity * frac
    if max_capital:
        c = min(c, max_capital)
    if covered:
        c = min(c, covered * COVER_X)
    return bucket(c)


def apply(s: MMSession, capital: float, z: SizingCfg | None = None) -> Sizes:
    """Rewrite a session's dollar sizes and stops for `capital`, from its `sizing` recipe (or `z`)."""
    z = z or s.sizing
    assert z is not None
    pct = Pct(z.position_stop_pct, z.daily_stop_pct, z.kill_pct)
    out = sizes(capital, z.leverage, z.leverage_off, pct=pct, order_max=z.order_max_usd)
    s.capital_usd = round(out.capital, 2)
    s.order_size_usd = round(out.order, 2)
    s.inventory_cap_usd = round(out.cap, 2)
    s.inventory_cap_off_usd = round(out.cap_off, 2) if z.leverage_off is not None and z.leverage_off < z.leverage \
        else None
    s.pos_stop_usd = round(out.pos_stop, 4)
    s.daily_stop_usd = round(out.daily_stop, 4)
    s.kill_usd = round(out.kill, 4)
    s.stop_loss_pct = z.kill_pct
    return out
