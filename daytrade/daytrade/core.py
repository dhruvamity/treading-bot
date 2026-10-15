"""What the three strategies share: markets, costs, orders and the strategy interface.

A strategy sees one trading day at a time (`Day`), one closed one-minute bar after another, and answers with actions:
an `Order` to enter, `Cancel`, `Exit` or `MoveStop`. The backtest (sim.py) and the bot (bot.py) feed it the same way;
only who fills the orders differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np

from .bars import LONDON, NY


@dataclass(frozen=True)
class Venue:
    """Costs on one venue, in basis points of the traded notional, one side."""
    name: str
    max_lev: float          # the venue's highest leverage on this market
    taker_bp: float         # fee + half the usual spread + a little depth, for a market order
    stop_bp: float          # a stop's market order: taker_bp + slippage while the price runs
    maker_bp: float = 0.0   # a resting limit order that fills
    funding_bp_h: float = 0.05   # charged per hour held, whatever the side (both venues pay hourly; small)


@dataclass(frozen=True)
class Market:
    symbol: str
    tz: ZoneInfo            # the clock its sessions are read on
    tick: float
    data: str               # the reference history in data/bars/
    venues: dict[str, Venue]
    venue_data: dict[str, str] = field(default_factory=dict)   # the venue's own price history


# Spreads measured on the recorded books (Lighter 24-27 Sep 2026, Arcus 24 Sep-3 Oct 2026): Lighter SPY 0.3 bp,
# QQQ 0.5, XAU 0.1; Arcus SPY 0.1, QQQ 0.9. Lighter charges no fee; Arcus 2.25 bp to a taker, nothing to a maker.
MARKETS = {
    "SPY": Market("SPY", NY, 0.01, "alpaca_SPY",
                  {"lighter": Venue("lighter", 50, 0.65, 1.15), "arcus": Venue("arcus", 50, 2.85, 3.35)},
                  {"lighter": "lighter_SPY", "arcus": "arcus_SPY"}),
    "QQQ": Market("QQQ", NY, 0.01, "alpaca_QQQ",
                  {"lighter": Venue("lighter", 50, 0.75, 1.25), "arcus": Venue("arcus", 25, 3.2, 3.7)},
                  {"lighter": "lighter_QQQ", "arcus": "arcus_QQQ"}),
    "XAU": Market("XAU", LONDON, 0.01, "duka_XAUUSD",
                  {"lighter": Venue("lighter", 25, 0.55, 1.05)},
                  {"lighter": "lighter_XAU"}),
}


@dataclass
class Order:
    side: int                       # +1 buy, -1 sell
    kind: str                       # "market" (next bar's open) or "limit" (rests at `price`)
    price: float                    # the limit price; for a market order the price it was decided at
    stop: float
    target: float | None            # None: no take profit (time exit or the strategy's own Exit)
    expire: int                     # UTC second: an unfilled order is cancelled
    flat_at: int                    # UTC second: an open position is closed at the bar before it
    tag: str = ""
    partial: float | None = None    # first take profit for `partial_frac` of the size; the stop then goes to entry
    partial_frac: float = 0.5
    cancel_beyond: float | None = None   # an unfilled limit is dropped once the price trades here


@dataclass
class Cancel:
    reason: str = ""


@dataclass
class Exit:
    reason: str = "signal"


@dataclass
class MoveStop:
    price: float


Action = Order | Cancel | Exit | MoveStop


@dataclass
class Position:
    side: int
    entry: float
    stop: float
    stop0: float
    target: float | None
    t_entry: int
    order: Order
    left: float = 1.0               # share of the size still open
    partial_done: bool = False


@dataclass
class Day:
    """One trading day as a strategy sees it: its one-minute bars and what was known before it began."""
    key: int                        # the local day number (days since 1970 on the market's clock)
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray                   # noqa: E741
    c: np.ndarray
    v: np.ndarray
    ctx: dict                       # levels and indicators, made by the strategy's own `prepare`

    def __len__(self) -> int:
        return len(self.t)


class Strategy:
    """Subclasses set `name`, `market`, `tf` (signal bars in minutes) and implement `days` and `on_bar`."""
    name = "base"
    market = "SPY"
    tf = 5
    max_trades = 1

    def days(self, bars, start: int | None = None, end: int | None = None) -> list[Day]:
        raise NotImplementedError

    def start_day(self, day: Day) -> None:
        self.day = day

    def on_bar(self, k: int, pos: Position | None, trades_today: int) -> list[Action]:
        raise NotImplementedError
