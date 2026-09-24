"""Arcus JSON -> normalized models. Field names follow the live API (2026-09-23 fixtures in tests/fixtures/live)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.common.decimal import D
from bot.common.errors import (
    AuthError,
    GeoRestricted,
    OrderRejected,
    RateLimited,
    TransmissionError,
    VenueError,
)
from bot.common.logging import Log
from bot.venues.base import (
    TIF,
    AccountState,
    Fill,
    FundingPayment,
    Market,
    OrderState,
    OrderStatus,
    Position,
    PublicTrade,
    Side,
    TickTier,
    Venue,
)
from bot.venues.symbols import canonical_base

V = Venue.ARCUS


def _opt(x: Any) -> Decimal | None:
    return None if x in (None, "") else D(x)


def parse_market(m: dict[str, Any], *, maker_fee: Decimal, taker_fee: Decimal) -> Market:
    tiers = tuple(TickTier(tick=D(t["tick"]), up_to_price=_opt(t.get("upToPrice"))) for t in m.get("tickTiers") or [])
    rth = m.get("regularTradingHours")
    imf = D(m["initialMarginFraction"])
    return Market(
        venue=V,
        base=canonical_base(V, m["marketDisplayName"]),
        venue_symbol=m["marketDisplayName"],
        venue_market_id=int(m["marketId"]),
        status=m.get("status", "OFFLINE"),
        category=m.get("category", ""),
        tick_size=D(m["tickSize"]),
        tick_tiers=tiers,
        step_size=D(m["stepSize"]),
        min_notional=D(m.get("minOrderNotional") or "5"),
        min_size=D(m.get("minOrderSize") or m["stepSize"]),
        max_size=_opt(m.get("maxOrderSize")),
        imf=imf,
        mmf=D(m["maintenanceMarginFraction"]),
        offhours_imf=_opt(m.get("offHoursInitialMarginFraction")),
        close_out_mf=None,
        rth=(int(rth["startSecondsOfDay"]), int(rth["endSecondsOfDay"]), rth["timezone"]) if rth else None,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        oi_cap_usd=_opt(m.get("openInterestCapNotional")),
        is_outside_rth=bool(m.get("isOutsideRth")),
        max_leverage=(Decimal(1) / imf) if imf > 0 else None,
        extra={
            "oraclePrice": m.get("oraclePrice"),
            "markPrice": m.get("markPrice"),
            "fundingRate": m.get("fundingRate"),
            "nextFundingRate": m.get("nextFundingRate"),
            "nextFundingAt": m.get("nextFundingAt"),
            "openInterest": m.get("openInterest"),
            "volume24hNotional": m.get("volume24hNotional"),
            "currentSettlementPrice": m.get("currentSettlementPrice"),
            "upperTradingBound": m.get("upperTradingBound"),
            "lowerTradingBound": m.get("lowerTradingBound"),
            "nextUpperTradingBound": m.get("nextUpperTradingBound"),
            "nextLowerTradingBound": m.get("nextLowerTradingBound"),
            "isUpperInExpansionZone": m.get("isUpperInExpansionZone"),
            "isLowerInExpansionZone": m.get("isLowerInExpansionZone"),
            "pythId": m.get("pythId"),
        },
    )


def base_fee_tier(feetiers: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """(maker, taker) as fractions for the base tier (level 0). ppm -> fraction."""
    tiers = sorted(feetiers["tiers"], key=lambda t: t["level"])
    t0 = tiers[0]
    return D(t0["maker_fee_ppm"]) / Decimal(1_000_000), D(t0["taker_fee_ppm"]) / Decimal(1_000_000)


def fee_tier_for_volume(feetiers: dict[str, Any], volume_30d_usd: Decimal) -> tuple[str, Decimal, Decimal]:
    """`volume_threshold` is USD x 1e9."""
    best = None
    for t in sorted(feetiers["tiers"], key=lambda t: t["level"]):
        if volume_30d_usd * Decimal(10**9) >= D(t["volume_threshold"]):
            best = t
    assert best is not None
    return best["name"], D(best["maker_fee_ppm"]) / Decimal(1_000_000), D(best["taker_fee_ppm"]) / Decimal(1_000_000)


log = Log("arcus.models")

_STATUS = {  # every status in the docs' order schema; anything new is logged and treated as not-yet-confirmed
    "ACK": OrderStatus.PENDING_NEW,
    "PENDING": OrderStatus.PENDING_NEW,
    "PLACED": OrderStatus.OPEN,
    "OPEN": OrderStatus.OPEN,
    "UNTRIGGERED": OrderStatus.OPEN,
    "TPSL_PLACED": OrderStatus.OPEN,
    "TPSL_TRIGGERED": OrderStatus.OPEN,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "CANCEL_PENDING": OrderStatus.OPEN,           # still on the book until CANCELED arrives
    "CANCEL_ACKNOWLEDGED": OrderStatus.OPEN,
    "CANCEL_ALL_ACKNOWLEDGED": OrderStatus.OPEN,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "MARGIN_CANCELED": OrderStatus.CANCELED,
    "TPSL_CANCELED": OrderStatus.CANCELED,
    "EXPIRED": OrderStatus.EXPIRED,
    "LIQUIDATED": OrderStatus.FILLED,
    "ADL": OrderStatus.FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "ERROR": OrderStatus.REJECTED,
}
MODIFY_CANCELED = "MODIFY_CANCELED"


def parse_order(o: dict[str, Any], base_by_id: dict[int, str], ts_us: int | None = None) -> OrderState:
    status_raw = str(o.get("status") or o.get("state") or "PENDING")
    status = _STATUS.get(status_raw)
    if status is None:
        log.warning("unknown_order_status", data={"status": status_raw, "orderId": o.get("orderId")})
        status = OrderStatus.PENDING_NEW
    if status is OrderStatus.CANCELED and o.get("cancelReason") == MODIFY_CANCELED:
        # Cancel-replace modify: the SAME order id is re-placed by the very next update (PLACED / fill / REJECTED).
        # Treating this leg as terminal would forget a live order and let the strategy place a duplicate.
        status = OrderStatus.PENDING_NEW
    orig = _opt(o.get("originalSize") or o.get("quantity"))
    rem = _opt(o.get("remainingSize"))
    filled = _opt(o.get("filledSize"))
    if filled is None and orig is not None and rem is not None:
        filled = orig - rem
    if status is OrderStatus.OPEN and filled and filled > 0 and rem and rem > 0:
        status = OrderStatus.PARTIALLY_FILLED
    tif = str(o.get("timeInForce") or "")
    tif_n = {"ALO": TIF.POST_ONLY, "IOC": TIF.IOC, "FOK": TIF.FOK, "GTT": TIF.GTT, "GTC": TIF.GTT}.get(tif)
    mid = o.get("marketId")
    return OrderState(
        client_id=o.get("clientId") or "",
        venue_order_id=o.get("orderId"),
        status=status,
        filled_size=filled or Decimal(0),
        avg_fill_price=_opt(o.get("avgFillPrice")),
        reject_reason=o.get("rejectionReason") or o.get("error"),
        ts_us=int(o.get("updatedAt") or o.get("updateTime") or o.get("createdAt") or ts_us or 0),
        venue=V,
        base=base_by_id.get(int(mid), "") if mid is not None else "",
        side=Side.BUY if o.get("side") == "BUY" else Side.SELL if o.get("side") == "SELL" else None,
        price=_opt(o.get("price")),
        size=orig,
        tif=tif_n,
        reduce_only=bool(o.get("reduceOnly")),
    )


def parse_fill(f: dict[str, Any], base_by_id: dict[int, str]) -> Fill:
    return Fill(
        venue=V,
        base=base_by_id.get(int(f["marketId"]), f.get("marketDisplayName", "")),
        client_id=f.get("clientId") or "",
        side=Side.BUY if f["side"] == "BUY" else Side.SELL,
        price=D(f["price"]),
        size=D(f["size"]),
        fee=D(f.get("fee") or 0),
        is_maker=f.get("role") == "MAKER",
        liquidation=bool(f.get("liquidation")),
        ts_us=int(f["createdAt"]),
        trade_id=str(f["tradeId"]),
        venue_order_id=f.get("orderId"),
    )


def parse_position(p: dict[str, Any], base_by_id: dict[int, str]) -> Position:
    size = D(p.get("size") or 0)
    side = str(p.get("side") or "").upper()
    if side in ("SHORT", "SELL") and size > 0:
        size = -size
    return Position(
        venue=V,
        base=base_by_id.get(int(p["marketId"]), p.get("marketDisplayName", "")),
        size=size,
        entry_price=D(p.get("averageEntryPrice") or 0),
        mark_price=D(p.get("markPx") or 0),
        unrealized_pnl=D(p.get("unrealizedPnl") or 0),
        margin_mode=str(p.get("marginMode") or "CROSS").lower(),
        liq_price=_opt(p.get("liquidationPrice")),
    )


def parse_account(a: dict[str, Any], ts_us: int) -> AccountState:
    equity = D(a.get("equity") or 0)
    free = D(a.get("freeCollateral") or 0)
    positions = a.get("positions") or {}
    mm = sum((D(p.get("marginUsed") or 0) for p in positions.values()), Decimal(0))
    return AccountState(venue=V, equity=equity, free_collateral=free, maintenance_margin=Decimal(0),
                        initial_margin=max(Decimal(0), equity - free) if equity else mm, ts_us=ts_us)


def parse_funding_payment(f: dict[str, Any], base_by_id: dict[int, str]) -> FundingPayment:
    return FundingPayment(venue=V, base=base_by_id.get(int(f["marketId"]), f.get("marketDisplayName", "")),
                          ts_us=int(f["time"]), rate_h=D(f["fundingRate"]), position_size=D(f["size"]),
                          payment=D(f["payment"]))


def parse_public_trade(t: dict[str, Any]) -> PublicTrade:
    # Trade `side` is the TAKER side on the public stream (maker/taker addresses are both present).
    return PublicTrade(
        venue=V,
        base=canonical_base(V, t["marketDisplayName"]),
        ts_us=int(t["timestamp"]),
        price=D(t["price"]),
        size=D(t["size"]),
        taker_side=Side.BUY if t["side"] == "BUY" else Side.SELL,
        trade_id=str(t["tradeId"]),
        maker_address=t.get("makerAddress"),
        taker_address=t.get("takerAddress"),
        maker_order_id=t.get("makerOrderId"),
        seq=t.get("sequenceNumber"),
    )


def raise_for_error(status: int, body: Any, *, client_id: str | None = None) -> None:
    """Map an Arcus error response to a typed exception."""
    if status < 400 and not (isinstance(body, dict) and body.get("status") in ("REJECTED", "ERROR")):
        return
    b = body if isinstance(body, dict) else {"error": str(body)}
    msg = str(b.get("error") or b.get("message") or b)
    if status == 429:
        raise RateLimited("arcus", msg, reason=b.get("reason"), retry_after_ms=int(b.get("retryAfterMs") or 1000))
    if b.get("code") == "GEO_RESTRICTED":
        raise GeoRestricted("arcus", msg, status=status)
    et = b.get("errorType")
    if et == "Transmission":
        raise TransmissionError("arcus", msg, status=status, retryable=True)
    if status in (401, 403) or et in ("Unauthorized", "Forbidden"):
        raise AuthError("arcus", msg, status=status)
    reason = b.get("rejectionReason") or et
    if reason:
        raise OrderRejected("arcus", str(reason), msg, client_id=client_id or b.get("clientId"))
    raise VenueError("arcus", msg, status=status, retryable=status >= 500)
