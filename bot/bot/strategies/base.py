"""Strategy interface: pure functions of (context + own state) -> desired book.

The SAME strategy objects run in paper and live. They never call venues: they return a StrategyOutput (desired orders
per venue/market, IOC exit/cut intents, a human-readable reason, metrics) and receive fills through on_fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from bot.core.order_manager import DesiredOrder
from bot.venues.base import Market, OrderRequest, Venue

if TYPE_CHECKING:
    from bot.core.marketdata import MarketView


@dataclass
class StrategyContext:
    now_us: int
    venue: Venue
    market: Market
    view: MarketView
    params: Any  # MMSession
    inventory: Decimal = Decimal(0)  # signed base units on this venue/market
    entry_price: Decimal | None = None  # average entry of `inventory`
    session_progress: float = 0.0
    quoting_allowed: bool = True
    quoting_block_reason: str = ""
    event_window: bool = False
    off_hours: bool = False  # Arcus RWA outside RTH
    our_fill_usd_5m: float = 0.0

    @property
    def mid(self) -> Decimal | None:
        return self.view.mid()


@dataclass
class StrategyOutput:
    desired: dict[tuple[Venue, str], list[DesiredOrder]] = field(default_factory=dict)
    ioc_intents: list[OrderRequest] = field(default_factory=list)   # reduce-only IOC exits and cuts
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    half_spread_ticks: float = 0.0  # feeds the order manager's requote tolerance (0.25 x h)

    def set(self, venue: Venue, base: str, orders: list[DesiredOrder]) -> None:
        self.desired[(venue, base)] = orders

