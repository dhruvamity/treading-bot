"""Lighter (Robinhood Chain), live: the Lighter bot's own pieces (lighter_bot/venue: signer, REST, WebSocket, nonces,
the synced order book) behind the `TradeVenue` interface. The key comes from lighter/.env.

Nothing here has traded live yet, in this program or in the Lighter bot. Checked without sending anything
(2026-10-04): the key is registered, account reads and the account's WebSocket streams work, and orders sign.
Written from the documentation and the official SDK alone: the order life cycle over the account stream, and the
stop-loss and take-profit orders (order types 2 and 4 with a trigger price).

Reads cost nothing against Lighter's 60 requests a minute: the book and the account come over the WebSocket. Only
orders, cancels and an occasional account check use the limit.

Give this program an API key of its own, or stop the Lighter market-making bot while it runs: both would draw
order numbers from the same key and treat each other's position as theirs.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any

import aiohttp

from arbitrage.exec.venue import BUY, OrderInfo, Spec, Top, VenueDown

AUTH_LIFETIME_S = 6 * 3600
TRIGGER_SLIP = 0.05          # a triggered stop may fill up to 5% past its trigger
RECONCILE_S = 20.0           # how often the position is also read over REST
STOPS_LOOKS = 6              # how many times Lighter's own list is read for a new stop pair
STOPS_WAIT_S = 1.0           # ... and how long apart


def order_view(od: dict[str, Any]) -> tuple[float, bool, str]:
    """(filled size, still open, status) from one order row of the account stream or the REST lists."""
    from lighter_bot.venue import consts as C

    status = str(od.get("status", ""))
    init = float(od.get("initial_base_amount") or 0)
    rem = float(od.get("remaining_base_amount") or 0)
    filled = float(od.get("filled_base_amount") or 0) or max(0.0, init - rem)
    return filled, status in C.OPEN_STATUSES, status


class LighterTrade:
    name = "lighter"

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.cfg: Any = None
        self.rest: Any = None
        self.signer: Any = None
        self.ws: Any = None
        self.feed: Any = None
        self.market: Any = None
        self.account = 0
        self.nonces: Any = None
        self.ids: Any = None
        self._orders: dict[int, OrderInfo] = {}
        self._sent: dict[int, float] = {}
        self._pos: float | None = None
        self._pos_at = 0.0
        self._auth_until = 0.0
        self._tasks: list[asyncio.Task[Any]] = []
        self._symbol = ""

    # ---------------------------------------------------------------- connection
    async def _connect(self, symbol: str) -> None:
        if self.rest is not None and symbol == self._symbol:
            return
        if self.rest is not None:
            await self.stop()
        from lighter_bot.config import load
        from lighter_bot.trade.feed import MarketFeed
        from lighter_bot.trade.live import resolve_account
        from lighter_bot.venue.market import parse_markets
        from lighter_bot.venue.nonce import ClientIds, Nonces
        from lighter_bot.venue.rest import Rest
        from lighter_bot.venue.signer import Signer
        from lighter_bot.venue.ws import WsClient

        self.cfg = cfg = load()
        if not cfg.creds.private_key:
            raise RuntimeError("LIGHTER_API_PRIVATE_KEY is not set in lighter/.env")
        self.rest = Rest(cfg.endpoints.rest)
        self.market = parse_markets(await self.rest.markets())[symbol]
        self.account = await resolve_account(self.rest, cfg)
        self.signer = Signer(cfg.endpoints.rest, cfg.creds.private_key, cfg.endpoints.chain_id,
                             cfg.creds.api_key_index, self.account)
        err = self.signer.check()
        if err:
            raise RuntimeError(f"Lighter does not hold this API key: {err}")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.nonces = Nonces(self.state_dir / f"lighter-nonce-{self.account}-{cfg.creds.api_key_index}.txt")
        self.ids = ClientIds(self.state_dir / f"lighter-client-{self.account}.txt")
        self.feed = MarketFeed(cfg.endpoints.ws, self.market)
        await self.feed.start()
        self.ws = WsClient(cfg.endpoints.ws, name="arb-account")
        self.ws.add_handler(self._on_msg)
        self._tasks.append(asyncio.create_task(self.ws.run()))
        await self._subscribe()
        self._symbol = symbol
        await self._reconcile()

    async def _subscribe(self) -> None:
        tok = self.signer.auth_token(int(time.time()) + AUTH_LIFETIME_S)
        self._auth_until = time.time() + AUTH_LIFETIME_S - 3600
        self.rest.auth = tok
        for ch in (f"account_orders/{self.market.market_id}/{self.account}", f"account_all/{self.account}"):
            if ch in self.ws.subs:
                self.ws.subs[ch] = tok
                await self.ws.resubscribe(ch)
            else:
                await self.ws.subscribe(ch, tok)

    def _on_msg(self, msg: dict[str, Any]) -> None:
        ch = str(msg.get("channel", ""))
        if ch.startswith("account_orders"):
            for rows in (msg.get("orders") or {}).values():
                for od in rows or []:
                    self._on_order(od)
        elif ch.startswith("account_all"):
            p = (msg.get("positions") or {}).get(str(self.market.market_id))
            if p is not None:
                self._on_position(p)

    def _on_order(self, od: dict[str, Any]) -> None:
        info = self._orders.get(int(od.get("client_order_index") or 0))
        if info is None:
            return
        filled, is_open, status = order_view(od)
        info.filled = max(info.filled, filled)
        px = float(od.get("filled_quote_amount") or 0)
        if px > 0 and info.filled > 0:
            info.avg_px = px / info.filled
        if not is_open:
            info.open, info.note = False, status

    def _on_position(self, p: dict[str, Any]) -> None:
        size = float(p.get("position") or 0) * (1 if int(p.get("sign") or 1) >= 0 else -1)
        self._pos = size if abs(size) > self.market.step / 2 else 0.0
        self._pos_at = time.time()

    async def _reconcile(self) -> None:
        """Lighter's own word over REST: the position, and which of our orders still rest."""
        r = await self._call(self.rest.account(self.account))
        acc = (r.get("accounts") or [{}])[0]
        found = False
        for p in acc.get("positions") or []:
            if int(p.get("market_id", -1)) == self.market.market_id:
                self._on_position(p)
                found = True
        if not found:
            self._pos, self._pos_at = 0.0, time.time()
        ao = await self._call(self.rest.active_orders(self.account, self.market.market_id))
        live = {int(od.get("client_order_index") or 0): od for od in ao.get("orders") or []}
        for od in live.values():
            self._on_order(od)
        now = time.time()
        for cid, info in self._orders.items():
            if info.open and cid not in live and now - self._sent.get(cid, now) > 10:
                info.open, info.note = False, info.note or "not on the book"

    async def _call(self, coro: Any) -> Any:
        from lighter_bot.venue.rest import ApiError

        try:
            return await coro
        except ApiError as e:
            if e.code in (23000, 429) or e.status >= 500:
                raise VenueDown(f"lighter: {e.code} {e.message}") from e
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            raise VenueDown(f"lighter: {type(e).__name__} {e}") from e

    async def _upkeep(self) -> None:
        if time.time() > self._auth_until:
            await self._subscribe()
        if time.time() - self._pos_at > RECONCILE_S:
            await self._reconcile()

    # ---------------------------------------------------------------- TradeVenue
    async def start(self, symbol: str) -> Spec:
        await self._call(self._connect(symbol))
        m = self.market
        return Spec(tick=m.tick, step=m.step, min_size=m.min_base, min_notional=m.min_quote, taker_bp=0.0)

    async def stop(self) -> None:
        if self.ws is not None:
            self.ws.stop()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        with contextlib.suppress(Exception):
            if self.feed is not None:
                await self.feed.stop()
            if self.rest is not None:
                await self.rest.close()
        self.rest = None

    async def top(self, symbol: str) -> Top | None:
        await self._upkeep()
        if not self.feed.fresh():
            return None
        b = self.feed.bbo()
        return Top(b[0], b[1]) if b else None

    async def position(self, symbol: str) -> float | None:
        await self._upkeep()
        return self._pos

    async def free_collateral(self) -> float | None:
        r = await self._call(self.rest.account(self.account))
        acc = (r.get("accounts") or [{}])[0]
        v = acc.get("available_balance")
        return float(v) if v is not None else None

    async def _send(self, txs: list[Any]) -> None:
        from lighter_bot.venue.rest import RESERVE

        await self._call(self.rest.send(txs, kind=RESERVE, wait=True))

    async def _order(self, side: int, size: float, price: float, reduce_only: bool, *, order_type: int, tif: int,
                     expiry: int, trigger: float = 0.0) -> int:
        from lighter_bot.venue.rest import ApiError

        m = self.market
        cid = self.ids.take()[0]
        px = m.price_int(price, side_buy=side == BUY)
        info = OrderInfo(str(cid), side, m.price_of(px), m.size_of(m.size_int(size)))
        self._orders[cid], self._sent[cid] = info, time.time()
        tx = self.signer.create_order(
            market=m.market_id, client_index=cid, size=m.size_int(size), price=px, is_ask=side != BUY,
            order_type=order_type, tif=tif, reduce_only=reduce_only, expiry=expiry, nonce=self.nonces.take()[0],
            trigger_price=m.price_int(trigger, side_buy=True) if trigger else 0)
        try:
            await self._send([tx])
        except ApiError as e:             # refused: nothing rests
            info.open, info.note = False, f"{e.code}: {e.message}"[:160]
        return cid

    @property
    def maker_life_s(self) -> float:
        """How long a maker order may rest before the engine replaces it: Lighter drops it by itself at its expiry,
        which cannot be moved (lighter_bot/venue/consts.py)."""
        from lighter_bot.venue import consts as C

        return C.QUOTE_EXPIRY_S - C.QUOTE_RENEW_S

    async def maker(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> str:
        """A post-only order that expires by itself in 5.5 minutes: what clears it if this program dies. Lighter's
        scheduled cancel-all does not (measured on the venue, 2026-10-09: it fires only on the account's next
        request). The stop and the take-profit keep the 28 days: they are what protects the position then."""
        from lighter_bot.venue import consts as C

        return str(await self._order(side, size, price, reduce_only, order_type=C.ORDER_LIMIT, tif=C.TIF_POST_ONLY,
                                     expiry=int((time.time() + C.QUOTE_EXPIRY_S) * 1000)))

    async def taker(self, symbol: str, side: int, size: float, worst: float, reduce_only: bool) -> str:
        from lighter_bot.venue import consts as C

        return str(await self._order(side, size, worst, reduce_only, order_type=C.ORDER_MARKET, tif=C.TIF_IOC,
                                     expiry=C.IOC_EXPIRY))

    async def cancel(self, symbol: str, order_id: str) -> None:
        from lighter_bot.venue.rest import ApiError

        info = self._orders.get(int(order_id))
        if info is None or not info.open:
            return
        tx = self.signer.cancel_order(market=self.market.market_id, index=int(order_id), nonce=self.nonces.take()[0])
        with contextlib.suppress(ApiError):    # already gone: the account stream says how it ended
            await self._send([tx])

    async def order(self, symbol: str, order_id: str) -> OrderInfo | None:
        await self._upkeep()
        info = self._orders.get(int(order_id))
        if info is None:
            return None
        if info.open and time.time() - self._sent.get(int(order_id), 0) > 10 and time.time() - self._pos_at > 5:
            await self._reconcile()        # an IOC that ended, or an update the stream dropped
        return info

    async def cancel_all(self, symbol: str) -> None:
        from lighter_bot.venue import consts as C
        from lighter_bot.venue.rest import ApiError

        tx = self.signer.cancel_all(tif=C.CANCEL_ALL_NOW, time_ms=0, nonce=self.nonces.take()[0],
                                    market=self.market.market_id)
        with contextlib.suppress(ApiError):
            await self._send([tx])

    async def set_stops(self, symbol: str, position: float, stop: float, take: float) -> bool:
        """A stop-loss and a take-profit order for the whole position: reduce-only, on the closing side, each a
        market order once the mark reaches its trigger, limited to TRIGGER_SLIP past it."""
        from lighter_bot.venue import consts as C
        from lighter_bot.venue.rest import ApiError

        if position == 0:
            return False
        m = self.market
        side = -1 if position > 0 else 1                  # the closing side
        txs, cids = [], set()
        for order_type, trigger in ((C.ORDER_STOP_LOSS, stop), (C.ORDER_TAKE_PROFIT, take)):
            worst = trigger * (1 + side * TRIGGER_SLIP)
            cid = self.ids.take()[0]
            cids.add(cid)
            txs.append(self.signer.create_order(
                market=m.market_id, client_index=cid, size=m.size_int(abs(position)),
                price=m.price_int(worst, side_buy=side == BUY), is_ask=side != BUY, order_type=order_type,
                tif=C.TIF_IOC, reduce_only=True, expiry=C.ORDER_EXPIRY_DEFAULT, nonce=self.nonces.take()[0],
                trigger_price=m.price_int(trigger, side_buy=True)))
        try:
            await self._send(txs)
        except ApiError:
            return False
        # "OK" is not enough: Lighter answers OK to a batch and leaves out a member it does not like (seen on the
        # venue, 2026-10-09). The position is protected only when both orders are on Lighter's own list.
        for look in range(STOPS_LOOKS):
            ao = await self._call(self.rest.active_orders(self.account, m.market_id))
            if cids <= {int(o.get("client_order_index") or 0) for o in ao.get("orders") or []}:
                return True
            if look < STOPS_LOOKS - 1:
                await asyncio.sleep(STOPS_WAIT_S)
        return False

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        """The account's margin setting for the market: enough for the position, never over the market's maximum."""
        m = self.market
        lev = max(1.0, min(m.max_leverage, float(-(-leverage // 1))))
        tx = self.signer.update_leverage(market=m.market_id, fraction=m.leverage_fraction(lev),
                                         nonce=self.nonces.take()[0])
        await self._send([tx])
