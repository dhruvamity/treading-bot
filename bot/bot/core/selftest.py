"""`bot selftest`: prove the venue accepts every kind of signed request the bot sends, without trading.

It drives the production ArcusAdapter / ArcusRest (the exact code a live run uses):
  1. scheduleCancel arm, then disarm              (dead man's switch; signing scheme 2)
  2. cancelAllOrders for the market               (scheme 2)
  3. cancelOrder for an unknown clientId          (scheme 1, cancel)
  4. placeOrder: one post-only BUY at the minimum size, `offset_pct` below the bid   (scheme 1, place)
  5. modifyOrder: reprice it one tick lower       (scheme 1, modify)
  6. batchPlaceOrders: two more such orders       (scheme 1 per element)
  7. batchCancelOrders, then cancelOrder          (scheme 1)
  8. setLeverage to the value ALREADY in force    (scheme 2; changes nothing)
  9. afterwards: no open orders left
Order steps (4-7) run only on an UNFUNDED subaccount, where the engine rejects them UNDERCOLLATERALIZED after the
signature and payload checks, so nothing can fill; or with allow_funded (the CLI makes the owner type CONFIRM):
then the orders rest post-only below the market and are cancelled within seconds.
A step passes when the venue accepted the request: a 2xx, or a business rejection (UNDERCOLLATERALIZED, not found,
...). It fails on key/signature errors (401/403) and on payload errors (tick, invalid request).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Any

from bot.common.errors import (
    AuthError,
    BotError,
    GeoRestricted,
    OrderRejected,
    RateLimited,
    VenueError,
)
from bot.common.ids import ClientIdFactory
from bot.venues.base import TIF, Market, OrderRequest, OrderState, Side, Venue

BUSINESS_REJECTS = ("UNDERCOLLATERALIZED", "NOT_FOUND", "NOT FOUND", "ORDER_NOT_FOUND", "UNKNOWN ORDER",
                    "NO ACTIVITY", "INSUFFICIENT", "MARGIN")
PAYLOAD_ERRORS = ("TICK", "INVALIDREQUEST", "INVALID REQUEST", "NOT A MULTIPLE", "VALIDATION", "MALFORMED")


@dataclass
class Step:
    name: str
    level: str = "PASS"   # PASS | FAIL | SKIP | INFO
    detail: str = ""


@dataclass
class SelftestResult:
    steps: list[Step] = field(default_factory=list)
    events: list[OrderState] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(s.level == "FAIL" for s in self.steps)

    def render(self) -> str:
        out = [f"[{s.level:<4}] {s.name:<34} {s.detail}" for s in self.steps]
        out.append("")
        out.append("ALL SIGNED REQUESTS ACCEPTED" if self.ok else "SELFTEST FAILED: see FAIL lines above")
        return "\n".join(out)


def classify(exc: BaseException | None) -> tuple[str, str]:
    """(level, detail) for the outcome of one signed request."""
    if exc is None:
        return "PASS", "accepted"
    msg = str(exc)
    up = msg.upper()
    if isinstance(exc, AuthError):
        return "FAIL", f"key or signature rejected: {msg[:200]}"
    if isinstance(exc, GeoRestricted):
        return "FAIL", f"region blocked: {msg[:200]}"
    if isinstance(exc, RateLimited):
        return "FAIL", f"rate limited: {msg[:200]}"
    if any(k in up for k in PAYLOAD_ERRORS):
        return "FAIL", f"payload rejected (encoding bug): {msg[:200]}"
    if isinstance(exc, OrderRejected) or any(k in up for k in BUSINESS_REJECTS):
        return "PASS", f"signature accepted; business reject: {msg[:160]}"
    if isinstance(exc, VenueError):
        return "INFO", f"venue answered: {msg[:200]}"
    return "FAIL", f"{type(exc).__name__}: {msg[:200]}"


async def _do(result: SelftestResult, name: str, coro: Awaitable[Any]) -> Any:
    try:
        r = await coro
    except BotError as e:
        lvl, detail = classify(e)
        result.steps.append(Step(name, lvl, detail))
        return None
    result.steps.append(Step(name, "PASS", "accepted"))
    return r


def probe_order_size(m: Market, price: Decimal) -> Decimal:
    q = max(m.min_size, (m.min_notional * Decimal("1.05") / price / m.step_size).to_integral_value(ROUND_CEILING) * m.step_size)
    return q.quantize(m.step_size) if m.step_size.as_tuple().exponent < 0 else q  # type: ignore[operator]


async def run_selftest(adapter: Any, public_rest: Any, *, market: Market, funded: bool, allow_funded: bool,
                       offset_pct: float = 3.0, settle_s: float = 2.0) -> SelftestResult:
    res = SelftestResult()
    ad = adapter

    async def drain() -> None:
        while not ad._orders_q.empty():
            res.events.append(ad._orders_q.get_nowait())

    now_us = int(time.time() * 1e6)
    await _do(res, "dead man's switch: arm (+60 s)", ad.arm_dead_mans_switch(now_us + 60_000_000))
    await _do(res, "dead man's switch: disarm", ad.arm_dead_mans_switch(None))
    await _do(res, f"cancel-all ({market.venue_symbol})", ad.cancel_all(market.base))
    await _do(res, "cancel unknown clientId", ad.rest.cancel_order(ad.account_index, market.venue_market_id,
                                                                    client_id="alzzselftest0"))

    if funded and not allow_funded:
        res.steps.append(Step("order steps", "SKIP", "subaccount is funded; rerun with --allow-funded to include them"))
    else:
        bbo = await public_rest.bbo(market.venue_symbol)
        bid = Decimal(str((bbo.get("bestBid") or {}).get("price") or 0))
        if bid <= 0:
            res.steps.append(Step("order steps", "SKIP", "no bid on the book"))
        else:
            px = market.round_price(bid * (1 - Decimal(str(offset_pct)) / 100), is_bid=True)
            px = (px / market.tick_size).to_integral_value(ROUND_DOWN) * market.tick_size
            size = probe_order_size(market, px)
            ids = ClientIdFactory("grid", int(time.time()) % 1_000_000)
            c1, c2, c3 = ids.arcus(), ids.arcus(), ids.arcus()
            res.steps.append(Step("order under test", "INFO",
                                  f"BUY {size} {market.base} @ {px} post-only ({offset_pct:.1f}% below bid {bid}, "
                                  f"${size * px:.2f})"))
            placed = await _do(res, "place (single)", ad.place([OrderRequest(
                Venue.ARCUS, market.base, Side.BUY, px, size, TIF.POST_ONLY, client_id=c1, tag="selftest")]))
            await asyncio.sleep(settle_s)
            await drain()
            oid = placed[0].venue_order_id if placed else None
            resting = [o for o in await ad.rest.open_orders(ad.address, ad.account_index)
                       if o.get("clientId") == c1] if oid else []
            if resting:
                # A modify only counts when the book shows the new price: an accepted request, or a "business"
                # reject, proved nothing (live 2026-09-25: every modify refused, selftest said PASS).
                await _do(res, "modify (one tick lower)", ad.modify(c1, px - market.tick_size, size))
                await asyncio.sleep(settle_s)
                now_px = [Decimal(str(o["price"])) for o in await ad.rest.open_orders(ad.address, ad.account_index)
                          if o.get("clientId") == c1]
                last = res.steps[-1]
                if now_px and now_px[0] == px - market.tick_size and last.level == "PASS":
                    res.steps[-1] = Step(last.name, "PASS", f"the order now rests at {now_px[0]}")
                else:
                    res.steps[-1] = Step(last.name, "FAIL", f"not applied: the order rests at "
                                         f"{now_px[0] if now_px else 'nothing'} ({last.detail[:120]})")
            else:
                res.steps.append(Step("modify (one tick lower)", "SKIP",
                                      "no resting order to modify (unfunded?): a modify can only be proven on a "
                                      "resting order; run with --allow-funded on a funded account or on testnet"))
            await asyncio.sleep(settle_s / 2)
            await _do(res, "place (batch of 2)", ad.place([
                OrderRequest(Venue.ARCUS, market.base, Side.BUY, px - 2 * market.tick_size, size, TIF.POST_ONLY,
                             client_id=c2, tag="selftest"),
                OrderRequest(Venue.ARCUS, market.base, Side.BUY, px - 3 * market.tick_size, size, TIF.POST_ONLY,
                             client_id=c3, tag="selftest")]))
            await asyncio.sleep(settle_s)
            await _do(res, "cancel (batch of 2)", ad.rest.batch_cancel(
                ad.account_index, [(market.venue_market_id, None, c2), (market.venue_market_id, None, c3)]))
            await _do(res, "cancel (single)", ad.rest.cancel_order(ad.account_index, market.venue_market_id, client_id=c1))
            await asyncio.sleep(settle_s / 2)
            await drain()
            seen = {e.client_id: e for e in res.events if e.client_id in (c1, c2, c3)}
            if seen:
                txt = ", ".join(f"{e.status.value}{f' ({e.reject_reason})' if e.reject_reason else ''}" for e in seen.values())
                res.steps.append(Step("orders channel", "PASS", f"lifecycle seen for {len(seen)}/3 orders: {txt}"))
            else:
                res.steps.append(Step("orders channel", "INFO", "no lifecycle events seen (rejected synchronously?)"))

    try:
        cur = await ad.leverage(market.base)
    except BotError as e:
        cur = None
        res.steps.append(Step("read leverage", "INFO", str(e)[:200]))
    if cur is not None:
        await _do(res, f"set leverage (unchanged {cur}x)", ad.set_leverage(market.base, int(cur)))
    try:
        left = await ad.open_orders()
        mine = [o for o in left if o.client_id.startswith("al")]
        res.steps.append(Step("clean-up", "PASS" if not mine else "FAIL",
                              "no orders left" if not mine else f"{len(mine)} test order(s) still open: cancel-all now"))
        if mine:
            await ad.cancel_all(market.base)
    except BotError as e:
        res.steps.append(Step("clean-up", "INFO", f"could not list open orders: {e}"))
    return res


__all__ = ["SelftestResult", "Step", "classify", "probe_order_size", "run_selftest"]
