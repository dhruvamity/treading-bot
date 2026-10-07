"""Arcus, live: the Arcus bot's own client (arcus/venues/arcus: signing, order placement, cancels) behind the
`TradeVenue` interface. Keys come from arcus/.env, the same ones the market-making bot uses.

What has run live before, in the market-making bot: post-only and IOC orders, cancels, cancel-all, set leverage,
positions, balances. What has NOT run live anywhere yet, and is written from the documentation alone:
- reading one order by its id (`GET /v1/order/{orderId}`);
- the position stop and take profit (`POST /v1/batchPlaceOrders` with `grouping: positionTpsl`, each leg signed
  with op 4). If Arcus refuses them, `set_stops` returns False and the executor closes the position rather than
  hold it without the venue's own stops.

Use a subaccount of its own for this (or stop the market-making bot): two programs trading one market on one account
would each treat the other's position as theirs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

import aiohttp

from arbitrage.exec.venue import BUY, OrderInfo, Spec, Top, VenueDown

TPSL_SLIP = Decimal("0.05")       # a triggered stop may fill up to 5% past its trigger (the venue allows 10%)


def tpsl_orders(address: str, account_index: int, market_id: int, position: float, stop: Decimal, take: Decimal,
                round_px: Callable[[Decimal, bool], Decimal], good_til_us: int, ts_ns: int) -> list[dict[str, Any]]:
    """The two legs of a position stop / take profit, as the batch endpoint wants them (unsigned). They close the
    whole position (quantity 0: the engine sizes them when they trigger), so a long is closed by SELL legs.
    round_px(price, down) puts a price on the market's tick."""
    sell = position > 0
    out = []
    for kind, trigger in (("STOP_LOSS", stop), ("TAKE_PROFIT", take)):
        # the price is only a limit on how far the market order may run once triggered
        bound = trigger * (1 - TPSL_SLIP) if sell else trigger * (1 + TPSL_SLIP)
        out.append({"address": address, "accountIndex": account_index, "marketId": market_id,
                    "orderSide": "SELL" if sell else "BUY", "orderType": "MARKET", "quantity": "0",
                    "price": str(round_px(bound, sell)), "timeInForce": "IOC", "tpslType": kind,
                    "stopPrice": str(round_px(trigger, True)), "reduceOnly": True, "timestamp": ts_ns,
                    "goodTilTime": str(good_til_us)})
    return out


class ArcusTrade:
    name = "arcus"

    def __init__(self, bot_dir: Path, *, mainnet: bool = True) -> None:
        self.bot_dir = bot_dir
        self.mainnet = mainnet
        self.adapter: Any = None
        self.rest: Any = None
        self.markets: dict[str, Any] = {}
        self.address = ""
        self.account = 0
        self._orders: dict[str, dict[str, Any]] = {}       # client id -> {"oid", "info", "at"}
        self._stops: dict[str, list[str]] = {}             # symbol -> the venue's ids of its stop orders
        self._ids: Any = None

    # ---------------------------------------------------------------- connection
    async def _connect(self) -> None:
        if self.adapter is not None:
            return
        from arcus.common.config import load_arcus_config
        from arcus.common.ids import ClientIdFactory
        from arcus.common.secrets import SecretStore, load_dotenv
        from arcus.core.creds import arcus_address, arcus_private_keys, discover_arcus_keys
        from arcus.core.liveparams import LiveParams
        from arcus.venues.arcus.adapter import ArcusAdapter
        from arcus.venues.arcus.rest import ArcusRest
        from arcus.venues.arcus.signing import ArcusSigner
        from arcus.venues.base import Venue

        load_dotenv(self.bot_dir / ".env")
        s = SecretStore(self.bot_dir / "config" / "secrets.enc")
        acfg = load_arcus_config(self.bot_dir / "config" / "venues" / "arcus.yaml")
        acfg.env = "mainnet" if self.mainnet else "testnet"
        url = acfg.rest_url()
        pub = ArcusRest(url)
        try:
            self.address = arcus_address(s, not self.mainnet)
            keys = [k for k in await discover_arcus_keys(pub, self.address, arcus_private_keys(s, not self.mainnet))
                    if k.active]
            if not keys:
                raise RuntimeError("no active Arcus API key in arcus/.env for this address (`arcus keys` lists them)")
            key = keys[0]
            lp = LiveParams(arcus=pub)
            await lp.refresh()
        finally:
            await pub.close()
        self.account = int(key.account_index or 0)
        self.rest = ArcusRest(url, signer=ArcusSigner(key.private_key), address=self.address,
                              writes_allowed=True, is_mainnet=self.mainnet)
        self.markets = dict(lp.markets[Venue.ARCUS])
        self.adapter = ArcusAdapter(self.rest, None, address=self.address, account_index=self.account,
                                    markets=self.markets)
        self._ids = ClientIdFactory("manual", int(time.time()) // 60 % 46_000)

    async def _call(self, coro: Any) -> Any:
        """Run one venue call; anything that may pass (a timeout, a 5xx, a rate limit) becomes VenueDown."""
        from arcus.common.errors import VenueError

        try:
            return await coro
        except VenueError as e:
            if e.retryable:
                raise VenueDown(f"arcus: {e}") from e
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            raise VenueDown(f"arcus: {type(e).__name__} {e}") from e

    # ---------------------------------------------------------------- TradeVenue
    async def start(self, symbol: str) -> Spec:
        await self._call(self._connect())
        m = self.markets[symbol]
        return Spec(tick=float(m.tick_size), step=float(m.step_size), min_size=float(m.min_size),
                    min_notional=float(m.min_notional), taker_bp=float(m.taker_fee) * 1e4)

    async def stop(self) -> None:
        if self.rest is not None:
            await self.rest.close()

    async def top(self, symbol: str) -> Top | None:
        b = await self._call(self.rest.bbo(f"{symbol}-USD"))
        try:
            return Top(float(b["bestBid"]["price"]), float(b["bestAsk"]["price"]))
        except (KeyError, TypeError, ValueError):
            return None

    async def position(self, symbol: str) -> float | None:
        for p in await self._call(self.adapter.positions()):
            if p.base == symbol:
                return float(p.size)
        return 0.0

    async def free_collateral(self) -> float | None:
        return float((await self._call(self.adapter.balances()))["free_collateral"])

    async def _place(self, symbol: str, side: int, size: float, price: float, reduce_only: bool, tif: Any) -> str:
        from arcus.common.errors import VenueError
        from arcus.venues.base import OrderRequest, Side, Venue

        m = self.markets[symbol]
        px = m.round_price(Decimal(str(price)), is_bid=side == BUY)
        qty = (Decimal(str(size)) / m.step_size).to_integral_value(ROUND_DOWN) * m.step_size
        cid = self._ids.arcus()
        info = OrderInfo(cid, side, float(px), float(qty))
        self._orders[cid] = {"oid": None, "info": info, "at": time.time()}
        req = OrderRequest(Venue.ARCUS, symbol, Side.BUY if side == BUY else Side.SELL, px, qty, tif, reduce_only, cid,
                           "arb", "funding arbitrage")
        try:
            (st,) = await self._call(self.adapter.place([req]))
        except VenueError as e:           # refused outright (validation, margin): nothing rests
            info.open, info.note = False, str(e)[:160]
            return cid
        self._orders[cid]["oid"] = st.venue_order_id
        self._take(info, st)
        return cid

    @staticmethod
    def _take(info: OrderInfo, st: Any) -> None:
        info.filled = float(st.filled_size or 0)
        if st.avg_fill_price:
            info.avg_px = float(st.avg_fill_price)
        if st.status.is_terminal:
            info.open, info.note = False, st.reject_reason or st.status.value.lower()

    async def maker(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> str:
        from arcus.venues.base import TIF

        return await self._place(symbol, side, size, price, reduce_only, TIF.POST_ONLY)

    async def taker(self, symbol: str, side: int, size: float, worst: float, reduce_only: bool) -> str:
        from arcus.venues.base import TIF

        return await self._place(symbol, side, size, worst, reduce_only, TIF.IOC)

    async def cancel(self, symbol: str, order_id: str) -> None:
        from arcus.common.errors import VenueError

        rec = self._orders.get(order_id)
        if rec is None or not rec["info"].open:
            return
        try:
            await self._call(self.adapter.cancel([order_id]))
        except VenueError:                # already gone (filled or cancelled): `order` will say which
            pass

    async def order(self, symbol: str, order_id: str) -> OrderInfo | None:
        from arcus.common.errors import VenueError
        from arcus.venues.arcus.models import parse_order

        rec = self._orders.get(order_id)
        if rec is None:
            return None
        info: OrderInfo = rec["info"]
        if not info.open:
            return info
        if not rec["oid"]:                # Arcus has not told us its id: look it up among the open orders
            for st in await self._call(self.adapter.open_orders()):
                if st.client_id == order_id:
                    rec["oid"] = st.venue_order_id
                    self._take(info, st)
                    return info
            return None if time.time() - rec["at"] < 10 else info
        try:
            body = await self._call(self.rest._get(f"/v1/order/{rec['oid']}", {"address": self.address}))
        except VenueError:                # not found yet: just placed
            return info
        row = body.get("order") if isinstance(body, dict) and isinstance(body.get("order"), dict) else body
        if not isinstance(row, dict):     # an empty or odd answer: keep what we know, ask again next loop
            return info
        self._take(info, parse_order({**row, "clientId": order_id}, self.adapter._by_id))
        return info

    async def cancel_all(self, symbol: str) -> None:
        from arcus.common.errors import VenueError

        await self._call(self.adapter.cancel_all(symbol))
        m = self.markets[symbol]
        for oid in self._stops.pop(symbol, []):       # the stop orders too, in case cancel-all leaves them
            try:
                await self._call(self.rest.cancel_order(self.account, m.venue_market_id, order_id=oid))
            except VenueError:
                pass
        for rec in self._orders.values():
            if rec["info"].open:
                rec["info"].note = "cancel sent"

    async def set_stops(self, symbol: str, position: float, stop: float, take: float) -> bool:
        from arcus.common.errors import VenueError
        from arcus.venues.arcus import signing as sg

        if position == 0:
            return False
        m = self.markets[symbol]
        ts = time.time_ns()
        good_til = int(time.time() * 1e6) + 35 * 86_400 * 1_000_000
        legs = tpsl_orders(self.address, self.account, m.venue_market_id, position, Decimal(str(stop)),
                           Decimal(str(take)), lambda px, down: m.round_price(px, is_bid=down), good_til, ts)
        signer = self.rest._require_write()
        for b in legs:
            f = sg.OrderFields(market_id=m.venue_market_id, side=b["orderSide"], price=Decimal(b["price"]),
                               size=Decimal(0), tif="IOC", good_til_us=good_til, reduce_only=True,
                               tick_size=m.tick_size, step_size=m.step_size)
            b["signature"] = signer.sign(sg.place_payload(self.address, self.account, ts, f, None, tpsl=True))
        try:
            resp = await self._call(self.rest._post_signed("/v1/batchPlaceOrders",
                                                           {"grouping": "positionTpsl", "orders": legs}, ts,
                                                           legs[0]["signature"]))
        except VenueError:
            return False
        rows = list(resp.get("responses") or resp.get("orders") or []) if isinstance(resp, dict) else []
        ids = [str(r.get("orderId")) for r in rows if isinstance(r, dict) and r.get("orderId") and not r.get("error")]
        self._stops[symbol] = ids
        return len(ids) == 2

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        """The account's leverage setting for the market: at least what the position needs (rounded up), never over
        the market's maximum. It only decides how much margin the position reserves."""
        m = self.markets[symbol]
        top = int(m.max_leverage or (1 / float(m.imf) if float(m.imf) > 0 else 1))
        await self._call(self.adapter.set_leverage(symbol, max(1, min(top, int(-(-leverage // 1))))))
        await asyncio.sleep(0)
