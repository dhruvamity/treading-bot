"""Arcus VenueAdapter (testnet or mainnet; mainnet writes only with the B4 lock open, enforced by ArcusRest).

Writes go over REST (0 IP weight); lifecycle comes from the `orders` / `userFills` account channels.
Every order carries a clientId, so a retry after a transport error is reconciled by clientId, never blind.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from bot.common.decimal import D
from bot.common.errors import OrderRejected, VenueError
from bot.common.logging import Log
from bot.venues.arcus import signing as sg
from bot.venues.arcus.models import parse_fill, parse_order, parse_position
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import (
    TIF,
    Fill,
    Market,
    OrderRequest,
    OrderState,
    OrderStatus,
    Position,
    RateBudget,
    Venue,
)

log = Log("arcus.adapter")
_TIF = {TIF.POST_ONLY: "ALO", TIF.GTT: "GTT", TIF.IOC: "IOC", TIF.FOK: "FOK"}


@dataclass
class _Live:
    req: OrderRequest
    market: Market
    order_id: str | None
    good_til_us: int
    status: OrderStatus = OrderStatus.PENDING_NEW


class ArcusAdapter:
    venue = Venue.ARCUS

    def __init__(self, rest: ArcusRest, ws: ArcusWS | None, *, address: str, account_index: int,
                 markets: dict[str, Market], good_til_days: int = 35, modify_echo_client_id: bool = False) -> None:
        self.rest = rest
        self.ws = ws
        self.address = address
        self.account_index = account_index
        self._markets = markets
        self._by_id = {m.venue_market_id: m.base for m in markets.values()}
        self.good_til_days = good_til_days
        self.modify_echo_client_id = modify_echo_client_id
        self._live: dict[str, _Live] = {}
        self._orders_q: asyncio.Queue[OrderState] = asyncio.Queue()
        self._fills_q: asyncio.Queue[Fill] = asyncio.Queue()
        self._pool: dict[str, dict[str, Any]] = {}
        self._last_pool_poll = 0.0
        self.errors = 0
        self.actions = 0

    # ---------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        if self.ws is not None:
            self.ws.on("orders", self._on_orders)
            self.ws.on("userFills", self._on_fills)
            await self.ws.subscribe_account(self.address, self.account_index, ("orders", "userFills", "positions",
                                                                               "account"))
            self.ws.start()
            await self.ws.ws.wait_connected()
        await self.poll_rate_limit()

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.stop()
        await self.rest.close()

    async def markets(self) -> Sequence[Market]:
        return list(self._markets.values())

    def update_markets(self, markets: dict[str, Market]) -> None:
        self._markets = markets
        self._by_id = {m.venue_market_id: m.base for m in markets.values()}

    # ---------------------------------------------------------------- helpers
    def _fields(self, r: OrderRequest, m: Market, good_til_us: int) -> sg.OrderFields:
        side: Literal["BUY", "SELL"] = "BUY" if r.side.value == "buy" else "SELL"
        return sg.OrderFields(market_id=m.venue_market_id, side=side, price=r.price, size=r.size,
                              tif=_TIF[r.tif], good_til_us=good_til_us, reduce_only=r.reduce_only,  # type: ignore[arg-type]
                              tick_size=m.tick_size, step_size=m.step_size)

    def _good_til(self) -> int:
        return int(time.time() * 1e6) + self.good_til_days * 86_400 * 1_000_000

    # ---------------------------------------------------------------- writes
    async def place(self, orders: Sequence[OrderRequest]) -> Sequence[OrderState]:
        out: list[OrderState] = []
        batch: list[tuple[sg.OrderFields, str | None]] = []
        reqs: list[OrderRequest] = []
        for r in orders:
            if not r.client_id:
                raise ValueError("every Arcus order must carry a clientId (idempotency, B3.4)")
            m = self._markets[r.base]
            gt = self._good_til()
            self._live[r.client_id] = _Live(r, m, None, gt)
            batch.append((self._fields(r, m, gt), r.client_id))
            reqs.append(r)
        self.actions += len(batch)
        for i in range(0, len(batch), 39):
            chunk, chunk_reqs = batch[i:i + 39], reqs[i:i + 39]
            try:
                if len(chunk) == 1:
                    resp = await self.rest.place_order(self.account_index, chunk[0][0], chunk[0][1])
                    items = [resp]
                else:
                    resp = await self.rest.batch_place(self.account_index, chunk)
                    items = list(resp.get("responses") or resp.get("orders") or [])
            except OrderRejected as e:
                # Definitive engine reject on the synchronous path: nothing rests. Report it like a streamed reject
                # so the state, the order manager and the reject breaker all see it (no ghost "pending" order).
                out += [await self._reject_local(r, e.reason) for r in chunk_reqs]
                continue
            except VenueError as e:
                if not e.retryable:  # 4xx / auth / geo: the request was not accepted, so nothing can be resting
                    for r in chunk_reqs:
                        await self._reject_local(r, type(e).__name__)
                raise  # retryable (5xx / transmission): may have landed; reconciliation by clientId decides
            for r, it in zip(chunk_reqs, items, strict=False):
                st = parse_order({**it, "clientId": it.get("clientId") or r.client_id,
                                  "marketId": self._markets[r.base].venue_market_id}, self._by_id)
                live = self._live.get(r.client_id)
                if live is not None:
                    live.order_id = st.venue_order_id or live.order_id
                    live.status = st.status
                if st.status.is_terminal:  # e.g. a batch element rejected in the synchronous response
                    self._live.pop(r.client_id, None)
                    await self._orders_q.put(st)
                out.append(st)
        return out

    async def _reject_local(self, r: OrderRequest, reason: str) -> OrderState:
        self._live.pop(r.client_id or "", None)
        st = OrderState(r.client_id or "", None, OrderStatus.REJECTED, D(0), None, reason, int(time.time() * 1e6),
                        Venue.ARCUS, r.base, r.side, r.price, r.size, r.tif, r.reduce_only, r.tag)
        await self._orders_q.put(st)
        return st

    async def modify(self, client_id: str, price: Decimal, size: Decimal) -> OrderState:
        live = self._live[client_id]
        r = live.req
        new_req = OrderRequest(r.venue, r.base, r.side, price, size, r.tif, r.reduce_only, r.client_id, r.tag, r.reason)
        f = self._fields(new_req, live.market, live.good_til_us)
        self.actions += 1
        resp = await self.rest.modify_order(self.account_index, f, order_id=live.order_id,
                                            client_id=None if live.order_id else client_id,
                                            echo_client_id=self.modify_echo_client_id)
        live.req = new_req
        live.order_id = resp.get("orderId") or live.order_id
        return parse_order({**resp, "clientId": client_id, "marketId": live.market.venue_market_id}, self._by_id)

    async def cancel(self, client_ids: Sequence[str]) -> None:
        todo = [(self._live[c].market.venue_market_id, None, c) for c in client_ids if c in self._live]
        self.actions += len(todo)
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            if len(chunk) == 1:
                mid, _, cid = chunk[0]
                await self.rest.cancel_order(self.account_index, mid, client_id=cid)
            else:
                await self.rest.batch_cancel(self.account_index, chunk)

    async def cancel_all(self, base: str | None = None) -> None:
        mid = self._markets[base].venue_market_id if base else None
        self.actions += 1
        await self.rest.cancel_all(self.account_index, mid)

    async def set_leverage(self, base: str, leverage: int, isolated: bool = False) -> None:
        await self.rest.set_leverage(self.account_index, self._markets[base].venue_market_id, leverage, isolated)

    async def leverage(self, base: str) -> int | None:
        """The leverage in force for this subaccount and market (the override, else the market default = max)."""
        rows = await self.rest.leverages(self.address, self.account_index, self._markets[base].venue_symbol)
        return int(rows[0]["leverage"]) if rows else None

    async def arm_dead_mans_switch(self, deadline_us: int | None) -> None:
        await self.rest.schedule_cancel(self.account_index, deadline_us)

    # ---------------------------------------------------------------- reads
    async def positions(self) -> Sequence[Position]:
        body = await self.rest.positions(self.address, self.account_index)
        return [parse_position(p, self._by_id) for p in (body.get("positions") or {}).values()
                if D(p.get("size") or 0) != 0]

    async def open_orders(self) -> Sequence[OrderState]:
        return [parse_order(o, self._by_id) for o in await self.rest.open_orders(self.address, self.account_index)]

    async def balances(self) -> dict[str, Decimal]:
        a = await self.rest.account(self.address, self.account_index)
        return {"equity": D(a.get("equity") or 0), "free_collateral": D(a.get("freeCollateral") or 0),
                "net_deposits": D(a.get("netDeposits") or 0)}

    async def poll_rate_limit(self) -> dict[str, Any]:
        try:
            body = await self.rest.rate_limit(self.address, self.account_index)
        except Exception as e:  # budget polling must not kill trading; the governor falls back to write echoes
            log.warning("rate_limit_poll_failed", reason=type(e).__name__)
            return self._pool
        self._pool = {"order": body.get("order") or {}, "cancel": body.get("cancel") or {}}
        self._last_pool_poll = time.monotonic()
        return self._pool

    # ---------------------------------------------------------------- streams
    async def _on_orders(self, contents: Any, recv_us: int, frame: dict[str, Any]) -> None:
        rows = contents.get("orders") if isinstance(contents, dict) else contents
        if isinstance(rows, dict):
            rows = list(rows.values())
        for o in rows or []:
            if not isinstance(o, dict) or "orderId" not in o:
                continue
            st = parse_order(o, self._by_id, recv_us)
            live = self._live.get(st.client_id)
            if live is not None:
                live.order_id = st.venue_order_id or live.order_id
                live.status = st.status
                if st.status.is_terminal:
                    self._live.pop(st.client_id, None)
            await self._orders_q.put(st)

    async def _on_fills(self, contents: Any, recv_us: int, frame: dict[str, Any]) -> None:
        rows = contents.get("fills") if isinstance(contents, dict) else contents
        if frame.get("type") == "subscribed":
            return  # snapshot of historical fills: reconciliation reads them via REST, not as new fills
        for f in rows or []:
            if isinstance(f, dict) and "tradeId" in f:
                await self._fills_q.put(parse_fill(f, self._by_id))

    async def order_updates(self) -> AsyncIterator[OrderState]:
        while True:
            yield await self._orders_q.get()

    async def fills(self) -> AsyncIterator[Fill]:
        while True:
            yield await self._fills_q.get()

    def budget(self) -> RateBudget:
        o, c = self._pool.get("order") or {}, self._pool.get("cancel") or {}
        o_rem = self.rest.pool_remaining.get("order")
        c_rem = self.rest.pool_remaining.get("cancel")
        if o_rem is None and o:
            o_rem = int(o.get("cap", 0)) - int(o.get("used", 0))
        if c_rem is None and c:
            c_rem = int(c.get("cap", 0)) - int(c.get("used", 0))
        return RateBudget(self.venue, o_rem, int(o["cap"]) if o.get("cap") else None, c_rem,
                          int(c["cap"]) if c.get("cap") else None, None,
                          int(max(o.get("nextAvailableMs", 0) or 0, c.get("nextAvailableMs", 0) or 0)))

    def health(self) -> dict[str, float]:
        h: dict[str, float] = {"rest_error_rate": self.rest.http.error_rate(),
                               "rest_latency_ms": self.rest.http.last_latency_us / 1000}
        if self.ws is not None:
            now = time.time_ns() // 1000
            for k, t in self.ws.ws.last_msg_us.items():
                h[f"age_s:{k}"] = (now - t) / 1e6
        return h

    def live_orders(self) -> dict[str, OrderRequest]:
        return {k: v.req for k, v in self._live.items()}
