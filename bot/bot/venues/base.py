"""Normalized venue models.

The Arcus adapter and the paper venue both speak these types, so strategies, the order manager and the risk engine
never see venue JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from bot.common.decimal import round_price, tick_for_price


class Venue(StrEnum):
    ARCUS = "arcus"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


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
    tick_size: Decimal  # Arcus top-level tickSize (signing divisor)
    tick_tiers: tuple[TickTier, ...]
    step_size: Decimal
    min_notional: Decimal
    min_size: Decimal
    max_size: Decimal | None
    imf: Decimal
    mmf: Decimal
    offhours_imf: Decimal | None
    rth: tuple[int, int, str] | None  # (start_sec, end_sec, tz) or None for 24/7
    maker_fee: Decimal  # fraction of notional (negative = rebate)
    taker_fee: Decimal
    oi_cap_usd: Decimal | None
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
