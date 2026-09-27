"""The live Lighter exchange for one market and one account.

- Transactions are signed here (lbot/venue/signer.py) with SkipNonce and time-based nonces (lbot/venue/nonce.py),
  and sent over REST: one requote = one sendTxBatch with the modifies, new orders and cancels together.
- Our orders are referenced by our own client order index (Lighter accepts it wherever an order index goes).
- Account state arrives over an authenticated WebSocket: account_orders/<market>/<account> (our orders),
  account_all/<account> (positions and fills), user_stats/<account> (equity). A REST reconcile every 5 minutes (and
  whenever an order goes unacknowledged) makes Lighter the source of truth.
- Dead man's switch: every 60 s the bot moves a scheduled cancel-all 5.5 minutes ahead (abort + schedule, one
  request). If the bot or its machine dies, Lighter cancels every order within about 5 minutes by itself.
- At start the market's orders are cancelled (the bot treats the account's orders on its market as its own) and the
  leverage is set (cross margin).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from lbot.config import Config
from lbot.log import Log
from lbot.trade.exchange import Change, Exchange, Fill, Order
from lbot.trade.feed import MarketFeed
from lbot.trade.strategy import BUY, SELL
from lbot.venue import consts as C
from lbot.venue.market import Market
from lbot.venue.nonce import ClientIds, Nonces
from lbot.venue.rest import READ, RESERVE, ApiError, Rest
from lbot.venue.signer import SignedTx, Signer
from lbot.venue.ws import WsClient

log = Log("live")
AUTH_LIFETIME_S = 7 * 3600
DMS_EVERY_S = 60.0
DMS_AHEAD_MS = C.CANCEL_ALL_MIN_MS + 30_000
RECONCILE_EVERY_S = 300.0
UNACKED_S = 10.0


async def resolve_account(rest: Rest, cfg: Config) -> int:
    """The account to trade: LIGHTER_ACCOUNT_INDEX, else the L1 address's main account."""
    if cfg.creds.account_index is not None:
        return cfg.creds.account_index
    if not cfg.creds.address:
        raise RuntimeError("LIGHTER_ADDRESS is not set in lighter/.env")
    r = await rest.accounts_by_l1(cfg.creds.address)
    subs = r.get("sub_accounts") or []
    if not subs:
        raise RuntimeError(f"no Lighter account for {cfg.creds.address[:8]}…: deposit first (app.lighter.xyz)")
    return min(int(s["index"]) for s in subs)          # the main account has the lowest index (Lighter's examples)


class LiveExchange(Exchange):
    mode = "live"

    def __init__(self, market: Market, feed: MarketFeed, cfg: Config, account: int, ids: ClientIds,
                 nonces: Nonces) -> None:
        super().__init__(market, feed, cfg.requests, cfg.latency, ids)
        self.cfg = cfg
        self.account = account
        self.nonces = nonces
        self.rest = Rest(cfg.endpoints.rest, self.budget)
        self.signer = Signer(cfg.endpoints.rest, cfg.creds.private_key, cfg.endpoints.chain_id,
                             cfg.creds.api_key_index, account)
        self.ws = WsClient(cfg.endpoints.ws, name="account")
        self.ws.add_handler(self.on_msg)
        self.seen_tids: set[int] = set()
        self.started_ms = int(time.time() * 1000)
        self.auth_until = 0.0
        self._tasks: list[asyncio.Task[Any]] = []
        self._last_dms = 0.0
        self._last_reconcile = 0.0
        self.taker_in_flight_until = 0.0
        self.errors: list[tuple[float, str]] = []

    # ---------------------------------------------------------------- signing helpers
    def _n(self, k: int = 1) -> list[int]:
        return self.nonces.take(k)

    def _px(self, side: int, px: float) -> int:
        return self.market.price_int(px, side_buy=side == BUY)

    def _sign_changes(self, changes: list[Change]) -> tuple[list[SignedTx], list[Order], list[int]]:
        txs: list[SignedTx] = []
        new_orders: list[Order] = []
        touched: list[int] = []
        nonces = self._n(len(changes))
        for c, nonce in zip(changes, nonces, strict=True):
            m = self.market
            if c.kind == "new" and c.quote is not None:
                q = c.quote
                cid = self.ids.take()[0]
                txs.append(self.signer.create_order(
                    market=m.market_id, client_index=cid, size=m.size_int(q.qty), price=self._px(q.side, q.px),
                    is_ask=q.side == SELL, order_type=C.ORDER_LIMIT, tif=C.TIF_POST_ONLY, reduce_only=q.reduce_only,
                    expiry=C.ORDER_EXPIRY_DEFAULT, nonce=nonce))
                new_orders.append(Order(cid, q.side, m.price_of(self._px(q.side, q.px)), m.size_of(m.size_int(q.qty)),
                                        q.tag, q.reduce_only, "sent", time.time()))
            elif c.kind == "modify" and c.quote is not None:
                q = c.quote
                txs.append(self.signer.modify_order(market=m.market_id, index=c.cid, size=m.size_int(q.qty),
                                                    price=self._px(q.side, q.px), nonce=nonce))
                touched.append(c.cid)
            elif c.kind == "cancel":
                txs.append(self.signer.cancel_order(market=m.market_id, index=c.cid, nonce=nonce))
                touched.append(c.cid)
        return txs, new_orders, touched

    # ---------------------------------------------------------------- the interface
    async def send(self, changes: list[Change], kind: str) -> bool:
        if not changes:
            return True
        if not self.budget.allows(kind):
            return False
        txs, new_orders, _ = self._sign_changes(changes)
        try:
            r = await self.rest.send(txs, kind=kind, wait=False)
        except ApiError as e:
            self.errors.append((time.time(), f"{e.code}: {e.message}"))
            self.rejects.append((time.time(), str(e.code)))
            log.warn("send_refused", code=e.code, msg=e.message, n=len(txs))
            if e.code in (C.ERR_NOT_ENOUGH_MARGIN, C.ERR_BELOW_INITIAL_MARGIN):
                self.budget.block(5.0)
            return False
        except (TimeoutError, OSError) as e:
            log.warn("send_failed", err=str(e))
            self._last_reconcile = 0.0      # find out what landed
            return False
        if r is None:
            return False
        now = time.time()
        for o in new_orders:
            o.sent_at = now
            self.orders[o.cid] = o
        for c in changes:
            o = self.orders.get(c.cid)
            if o is None:
                continue
            if c.kind == "modify" and c.quote is not None:
                o.state, o.sent_at = "sent", now
                o.px, o.qty = c.quote.px, c.quote.qty
            elif c.kind == "cancel":
                o.state, o.sent_at = "cancelling", now
        return True

    async def taker(self, qty_signed: float) -> None:
        """A reduce-only market order (IOC) with a worst price 50 bp through the book."""
        if time.time() < self.taker_in_flight_until:
            return
        bbo = self.feed.bbo()
        if bbo is None or qty_signed == 0:
            return
        side = BUY if qty_signed > 0 else SELL
        worst = bbo[1] * 1.005 if side == BUY else bbo[0] * 0.995
        m = self.market
        tx = self.signer.create_order(market=m.market_id, client_index=self.ids.take()[0],
                                      size=m.size_int(abs(qty_signed)), price=self._px(-side, worst),
                                      is_ask=side == SELL, order_type=C.ORDER_MARKET, tif=C.TIF_IOC,
                                      reduce_only=True, expiry=C.IOC_EXPIRY, nonce=self._n()[0])
        try:
            await self.rest.send([tx], kind=RESERVE, wait=True)
            self.taker_in_flight_until = time.time() + 3.0
            log.info("taker_sent", side=side, qty=abs(qty_signed))
        except ApiError as e:
            log.warn("taker_refused", code=e.code, msg=e.message)

    async def cancel_all(self) -> None:
        """Every order on this market, now."""
        tx = self.signer.cancel_all(tif=C.CANCEL_ALL_NOW, time_ms=0, nonce=self._n()[0], market=self.market.market_id)
        try:
            await self.rest.send([tx], kind=RESERVE, wait=True)
        except ApiError as e:
            log.warn("cancel_all_refused", code=e.code, msg=e.message)
        for o in self.orders.values():
            if o.state in ("sent", "open"):
                o.state = "cancelling"

    async def set_leverage(self, leverage: float) -> None:
        frac = self.market.leverage_fraction(leverage)
        tx = self.signer.update_leverage(market=self.market.market_id, fraction=frac, nonce=self._n()[0])
        await self.rest.send([tx], kind=RESERVE, wait=True)
        log.info("leverage_set", market=self.market.symbol, leverage=leverage, fraction=frac)

    async def dead_mans_switch(self, now: float) -> None:
        if now - self._last_dms < DMS_EVERY_S:
            return
        self._last_dms = now
        n = self._n(2)
        txs = [self.signer.cancel_all(tif=C.CANCEL_ALL_ABORT, time_ms=0, nonce=n[0]),
               self.signer.cancel_all(tif=C.CANCEL_ALL_SCHEDULED, time_ms=int(now * 1000) + DMS_AHEAD_MS,
                                      nonce=n[1])]
        try:
            await self.rest.send(txs, kind=RESERVE, wait=True)
        except ApiError as e:
            log.warn("dms_refused", code=e.code, msg=e.message)
            self._last_dms = now - DMS_EVERY_S + 10      # try again in 10 s

    async def disarm(self) -> None:
        """Abort the scheduled cancel-all (a clean stop that leaves no orders)."""
        with contextlib.suppress(ApiError):
            await self.rest.send([self.signer.cancel_all(tif=C.CANCEL_ALL_ABORT, time_ms=0, nonce=self._n()[0])],
                                 kind=RESERVE, wait=True)

    # ---------------------------------------------------------------- account stream
    def _auth(self) -> str:
        tok = self.signer.auth_token(int(time.time()) + AUTH_LIFETIME_S)
        self.auth_until = time.time() + AUTH_LIFETIME_S - 3600
        self.rest.auth = tok
        return tok

    async def subscribe_account(self) -> None:
        """Subscribe (or, with a fresh token, re-subscribe) the account channels."""
        tok = self._auth()
        a, mk = self.account, self.market.market_id
        for ch in (f"account_orders/{mk}/{a}", f"account_all/{a}", f"user_stats/{a}"):
            if ch in self.ws.subs:
                self.ws.subs[ch] = tok
                await self.ws.resubscribe(ch)
            else:
                await self.ws.subscribe(ch, tok)

    def on_msg(self, msg: dict[str, Any]) -> None:
        ch = msg.get("channel", "")
        typ = msg.get("type", "")
        if ch.startswith("account_orders"):
            for rows in (msg.get("orders") or {}).values():
                for od in rows or []:
                    self._on_order(od)
        elif ch.startswith("account_all"):
            pos = (msg.get("positions") or {}).get(str(self.market.market_id))
            if pos is not None:
                self._on_position(pos)
            if not typ.startswith("subscribed"):     # the snapshot repeats old trades
                for t in (msg.get("trades") or {}).get(str(self.market.market_id)) or []:
                    self._on_trade(t)
        elif ch.startswith("user_stats"):
            st = msg.get("stats") or {}
            tot = st.get("total_stats") or {}
            pv = tot.get("portfolio_value") or st.get("portfolio_value")
            if pv is not None:
                self.acct.equity = float(pv)
                self.acct.free = float(tot.get("available_balance") or st.get("available_balance") or 0)
                self.acct.updated = time.time()

    def _on_order(self, od: dict[str, Any]) -> None:
        cid = int(od.get("client_order_index") or 0)
        status = str(od.get("status", ""))
        o = self.orders.get(cid) if cid else None
        if o is None:
            if status in C.OPEN_STATUSES:     # an order we did not place (the app, an older run): ours to cancel
                side = SELL if od.get("is_ask") else BUY
                oid = int(od.get("order_index") or 0)
                key = cid or oid
                self.orders[key] = Order(key, side, float(od.get("price") or 0),
                                         float(od.get("remaining_base_amount") or 0), "foreign", bool(od.get("reduce_only")),
                                         "open", time.time(), oid=oid)
            return
        o.oid = int(od.get("order_index") or o.oid)
        if status in C.OPEN_STATUSES:
            if o.state != "cancelling":
                o.state = "open"
            o.px = float(od.get("price") or o.px)
            o.qty = float(od.get("remaining_base_amount") or o.qty)
        else:
            o.state, o.why_done = "done", status
            if status.startswith("canceled-") and status not in ("canceled-expired",):
                self.rejects.append((time.time(), status))

    def _on_position(self, p: dict[str, Any]) -> None:
        size = float(p.get("position") or 0) * (1 if int(p.get("sign") or 1) >= 0 else -1)
        self.acct.pos = size if abs(size) > self.market.step / 2 else 0.0
        e = float(p.get("avg_entry_price") or 0)
        self.acct.entry = e if self.acct.pos and e > 0 else None

    def _on_trade(self, t: dict[str, Any]) -> None:
        tid = int(t.get("trade_id") or 0)
        if tid in self.seen_tids or int(t.get("timestamp") or 0) < self.started_ms - 1000:
            return
        self.seen_tids.add(tid)
        we_bid = int(t.get("bid_account_id") or -1) == self.account
        we_ask = int(t.get("ask_account_id") or -1) == self.account
        if not (we_bid or we_ask):
            return
        side = BUY if we_bid else SELL
        maker_ask = bool(t.get("is_maker_ask"))
        maker = (maker_ask and we_ask) or (not maker_ask and we_bid)
        px, qty = float(t["price"]), float(t["size"])
        fee_units = int(t.get("maker_fee" if maker else "taker_fee") or 0)
        cid = int(t.get("bid_client_id" if we_bid else "ask_client_id") or 0)
        o = self.orders.get(cid)
        self._emit(Fill(time.time(), side, px, qty, maker, o.tag if o else ("taker" if not maker else ""),
                        px * qty * fee_units / 1e6, tid))

    # ---------------------------------------------------------------- upkeep
    async def reconcile(self) -> None:
        """Lighter's word on the position, the equity and our open orders."""
        self._last_reconcile = time.time()
        try:
            r = await self.rest.account(self.account)
            acc = (r.get("accounts") or [{}])[0]
            for p in acc.get("positions") or []:
                if int(p.get("market_id", -1)) == self.market.market_id:
                    self._on_position(p)
            if acc.get("total_asset_value") is not None:
                self.acct.equity = float(acc["total_asset_value"])
            if acc.get("available_balance") is not None:
                self.acct.free = float(acc["available_balance"])
            self.acct.updated = time.time()
            ao = await self.rest.active_orders(self.account, self.market.market_id)
        except (ApiError, TimeoutError, OSError) as e:
            log.warn("reconcile_failed", err=str(e))
            return
        live = {int(od.get("client_order_index") or 0) or int(od.get("order_index") or 0): od
                for od in ao.get("orders") or []}
        for od in live.values():
            self._on_order(od)
        for cid, o in self.orders.items():
            if o.state in ("sent", "open", "cancelling") and cid not in live and time.time() - o.sent_at > UNACKED_S:
                o.state, o.why_done = "done", "not on the book"

    def tick(self, now: float) -> None:
        self.orders = {k: o for k, o in self.orders.items() if o.state != "done" or now - o.sent_at < 60}

    async def upkeep(self, now: float) -> None:
        """Called by the engine every second: dead man's switch, auth refresh, reconciles."""
        await self.dead_mans_switch(now)
        if now > self.auth_until:
            await self.subscribe_account()
        stale = any(o.state in ("sent", "cancelling") and now - o.sent_at > UNACKED_S for o in self.orders.values())
        if (now - self._last_reconcile > RECONCILE_EVERY_S or stale) and self.budget.allows(READ) \
                and now - self._last_reconcile > 5:
            await self.reconcile()

    async def start(self) -> None:
        await self.feed.start()
        self._tasks.append(asyncio.create_task(self.ws.run()))
        await self.subscribe_account()
        await self.reconcile()
        await self.cancel_all()

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self.cancel_all()
            await self.disarm()
        self.ws.stop()
        for t in self._tasks:
            t.cancel()
        await self.feed.stop()
        await self.rest.close()
