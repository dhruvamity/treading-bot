"""Sizes from the capital: the same numbers in the backtest, the session file and the running bot.

At leverage L the position may reach capital x L (Lighter's limit for the initial margin fraction 1/L). The inventory
cap is capital x L / 1.25, so a burst of fills never runs into Lighter's own limit, and each order is half the cap.
The stops are percentages of the capital. Two bounds:
- floor: every order at least 1.2x Lighter's minimum order (max($10, minimum size x price) on most markets);
- ceiling: one order never above what the market's takers trade (the 99th percentile taker order, from the tape).
  Past it the sizes stop growing and the stops are taken on the capital actually used.
Capital is rounded down to a fixed series (about 20 steps a decade) so a backtest and a run size from the same number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

INV_BUFFER = 1.25
MIN_ORDER_X = 1.2
STEPS = (1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8, 3.2, 3.6, 4.0, 4.5, 5.0, 5.6, 6.3, 7.1, 8.0, 9.0)


@dataclass(frozen=True)
class Stops:
    """Percent of the capital."""

    position: float = 2.0
    daily: float = 5.0
    kill: float = 25.0


@dataclass(frozen=True)
class Sizes:
    capital: float        # the capital the sizes and stops are taken on (less than given when the ceiling binds)
    leverage: float
    order_usd: float
    cap_usd: float
    pos_stop_usd: float
    daily_stop_usd: float
    kill_usd: float

    def label(self) -> str:
        return (f"${self.capital:,.0f} at {self.leverage:g}x: orders ${self.order_usd:,.0f}, cap ${self.cap_usd:,.0f}, "
                f"stops ${self.pos_stop_usd:,.2f} / ${self.daily_stop_usd:,.2f} / ${self.kill_usd:,.2f}")


def bucket(x: float) -> float:
    """Round down to 1, 1.1, 1.25 ... 9 x 10^k."""
    if not x or x <= 0 or not math.isfinite(x):
        return 0.0
    e = math.floor(math.log10(x))
    if 10.0 ** (e + 1) <= x:
        e += 1
    elif 10.0 ** e > x:
        e -= 1
    m = x / 10.0 ** e
    return round(max(s for s in STEPS if s <= m * (1 + 1e-9)) * 10.0 ** e, 6)


def sizes(capital: float, leverage: float, stops: Stops | None = None, *, order_max: float | None = None) -> Sizes:
    st = stops or Stops()
    used = capital
    if order_max and leverage > 0 and capital * leverage / (2 * INV_BUFFER) > order_max:
        used = order_max * 2 * INV_BUFFER / leverage
    cap = used * leverage / INV_BUFFER
    return Sizes(used, leverage, cap / 2, cap, used * st.position / 100, used * st.daily / 100, used * st.kill / 100)


def min_capital(min_order_usd: float, leverage: float) -> float:
    """The least capital whose order is still 1.2x Lighter's minimum order."""
    return MIN_ORDER_X * min_order_usd * 2 * INV_BUFFER / leverage if leverage > 0 else math.inf


def target_capital(equity: float, *, frac: float = 1.0, max_capital: float | None = None) -> float:
    c = equity * frac
    if max_capital:
        c = min(c, max_capital)
    return bucket(c)
