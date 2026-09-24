"""Strategy interface (Appendix E2): pure functions of (context + own state) -> desired book.

The SAME strategy objects run in the simulator, the paper adapter and live. They never call venues: they return a
StrategyOutput (desired orders per venue/market, IOC hedge/cut intents, a human-readable reason, metrics) and
receive fills through on_fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from bot.core.order_manager import DesiredOrder
from bot.venues.base import Fill, Market, OrderRequest, Venue

if TYPE_CHECKING:
    from bot.core.marketdata import MarketView


@dataclass
class StrategyContext:
    now_us: int
    venue: Venue
    market: Market
    view: MarketView
    params: Any  # MMSession | DNSession
    inventory: Decimal = Decimal(0)  # signed base units on this venue/market
    entry_price: Decimal | None = None  # average entry of `inventory`
    other_market: Market | None = None
    other_view: MarketView | None = None
    other_inventory: Decimal = Decimal(0)
    session_progress: float = 0.0
    quoting_allowed: bool = True
    quoting_block_reason: str = ""
    event_window: bool = False
    off_hours: bool = False  # Arcus RWA outside RTH
    hysteresis_mult: float = 1.0
    budget_fills_per_hour_cap: float | None = None
    our_fill_usd_5m: float = 0.0
    features: Any = None  # autopilot Features, when available
    regime: str | None = None
    other_venue_healthy: bool = True
    other_venue_down_s: float = 0.0
    equity_usd: Decimal = Decimal(0)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def mid(self) -> Decimal | None:
        return self.view.mid()


@dataclass
class StrategyOutput:
    desired: dict[tuple[Venue, str], list[DesiredOrder]] = field(default_factory=dict)
    hedge_intents: list[OrderRequest] = field(default_factory=list)
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    half_spread_ticks: float = 0.0  # feeds the order manager's requote tolerance (0.25 x h)

    def set(self, venue: Venue, base: str, orders: list[DesiredOrder]) -> None:
        self.desired[(venue, base)] = orders


@dataclass(frozen=True, slots=True)
class SessionEvent:
    kind: str  # rth_open | rth_close | funding_tick | band_change | session_start | session_end
    ts_us: int
    data: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    name: str

    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_tick(self, ctx: StrategyContext) -> StrategyOutput: ...
    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None: ...
    def on_session_event(self, ctx: StrategyContext, ev: SessionEvent) -> None: ...
    def on_stop(self, ctx: StrategyContext) -> StrategyOutput: ...
