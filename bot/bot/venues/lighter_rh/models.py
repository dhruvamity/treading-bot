"""Lighter RH JSON -> normalized models (docs: apidocs.rh.lighter.xyz websocket + reference; live fixtures).

Units: margin fractions in 1/10,000; fees on trades in 1e-6 of notional; `fundings.rate` in PERCENT rounded to
4 decimals (use value / index for the precise hourly fraction); REST timestamps in seconds, candle `t` in ms,
`transaction_time` in µs, WS book `last_updated_at` in µs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.common.decimal import D
from bot.common.errors import AuthError, OrderRejected, RateLimited, VenueError
from bot.venues.base import (
    TIF,
    Fill,
    Market,
    OrderState,
    OrderStatus,
    Position,
    PublicTrade,
    Side,
    Venue,
)
from bot.venues.symbols import canonical_base

V = Venue.LIGHTER_RH
TEN_K = Decimal(10_000)
MILLION = Decimal(1_000_000)


def parse_market(ob: dict[str, Any], details: dict[str, Any] | None) -> Market:
    """`ob` from /orderBooks, `details` from /orderBookDetails (margins, funding params)."""
    d = details or {}
    sd = int(d.get("size_decimals", ob["supported_size_decimals"]))
    pd = int(d.get("price_decimals", ob["supported_price_decimals"]))
    min_imf = D(d["min_initial_margin_fraction"]) / TEN_K if "min_initial_margin_fraction" in d else Decimal("0.02")
    mmf = D(d["maintenance_margin_fraction"]) / TEN_K if "maintenance_margin_fraction" in d else Decimal("0.012")
    cmf = D(d["closeout_margin_fraction"]) / TEN_K if "closeout_margin_fraction" in d else None
    trading_hours = (d.get("market_config") or {}).get("trading_hours") or ""
    return Market(
        venue=V,
        base=canonical_base(V, ob["symbol"]),
        venue_symbol=ob["symbol"],
        venue_market_id=int(ob["market_id"]),
        status=ob.get("status", "inactive"),
        category=ob.get("market_type", "perp"),
        tick_size=Decimal(1).scaleb(-pd),
        tick_tiers=(),
        step_size=Decimal(1).scaleb(-sd),
        min_notional=D(ob.get("min_quote_amount") or "10"),
        min_size=D(ob.get("min_base_amount") or "0"),
        max_size=None,
        imf=min_imf,
        mmf=mmf,
        offhours_imf=None,
        close_out_mf=cmf,
        rth=None,
        maker_fee=D(ob.get("maker_fee") or 0) / 100,  # orderBooks fees are in PERCENT
        taker_fee=D(ob.get("taker_fee") or 0) / 100,
        oi_cap_usd=None,
        size_decimals=sd,
        price_decimals=pd,
        multiplier=D(ob.get("multiplier") or 1),
        liquidation_fee=D(ob.get("liquidation_fee") or 0) / 100,
        max_leverage=(Decimal(1) / min_imf) if min_imf > 0 else None,
        extra={
            "market_type": ob.get("market_type"),
            "default_imf": d.get("default_initial_margin_fraction"),
            "base_interest_rate_pct_8h": d.get("base_interest_rate"),
            "funding_clamp_small_pct": d.get("funding_clamp_small"),
            "funding_clamp_big_pct": d.get("funding_clamp_big"),
            "funding_premium_multiplier": d.get("funding_premium_multiplier"),
            "trading_hours": trading_hours,
            "force_reduce_only": (d.get("market_config") or {}).get("force_reduce_only"),
            "mark_price": d.get("mark_price"),
            "index_price": d.get("index_price"),
            "open_interest": d.get("open_interest"),
            "daily_quote_volume": d.get("daily_quote_token_volume"),
        },
    )


def price_to_int(price: Decimal, m: Market) -> int:
    """Signer price integer = price x multiplier x 10^price_decimals (exact after rounding by the caller)."""
    assert m.price_decimals is not None
    v = price * m.multiplier * Decimal(10) ** m.price_decimals
    if v != v.to_integral_value():
        raise ValueError(f"price {price} not on Lighter tick for {m.base}")
    return int(v)


def size_to_int(size: Decimal, m: Market) -> int:
    assert m.size_decimals is not None
    v = size / m.multiplier * Decimal(10) ** m.size_decimals
    if v != v.to_integral_value():
        raise ValueError(f"size {size} not on Lighter step for {m.base}")
    return int(v)


_STATUS = {
    "in-progress": OrderStatus.PENDING_NEW,
    "pending": OrderStatus.PENDING_NEW,
    "open": OrderStatus.OPEN,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "canceled-expired": OrderStatus.EXPIRED,
}


def parse_order(o: dict[str, Any], base_by_id: dict[int, str], ts_us: int = 0) -> OrderState:
    st_raw = str(o.get("status", "open"))
    status = _STATUS.get(st_raw)
    reject = None
    if status is None:
        status = OrderStatus.CANCELED if st_raw.startswith("canceled") else OrderStatus.OPEN
        reject = st_raw if st_raw.startswith("canceled-") else None
    init = D(o.get("initial_base_amount") or 0)
    filled = D(o.get("filled_base_amount") or 0)
    if status is OrderStatus.OPEN and 0 < filled < init:
        status = OrderStatus.PARTIALLY_FILLED
    fq = D(o.get("filled_quote_amount") or 0)
    tif = {"post-only": TIF.POST_ONLY, "immediate-or-cancel": TIF.IOC, "good-till-time": TIF.GTT}.get(
        str(o.get("time_in_force")))
    is_ask = bool(o.get("is_ask"))
    return OrderState(
        client_id=str(o.get("client_order_index") or o.get("client_order_id") or ""),
        venue_order_id=str(o.get("order_index") or o.get("order_id") or ""),
        status=status,
        filled_size=filled,
        avg_fill_price=(fq / filled) if filled > 0 else None,
        reject_reason=reject,
        ts_us=int(o.get("transaction_time") or 0) or int(o.get("updated_at") or 0) * 1000 or ts_us,
        venue=V,
        base=base_by_id.get(int(o.get("market_index", -1)), ""),
        side=Side.SELL if is_ask else Side.BUY,
        price=D(o["price"]) if o.get("price") not in (None, "") else None,
        size=init,
        tif=tif,
        reduce_only=bool(o.get("reduce_only")),
    )


def parse_own_trade(t: dict[str, Any], account_index: int, base_by_id: dict[int, str]) -> Fill:
    is_bid = int(t.get("bid_account_id", -1)) == account_index
    side = Side.BUY if is_bid else Side.SELL
    maker_is_ask = bool(t.get("is_maker_ask"))
    is_maker = (maker_is_ask and not is_bid) or (not maker_is_ask and is_bid)
    fee_units = D(t.get("maker_fee" if is_maker else "taker_fee") or 0)
    usd = D(t.get("usd_amount") or 0)
    coi = t.get("bid_client_id" if is_bid else "ask_client_id") or 0
    oid = t.get("bid_id" if is_bid else "ask_id")
    ts = int(t.get("transaction_time") or 0) or int(t.get("timestamp") or 0) * 1000
    return Fill(
        venue=V,
        base=base_by_id.get(int(t["market_id"]), ""),
        client_id=str(coi) if coi else "",
        side=side,
        price=D(t["price"]),
        size=D(t["size"]),
        fee=usd * fee_units / MILLION,
        is_maker=is_maker,
        liquidation=t.get("type") in ("liquidation", "deleverage"),
        ts_us=ts,
        trade_id=str(t.get("trade_id_str") or t.get("trade_id")),
        venue_order_id=str(oid) if oid is not None else None,
    )


def parse_public_trade(t: dict[str, Any], base: str, *, liquidation: bool = False) -> PublicTrade:
    # Taker side: if the maker was the ask, the taker bought.
    taker_side = Side.BUY if t.get("is_maker_ask") else Side.SELL
    ts = int(t.get("transaction_time") or 0) or int(t.get("timestamp") or 0) * 1000
    return PublicTrade(
        venue=V, base=base, ts_us=ts, price=D(t["price"]), size=D(t["size"]), taker_side=taker_side,
        trade_id=str(t.get("trade_id_str") or t.get("trade_id")), seq=t.get("block_height"),
        maker_order_id=str(t.get("ask_id") if t.get("is_maker_ask") else t.get("bid_id")),
        is_liquidation=liquidation or t.get("type") in ("liquidation", "deleverage"),
    )


def parse_position(p: dict[str, Any], base_by_id: dict[int, str]) -> Position:
    size = D(p.get("position") or 0) * (1 if int(p.get("sign", 1)) >= 0 else -1)
    value = D(p.get("position_value") or 0)
    mark = (value / abs(size)) if size else D(p.get("avg_entry_price") or 0)
    liq = p.get("liquidation_price")
    return Position(
        venue=V,
        base=base_by_id.get(int(p["market_id"]), canonical_base(V, str(p.get("symbol", "")))),
        size=size,
        entry_price=D(p.get("avg_entry_price") or 0),
        mark_price=mark,
        unrealized_pnl=D(p.get("unrealized_pnl") or 0),
        margin_mode="isolated" if int(p.get("margin_mode", 0)) == 1 else "cross",
        liq_price=D(liq) if liq not in (None, "", "0") else None,
    )


def precise_funding_rate(value_per_unit: Decimal, index_price: Decimal) -> Decimal:
    """Hourly funding as a fraction: `value` (USD per 1 base unit) / index price. Sign applied by caller."""
    return value_per_unit / index_price


def funding_sign(direction: str) -> int:
    """'long' = longs pay (positive rate in our convention)."""
    return 1 if direction == "long" else -1


def raise_for_error(status: int, body: Any) -> None:
    code = body.get("code") if isinstance(body, dict) else None
    if status < 400 and (code in (None, 200, 0)):
        return
    msg = str(body.get("message") if isinstance(body, dict) else body)[:300]
    if status in (429, 405) or code in (23000, 23001, 23002, 23003):
        raise RateLimited("lighter_rh", msg, reason=str(status), retry_after_ms=60_000 if status == 405 else 1_000)
    if code in (21120, 21108, 21109, 21136) or status in (401, 403):
        raise AuthError("lighter_rh", msg, status=status)
    if isinstance(code, int) and 21000 <= code < 22000:
        raise OrderRejected("lighter_rh", str(code), msg)
    raise VenueError("lighter_rh", f"{status} {code}: {msg}", status=status, retryable=status >= 500)
