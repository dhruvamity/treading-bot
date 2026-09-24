"""Normalized venue models and the VenueAdapter protocol (prompt pack Appendix E2).

Every venue-specific adapter (Arcus, Lighter RH, paper, sim) speaks these types, so strategies, the order
manager and the risk engine never see venue JSON.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from bot.common.decimal import round_price, tick_for_price


class Venue(StrEnum):
    ARCUS = "arcus"
    LIGHTER_RH = "lighter_rh"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class TIF(StrEnum):
    GTT = "gtt"
    IOC = "ioc"
    FOK = "fok"
    POST_ONLY = "post_only"  # Arcus ALO == POST_ONLY


class OrderStatus(StrEnum):
    PENDING_NEW = "PENDING_NEW"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED)


@dataclass(frozen=True, slots=True)
class TickTier:
    tick: Decimal
    up_to_price: Decimal | None  # None = unbounded top band


@dataclass(frozen=True, slots=True)
class Market:
    venue: Venue
    base: str
    venue_symbol: str
    venue_market_id: int
    status: str
    category: str
    tick_size: Decimal  # Arcus top-level tickSize (signing divisor) / Lighter 10^-price_decimals
    tick_tiers: tuple[TickTier, ...]
    step_size: Decimal
    min_notional: Decimal
    min_size: Decimal
    max_size: Decimal | None
    imf: Decimal
    mmf: Decimal
    offhours_imf: Decimal | None
    close_out_mf: Decimal | None
    rth: tuple[int, int, str] | None  # (start_sec, end_sec, tz) or None for 24/7
    maker_fee: Decimal  # fraction of notional (negative = rebate)
    taker_fee: Decimal
    oi_cap_usd: Decimal | None
    size_decimals: int | None = None
    price_decimals: int | None = None
    multiplier: Decimal = Decimal(1)
    liquidation_fee: Decimal | None = None
    is_outside_rth: bool = False
    max_leverage: Decimal | None = None
    extra: dict[str, object] = field(default_factory=dict, compare=False, hash=False)

    @property
    def online(self) -> bool:
        return self.status.upper() in ("ONLINE", "ACTIVE")

    def tiers(self) -> list[tuple[Decimal, Decimal | None]]:
        return [(t.tick, t.up_to_price) for t in self.tick_tiers]

    def tick_at(self, price: Decimal) -> Decimal:
        return tick_for_price(price, self.tiers(), self.tick_size) if self.tick_tiers else self.tick_size

    def round_price(self, price: Decimal, *, is_bid: bool) -> Decimal:
        return round_price(price, is_bid=is_bid, tiers=self.tiers(), default_tick=self.tick_size)

    def price_to_ticks(self, price: Decimal) -> int:
        from bot.common.decimal import to_units_exact

        return to_units_exact(price, self.tick_size)

    def ticks_to_price(self, ticks: int) -> Decimal:
        return Decimal(ticks) * self.tick_size

    def size_to_quantums(self, size: Decimal) -> int:
        from bot.common.decimal import to_units_exact

        return to_units_exact(size, self.step_size)

    def quantums_to_size(self, q: int) -> Decimal:
        return Decimal(q) * self.step_size

    def effective_min_notional(self, price: Decimal) -> Decimal:
        """Venue minimum in USD, including the min-size floor at this price."""
        return max(self.min_notional, self.min_size * price)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    venue: Venue
    base: str
    side: Side
    price: Decimal
    size: Decimal
    tif: TIF
    reduce_only: bool = False
    client_id: str = ""
    tag: str = ""
    reason: str = ""
    account_index: int | None = None

    @property
    def notional(self) -> Decimal:
        return self.price * self.size


@dataclass(frozen=True, slots=True)
class OrderState:
    client_id: str
    venue_order_id: str | None
    status: OrderStatus
    filled_size: Decimal
    avg_fill_price: Decimal | None
    reject_reason: str | None
    ts_us: int
    venue: Venue | None = None
    base: str = ""
    side: Side | None = None
    price: Decimal | None = None
    size: Decimal | None = None
    tif: TIF | None = None
    reduce_only: bool = False
    tag: str = ""


@dataclass(frozen=True, slots=True)
class Fill:
    venue: Venue
    base: str
    client_id: str
    side: Side
    price: Decimal
    size: Decimal
    fee: Decimal  # positive = paid, negative = rebate (USD)
    is_maker: bool
    liquidation: bool
    ts_us: int
    trade_id: str
    venue_order_id: str | None = None
    tag: str = ""

    @property
    def notional(self) -> Decimal:
        return self.price * self.size


@dataclass(frozen=True, slots=True)
class Position:
    venue: Venue
    base: str
    size: Decimal  # signed, base units
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    margin_mode: str
    liq_price: Decimal | None

    @property
    def notional(self) -> Decimal:
        return abs(self.size) * self.mark_price


@dataclass(frozen=True, slots=True)
class AccountState:
    venue: Venue
    equity: Decimal
    free_collateral: Decimal
    maintenance_margin: Decimal
    initial_margin: Decimal
    ts_us: int


@dataclass(frozen=True, slots=True)
class FundingPayment:
    venue: Venue
    base: str
    ts_us: int
    rate_h: Decimal  # fraction per hour, + = longs pay
    position_size: Decimal
    payment: Decimal  # USD, + = received
    pay_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RateBudget:
    venue: Venue
    order_remaining: int | None
    order_cap: int | None
    cancel_remaining: int | None
    cancel_cap: int | None
    tx_per_min_remaining: int | None
    next_available_ms: int


@dataclass(frozen=True, slots=True)
class PublicTrade:
    venue: Venue
    base: str
    ts_us: int
    price: Decimal
    size: Decimal
    taker_side: Side
    trade_id: str
    maker_address: str | None = None
    taker_address: str | None = None
    maker_order_id: str | None = None
    seq: int | None = None
    is_liquidation: bool = False


@dataclass(frozen=True, slots=True)
class BBO:
    venue: Venue
    base: str
    ts_us: int
    bid_px: Decimal | None
    bid_sz: Decimal | None
    ask_px: Decimal | None
    ask_sz: Decimal | None


@runtime_checkable
class VenueAdapter(Protocol):
    venue: Venue

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def markets(self) -> Sequence[Market]: ...
    async def place(self, orders: Sequence[OrderRequest]) -> Sequence[OrderState]: ...
    async def modify(self, client_id: str, price: Decimal, size: Decimal) -> OrderState: ...
    async def cancel(self, client_ids: Sequence[str]) -> None: ...
    async def cancel_all(self, base: str | None = None) -> None: ...
    async def set_leverage(self, base: str, leverage: int, isolated: bool = False) -> None: ...
    async def arm_dead_mans_switch(self, deadline_us: int | None) -> None: ...
    async def positions(self) -> Sequence[Position]: ...
    async def open_orders(self) -> Sequence[OrderState]: ...
    async def balances(self) -> dict[str, Decimal]: ...
    def order_updates(self) -> AsyncIterator[OrderState]: ...
    def fills(self) -> AsyncIterator[Fill]: ...
    def budget(self) -> RateBudget: ...
    def health(self) -> dict[str, float]: ...
