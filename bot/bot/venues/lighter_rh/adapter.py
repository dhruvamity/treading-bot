"""Lighter RH VenueAdapter.

- Orders are signed locally (official signer) and sent with sendTxBatch (<= 50 per REST batch) to save the
  standard account's 60-per-minute budget. Client ids are uint48 `client_order_index` values (decimal strings in
  our normalized models); cancels may use the client order index as `order_index` (docs: signing-transactions).
- Self-trade prevention: expire-maker, compared by MASTER account (no own-subaccount matching, A2.1).
- Dead man's switch: Lighter supports a SCHEDULED cancel-all (tx 16, time-in-force 1); we arm it like Arcus's.
- Post-only uses TIF 2 (confirmed in lighter-sdk constants and docs).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.common.decimal import D
from bot.common.logging import Log
from bot.venues.base import (
    TIF,
    Fill,
    Market,
    OrderRequest,
    OrderState,
    OrderStatus,
    Position,
    RateBudget,
    Side,
    Venue,
)
from bot.venues.lighter_rh import signer as ls
from bot.venues.lighter_rh.auth import AuthTokenManager
from bot.venues.lighter_rh.models import (
    parse_order,
    parse_own_trade,
    parse_position,
    price_to_int,
    size_to_int,
)
from bot.venues.lighter_rh.nonce import NonceManager
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.lighter_rh.ws import LighterWS

log = Log("lighter.adapter")


@dataclass
class _Live:
    req: OrderRequest
    market: Market
    coi: int
    status: OrderStatus = OrderStatus.PENDING_NEW


class LighterAdapter:
    venue = Venue.LIGHTER_RH

    def __init__(self, rest: LighterRest, ws: LighterWS | None, *, signer: ls.LighterSigner, nonces: NonceManager,
                 auth: AuthTokenManager, account_index: int, markets: dict[str, Market],
                 hedge_slippage_bps: float = 10.0) -> None:
        self.rest = rest
        self.ws = ws
        self.signer = signer
        self.nonces = nonces
        self.auth = auth
        self.account_index = account_index
        self._markets = markets
        self._by_id = {m.venue_market_id: m.base for m in markets.values()}
        self._live: dict[str, _Live] = {}
        self._orders_q: asyncio.Queue[OrderState] = asyncio.Queue()
        self._fills_q: asyncio.Queue[Fill] = asyncio.Queue()
        self._seen_trades: set[str] = set()
        self.hedge_slippage_bps = hedge_slippage_bps
        self.actions = 0

    async def connect(self) -> None:
        if self.ws is not None:
            self.ws.on("account_all_orders", self._on_orders)
            self.ws.on("account_all_trades", self._on_trades)
            await self.ws.subscribe_account(self.account_index, self.auth.token())
            self.ws.start()
            await self.ws.ws.wait_connected()

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.stop()
        await self.rest.close()

    async def markets(self) -> Sequence[Market]:
        return list(self._markets.values())

    def update_markets(self, markets: dict[str, Market]) -> None:
        self._markets = markets
        self._by_id = {m.venue_market_id: m.base for m in markets.values()}

    # ---------------------------------------------------------------- signing helpers
    def _sign_order(self, r: OrderRequest, m: Market, coi: int, nonce: int) -> ls.SignedTx:
        if r.tif is TIF.POST_ONLY:
            tif, otype, expiry = ls.TIF_POST_ONLY, ls.ORDER_TYPE_LIMIT, ls.DEFAULT_28_DAY_ORDER_EXPIRY
        elif r.tif is TIF.GTT:
            tif, otype, expiry = ls.TIF_GTT, ls.ORDER_TYPE_LIMIT, ls.DEFAULT_28_DAY_ORDER_EXPIRY
        else:  # IOC / FOK -> IOC limit with a worst-price bound (hedges)
            tif, otype, expiry = ls.TIF_IOC, ls.ORDER_TYPE_LIMIT, ls.IOC_EXPIRY
        return self.signer.create_order(
            market_index=m.venue_market_id, client_order_index=coi, base_amount=size_to_int(r.size, m),
            price=price_to_int(r.price, m), is_ask=r.side is Side.SELL, order_type=otype, time_in_force=tif,
            reduce_only=r.reduce_only, order_expiry=expiry, nonce=nonce)

    async def _send(self, txs: list[ls.SignedTx]) -> list[str]:
        hashes: list[str] = []
        for i in range(0, len(txs), 50):
            chunk = txs[i:i + 50]
            if len(chunk) == 1:
                resp = await self.rest.send_tx(chunk[0].tx_type, chunk[0].tx_info)
                hashes.append(str(resp.get("tx_hash", "")))
            else:
                resp = await self.rest.send_tx_batch([t.tx_type for t in chunk], [t.tx_info for t in chunk])
                hashes.extend(str(h) for h in resp.get("tx_hash") or [])
        return hashes

    # ---------------------------------------------------------------- writes
    async def place(self, orders: Sequence[OrderRequest]) -> Sequence[OrderState]:
        txs: list[ls.SignedTx] = []
        out: list[OrderState] = []
        for r in orders:
            if not r.client_id.isdigit():
                raise ValueError("Lighter client ids must be uint48 decimal strings (ClientIdFactory.lighter)")
            m = self._markets[r.base]
            coi = int(r.client_id)
            txs.append(self._sign_order(r, m, coi, self.nonces.next()))
            self._live[r.client_id] = _Live(r, m, coi)
            out.append(OrderState(r.client_id, None, OrderStatus.PENDING_NEW, Decimal(0), None, None,
                                  time.time_ns() // 1000, Venue.LIGHTER_RH, r.base, r.side, r.price, r.size, r.tif,
                                  r.reduce_only, r.tag))
        self.actions += len(txs)
        await self._send(txs)
        return out

    async def modify(self, client_id: str, price: Decimal, size: Decimal) -> OrderState:
        live = self._live[client_id]
        m = live.market
        tx = self.signer.modify_order(market_index=m.venue_market_id, order_index=live.coi,
                                      base_amount=size_to_int(size, m), price=price_to_int(price, m),
                                      nonce=self.nonces.next())
        self.actions += 1
        await self._send([tx])
        r = live.req
        live.req = OrderRequest(r.venue, r.base, r.side, price, size, r.tif, r.reduce_only, r.client_id, r.tag, r.reason)
        return OrderState(client_id, None, OrderStatus.PENDING_NEW, Decimal(0), None, None, time.time_ns() // 1000,
                          Venue.LIGHTER_RH, r.base, r.side, price, size, r.tif, r.reduce_only, r.tag)

    async def cancel(self, client_ids: Sequence[str]) -> None:
        txs = []
        for c in client_ids:
            live = self._live.get(c)
            if live is None:
                continue
            txs.append(self.signer.cancel_order(market_index=live.market.venue_market_id, order_index=live.coi,
                                                nonce=self.nonces.next()))
        if txs:
            self.actions += len(txs)
            await self._send(txs)

    async def cancel_all(self, base: str | None = None) -> None:
        mid = self._markets[base].venue_market_id if base else ls.NIL_MARKET_INDEX
        tx = self.signer.cancel_all(time_in_force=ls.CANCEL_ALL_IMMEDIATE, time_ms=0, nonce=self.nonces.next(),
                                    market_index=mid)
        self.actions += 1
        await self._send([tx])

    async def arm_dead_mans_switch(self, deadline_us: int | None) -> None:
        if deadline_us is None:
            tx = self.signer.cancel_all(time_in_force=ls.CANCEL_ALL_ABORT, time_ms=0, nonce=self.nonces.next())
        else:
            tx = self.signer.cancel_all(time_in_force=ls.CANCEL_ALL_SCHEDULED, time_ms=deadline_us // 1000,
                                        nonce=self.nonces.next())
        await self._send([tx])

    async def set_leverage(self, base: str, leverage: int, isolated: bool = False) -> None:
        m = self._markets[base]
        tx = self.signer.update_leverage(market_index=m.venue_market_id,
                                         imf_fraction=ls.leverage_to_imf_fraction(leverage),
                                         margin_mode=ls.ISOLATED_MARGIN if isolated else ls.CROSS_MARGIN,
                                         nonce=self.nonces.next())
        await self._send([tx])

    # ---------------------------------------------------------------- reads
    async def positions(self) -> Sequence[Position]:
        acct = await self.rest.account(self.account_index)
        return [parse_position(p, self._by_id) for p in acct.get("positions") or [] if D(p.get("position") or 0) != 0]

    async def open_orders(self) -> Sequence[OrderState]:
        return [parse_order(o, self._by_id) for o in await self.rest.active_orders(self.account_index, self.auth.token())]

    async def balances(self) -> dict[str, Decimal]:
        acct = await self.rest.account(self.account_index)
        return {"equity": D(acct.get("total_asset_value") or acct.get("collateral") or 0),
                "free_collateral": D(acct.get("available_balance") or 0),
                "collateral": D(acct.get("collateral") or 0),
                "cross_mm_req": D(acct.get("cross_maintenance_margin_requirement") or 0)}

    # ---------------------------------------------------------------- streams
    async def _on_orders(self, frame: dict[str, Any], recv_us: int) -> None:
        for rows in (frame.get("orders") or {}).values():
            for o in rows:
                st = parse_order(o, self._by_id, recv_us)
                live = self._live.get(st.client_id)
                if live is not None:
                    live.status = st.status
                    if st.status.is_terminal:
                        self._live.pop(st.client_id, None)
                await self._orders_q.put(st)

    async def _on_trades(self, frame: dict[str, Any], recv_us: int) -> None:
        if str(frame.get("type", "")).startswith("subscribed"):
            return
        trades = frame.get("trades") or {}
        rows = [t for v in trades.values() for t in v] if isinstance(trades, dict) else list(trades)
        for t in rows:
            f = parse_own_trade(t, self.account_index, self._by_id)
            if f.trade_id in self._seen_trades:
                continue
            self._seen_trades.add(f.trade_id)
            await self._fills_q.put(f)

    async def order_updates(self) -> AsyncIterator[OrderState]:
        while True:
            yield await self._orders_q.get()

    async def fills(self) -> AsyncIterator[Fill]:
        while True:
            yield await self._fills_q.get()

    def budget(self) -> RateBudget:
        return RateBudget(self.venue, None, None, None, None, self.rest.sendtx_remaining(), 0)

    def health(self) -> dict[str, float]:
        h: dict[str, float] = {"rest_error_rate": self.rest.http.error_rate(),
                               "rest_latency_ms": self.rest.http.last_latency_us / 1000,
                               "limit_events": float(self.rest.limit_events)}
        if self.ws is not None:
            now = time.time_ns() // 1000
            for k, t in self.ws.ws.last_msg_us.items():
                h[f"age_s:{k}"] = (now - t) / 1e6
        return h

    def live_orders(self) -> dict[str, OrderRequest]:
        return {k: v.req for k, v in self._live.items()}
