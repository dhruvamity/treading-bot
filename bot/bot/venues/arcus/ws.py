"""Arcus WebSocket client (docs: api-reference/websocket, market-data/*, account/*).

Envelope facts verified live (tests/fixtures/live/arcus_ws_frames.json):
- per-market channels use `id` = market display name (e.g. "BTC-USD"); `oraclePrices`, `markets`,
  `marketAttributes` are global; account channels use `id` = address plus optional `accountIndex`;
- `subscribed.contents` is the snapshot; updates arrive as `channel_data`;
- subscriptions are never authenticated (account data is public by address);
- order RPC: {"type":"post","id":<int>,"request":{"type":<method>,"payload":{..},"apiKey","timestamp","signature"}}
  returns {"method","id","status":202,"result":{...}}; lifecycle arrives on `orders` / `userFills`;
- a market that is not available (OFFLINE, pre-listed, delisted, unknown) answers `l2OrderbookUpdates` with an empty
  snapshot (`contents: {}`) and `bbo` with {"type":"error","message":"Market 'X' is not available"} (seen live
  2026-09-25): the book stays empty and not ready, and each distinct error is logged once per 10 minutes;
- a `degraded` frame (e.g. reason `snapshot_stale`) echoes the subscription's channel, id and accountIndex: that
  stream can no longer be trusted. A book is dropped at once; the stream is re-subscribed for a fresh snapshot (at
  most once per 10 s per stream), and a `degraded` event lets the account adapter ask for a reconciliation.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Callable
from typing import Any

from bot.common.logging import Log
from bot.core.book import ArcusBookSync, SyncResult
from bot.venues.base import Venue
from bot.venues.symbols import canonical_base
from bot.venues.ws_base import ReconnectingWS

Callback = Callable[..., Any]
log = Log("arcus.ws")
DEGRADED_RESUB_S = 10.0
ERROR_LOG_EVERY_S = 600.0
PER_MARKET = {"l2OrderbookUpdates": "l2u", "bbo": "bbo", "trades": "trades", "predictedFunding": "pf"}
GLOBAL = ("markets", "oraclePrices", "marketAttributes")
ACCOUNT = ("orders", "userFills", "positions", "account", "funding", "accountTransferUpdates", "accountAttributeUpdates")


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
        self._resub_at: dict[str, float] = {}             # last degraded-driven resubscribe per stream
        self._resub_later: dict[str, asyncio.Task[None]] = {}
        self._errors_logged: dict[str, float] = {}
        self.degraded = 0

    # ---------------------------------------------------------------- wiring
    def on(self, event: str, fn: Callback) -> None:
        """Events: book(base, sync, result, recv_us), bbo(base, contents, recv_us), trades(base, list, recv_us),
        oracle(list, recv_us), predicted_funding(base, contents, recv_us), markets(dict, recv_us),
        market_attrs(contents, recv_us), orders/userFills/positions/account/funding(contents, recv_us, frame),
        degraded(channel, id, frame, recv_us), error(frame, recv_us)."""
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
        for task in self._resub_later.values():
            task.cancel()
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
        elif t == "degraded":
            await self._on_degraded(m, recv_us)
        elif t == "error":
            msg = str(m.get("message"))[:300]
            now = time.monotonic()
            if now - self._errors_logged.get(msg, -ERROR_LOG_EVERY_S) >= ERROR_LOG_EVERY_S:
                self._errors_logged[msg] = now
                log.warning("ws_error_frame", data={"message": msg})
            await self._emit("error", m, recv_us)

    @staticmethod
    def stream_key(channel: str, sid: str, account_index: Any = None) -> tuple[str, dict[str, Any]] | None:
        """(subscription key, unsubscribe frame) for a channel frame, as subscribe_* registered it."""
        if channel in PER_MARKET:
            return f"{PER_MARKET[channel]}:{sid}", {"type": "unsubscribe", "channel": channel, "id": sid}
        if channel in GLOBAL:
            return channel, {"type": "unsubscribe", "channel": channel}
        if channel in ACCOUNT:
            ai = int(account_index or 0)
            return (f"{channel}:{sid.lower()}:{ai}",
                    {"type": "unsubscribe", "channel": channel, "id": sid, "accountIndex": ai})
        return None

    async def _on_degraded(self, m: dict[str, Any], recv_us: int) -> None:
        ch, sid = str(m.get("channel") or ""), str(m.get("id") or "")
        self.degraded += 1
        if ch == "l2OrderbookUpdates" and sid in self.books:
            self.books[sid].invalidate(recv_us)   # never quote off a book the venue says is stale
        k = self.stream_key(ch, sid, m.get("accountIndex"))
        log.warning("ws_degraded", venue="arcus", market=sid or None,
                    reason=str(m.get("reason") or m.get("message") or m.get("contents") or "")[:200],
                    data={"channel": ch, "resubscribe": bool(k)})
        await self._emit("degraded", ch, sid, m, recv_us)
        if k is not None:
            await self._resubscribe_soon(*k)

    async def _resubscribe_soon(self, key: str, unsubscribe: dict[str, Any]) -> None:
        """Re-subscribe for a fresh snapshot now, or once DEGRADED_RESUB_S has passed since the last one (a venue that
        keeps sending `degraded` must not turn this into a subscribe storm)."""
        wait = self._resub_at.get(key, -DEGRADED_RESUB_S) + DEGRADED_RESUB_S - time.monotonic()
        if wait <= 0:
            self._resub_at[key] = time.monotonic()
            await self.ws.resubscribe(key, unsubscribe)
            return
        pending = self._resub_later.get(key)
        if pending is not None and not pending.done():
            return

        async def later() -> None:
            await asyncio.sleep(wait)
            self._resub_at[key] = time.monotonic()
            await self.ws.resubscribe(key, unsubscribe)

        self._resub_later[key] = asyncio.create_task(later())

    async def _on_channel(self, m: dict[str, Any], recv_us: int, *, snapshot: bool) -> None:
        ch = m.get("channel")
        sid = m.get("id") or ""
        c: Any = m.get("contents") or {}
        if ch == "l2OrderbookUpdates":
            sync = self.books.setdefault(sid, ArcusBookSync())
            self.ws.touch(f"l2u:{sid}", recv_us)
            if snapshot:
                res = sync.on_snapshot(c, recv_us)
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
