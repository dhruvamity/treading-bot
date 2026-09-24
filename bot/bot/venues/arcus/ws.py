"""Arcus WebSocket client (docs: api-reference/websocket, market-data/*, account/*).

Envelope facts verified live (tests/fixtures/live/arcus_ws_frames.json):
- per-market channels use `id` = market display name (e.g. "BTC-USD"); `oraclePrices`, `markets`,
  `marketAttributes` are global; account channels use `id` = address plus optional `accountIndex`;
- `subscribed.contents` is the snapshot; updates arrive as `channel_data`;
- subscriptions are never authenticated (account data is public by address);
- order RPC: {"type":"post","id":<int>,"request":{"type":<method>,"payload":{..},"apiKey","timestamp","signature"}}
  returns {"method","id","status":202,"result":{...}}; lifecycle arrives on `orders` / `userFills`.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from typing import Any

from bot.common.logging import Log
from bot.core.book import ArcusBookSync, SyncResult
from bot.venues.base import Venue
from bot.venues.symbols import canonical_base
from bot.venues.ws_base import ReconnectingWS

Callback = Callable[..., Any]
log = Log("arcus.ws")


class ArcusWS:
    def __init__(self, url: str, *, n_levels: int = 100, max_lifetime_s: float = 23 * 3600) -> None:
        self.ws = ReconnectingWS("arcus", url, self._on_message, max_lifetime_s=max_lifetime_s,
                                 client_msgs_per_min=900)
        self.n_levels = n_levels
        self.books: dict[str, ArcusBookSync] = {}
        self.cb: dict[str, list[Callback]] = {}
        self._rpc_ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._inflight = asyncio.Semaphore(40)

    # ---------------------------------------------------------------- wiring
    def on(self, event: str, fn: Callback) -> None:
        """Events: book(base, sync, result, recv_us), bbo(base, contents, recv_us), trades(base, list, recv_us),
        oracle(list, recv_us), predicted_funding(base, contents, recv_us), markets(dict, recv_us),
        market_attrs(contents, recv_us), orders/userFills/positions/account/funding(contents, recv_us, frame)."""
        self.cb.setdefault(event, []).append(fn)

    async def _emit(self, event: str, *args: Any) -> None:
        for fn in self.cb.get(event, []):
            r = fn(*args)
            if asyncio.iscoroutine(r):
                await r

    def start(self) -> None:
        self.ws.start()

    async def stop(self) -> None:
        for f in self._pending.values():
            if not f.done():
                f.cancel()
        await self.ws.stop()

    # ---------------------------------------------------------------- subscriptions
    async def subscribe_market(self, display: str, *, book: bool = True, bbo: bool = True, trades: bool = True,
                               predicted_funding: bool = True) -> None:
        if book:
            self.books.setdefault(display, ArcusBookSync())
            await self.ws.subscribe(f"l2u:{display}", {"type": "subscribe", "channel": "l2OrderbookUpdates",
                                                       "id": display, "nLevels": self.n_levels})
        if bbo:
            await self.ws.subscribe(f"bbo:{display}", {"type": "subscribe", "channel": "bbo", "id": display})
        if trades:
            await self.ws.subscribe(f"trades:{display}", {"type": "subscribe", "channel": "trades", "id": display})
        if predicted_funding:
            await self.ws.subscribe(f"pf:{display}", {"type": "subscribe", "channel": "predictedFunding",
                                                      "id": display})

    async def subscribe_global(self, *, markets: bool = True, oracle: bool = True, attrs: bool = True) -> None:
        if markets:
            await self.ws.subscribe("markets", {"type": "subscribe", "channel": "markets"})
        if oracle:
            await self.ws.subscribe("oraclePrices", {"type": "subscribe", "channel": "oraclePrices"})
        if attrs:
            await self.ws.subscribe("marketAttributes", {"type": "subscribe", "channel": "marketAttributes"})

    async def subscribe_account(self, address: str, account_index: int,
                                channels: tuple[str, ...] = ("account", "positions", "orders", "userFills", "funding")) -> None:
        for ch in channels:
            frame: dict[str, Any] = {"type": "subscribe", "channel": ch, "id": address, "accountIndex": account_index}
            if ch == "userFills":
                frame["nFills"] = 50
            if ch == "orders":
                frame["nRecentClosed"] = 20
            await self.ws.subscribe(f"{ch}:{address.lower()}:{account_index}", frame)

    # ---------------------------------------------------------------- RPC
    async def post(self, method: str, payload: dict[str, Any], *, api_key: str, ts_ns: int, signature: str,
                   timeout: float = 5.0) -> dict[str, Any]:
        rid = next(self._rpc_ids)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        async with self._inflight:
            await self.ws.send({"type": "post", "id": rid, "request": {
                "type": method, "payload": payload, "apiKey": api_key, "timestamp": str(ts_ns),
                "signature": signature}}, counted=False)
            try:
                return await asyncio.wait_for(fut, timeout)
            finally:
                self._pending.pop(rid, None)

    # ---------------------------------------------------------------- dispatch
    async def _on_message(self, m: dict[str, Any], recv_us: int) -> None:
        t = m.get("type")
        if t in ("subscribed", "channel_data"):
            await self._on_channel(m, recv_us, snapshot=(t == "subscribed"))
        elif "method" in m and isinstance(m.get("id"), int):
            fut = self._pending.get(m["id"])
            if fut and not fut.done():
                fut.set_result(m)
        elif t == "error":
            log.warning("ws_error_frame", data={"message": str(m.get("message"))[:300]})
            await self._emit("error", m, recv_us)

    async def _on_channel(self, m: dict[str, Any], recv_us: int, *, snapshot: bool) -> None:
        ch = m.get("channel")
        sid = m.get("id") or ""
        c: Any = m.get("contents") or {}
        if ch == "l2OrderbookUpdates":
            sync = self.books.setdefault(sid, ArcusBookSync())
            self.ws.touch(f"l2u:{sid}", recv_us)
            if snapshot:
                sync.on_snapshot(c, recv_us)
                res = SyncResult.APPLIED
            else:
                res = sync.on_delta(c, recv_us)
                if res is SyncResult.GAP:
                    log.warning("book_gap", venue="arcus", market=sid, reason="mid-stream sequence gap; resubscribing")
                    await self.ws.resubscribe(f"l2u:{sid}", {"type": "unsubscribe", "channel": "l2OrderbookUpdates",
                                                             "id": sid})
            await self._emit("book", canonical_base(Venue.ARCUS, sid), sync, res, recv_us, snapshot, c)
        elif ch == "bbo":
            self.ws.touch(f"bbo:{sid}", recv_us)
            await self._emit("bbo", canonical_base(Venue.ARCUS, sid), c, recv_us)
        elif ch == "trades":
            self.ws.touch(f"trades:{sid}", recv_us)
            if isinstance(c, list) and c:
                await self._emit("trades", canonical_base(Venue.ARCUS, sid), c, recv_us)
        elif ch == "predictedFunding":
            self.ws.touch(f"pf:{sid}", recv_us)
            if c:
                await self._emit("predicted_funding", canonical_base(Venue.ARCUS, sid), c, recv_us)
        elif ch == "oraclePrices":
            self.ws.touch("oraclePrices", recv_us)
            await self._emit("oracle", (c or {}).get("prices") or [], recv_us)
        elif ch == "markets":
            self.ws.touch("markets", recv_us)
            await self._emit("markets", (c or {}).get("markets") or {}, recv_us)
        elif ch == "marketAttributes":
            self.ws.touch("marketAttributes", recv_us)
            await self._emit("market_attrs", c or {}, recv_us)
        elif ch in ("orders", "userFills", "positions", "account", "funding", "accountTransferUpdates",
                    "accountAttributeUpdates"):
            await self._emit(ch, c, recv_us, m)
