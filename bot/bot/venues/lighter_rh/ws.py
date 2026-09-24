"""Lighter RH WebSocket client (docs: apidocs.rh.lighter.xyz/docs/websocket).

Verified live (tests/fixtures/live/lighter_ws_frames.json): frames carry `type` "subscribed/<ch>" then
"update/<ch>", `channel` "order_book:1" (colon form); the order_book subscribe frame is a FULL snapshot and each
update's `begin_nonce` equals the previous `nonce`. Keepalive: send a frame at least every 2 min (we send
{"type":"ping"} every 60 s). Client messages <= 200/min per IP (we cap at 150); sendTx over WS is not counted.
`market_stats` carries a live `premium` (percent) and `current_funding_rate` (percent per hour, estimate).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from bot.common.logging import Log
from bot.core.book import LighterBookSync, SyncResult
from bot.venues.ws_base import ReconnectingWS

Callback = Callable[..., Any]
log = Log("lighter.ws")


class LighterWS:
    def __init__(self, url: str, *, readonly: bool = True, msgs_per_min: int = 150) -> None:
        full = url + ("?readonly=true" if readonly else "")
        self.ws = ReconnectingWS("lighter_rh", full, self._on_message, ping_payload={"type": "ping"},
                                 ping_every_s=60.0, client_msgs_per_min=msgs_per_min)
        self.books: dict[int, LighterBookSync] = {}
        self.base_by_id: dict[int, str] = {}
        self.cb: dict[str, list[Callback]] = {}

    def on(self, event: str, fn: Callback) -> None:
        """Events: book(base, sync, result, recv_us, snapshot, ob), ticker(base, t, recv_us),
        trades(base, trades, liq_trades, recv_us), market_stats(base, stats, recv_us),
        account_all/account_all_orders/account_all_trades/account_all_positions/user_stats(frame, recv_us)."""
        self.cb.setdefault(event, []).append(fn)

    async def _emit(self, event: str, *args: Any) -> None:
        for fn in self.cb.get(event, []):
            r = fn(*args)
            if asyncio.iscoroutine(r):
                await r

    def start(self) -> None:
        self.ws.start()

    async def stop(self) -> None:
        await self.ws.stop()

    async def subscribe_market(self, market_id: int, base: str, *, book: bool = True, ticker: bool = True,
                               trades: bool = True, stats: bool = True) -> None:
        self.base_by_id[market_id] = base
        if book:
            self.books.setdefault(market_id, LighterBookSync())
            await self.ws.subscribe(f"order_book/{market_id}", {"type": "subscribe", "channel": f"order_book/{market_id}"})
        if ticker:
            await self.ws.subscribe(f"ticker/{market_id}", {"type": "subscribe", "channel": f"ticker/{market_id}"})
        if trades:
            await self.ws.subscribe(f"trade/{market_id}", {"type": "subscribe", "channel": f"trade/{market_id}"})
        if stats:
            await self.ws.subscribe(f"market_stats/{market_id}",
                                    {"type": "subscribe", "channel": f"market_stats/{market_id}"})

    async def subscribe_account(self, account_index: int, auth_token: str,
                                channels: tuple[str, ...] = ("account_all_orders", "account_all_trades",
                                                             "account_all_positions", "user_stats")) -> None:
        for ch in channels:
            await self.ws.subscribe(f"{ch}/{account_index}", {"type": "subscribe", "channel": f"{ch}/{account_index}",
                                                             "auth": auth_token})

    async def refresh_auth(self, account_index: int, auth_token: str, channels: tuple[str, ...] = (
            "account_all_orders", "account_all_trades", "account_all_positions", "user_stats")) -> None:
        """Replace the stored subscribe frames so the next reconnect uses a fresh token."""
        for ch in channels:
            key = f"{ch}/{account_index}"
            if key in self.ws._subs:
                self.ws._subs[key]["auth"] = auth_token

    async def _on_message(self, m: dict[str, Any], recv_us: int) -> None:
        t = str(m.get("type") or "")
        if t in ("pong", "connected", "ping"):
            return
        if t == "error" or "error" in m:
            log.warning("ws_error_frame", data={"frame": str(m)[:300]})
            return
        ch = str(m.get("channel") or "")
        name, _, rest = ch.partition(":")
        snapshot = t.startswith("subscribed/")
        if name == "order_book":
            mid = int(rest)
            sync = self.books.setdefault(mid, LighterBookSync())
            ob = m.get("order_book") or {}
            self.ws.touch(f"order_book/{mid}", recv_us)
            if snapshot:
                sync.on_snapshot(ob, recv_us)
                res = SyncResult.APPLIED
            else:
                res = sync.on_delta(ob, recv_us)
                if res is SyncResult.GAP:
                    log.warning("book_gap", venue="lighter_rh", market=self.base_by_id.get(mid),
                                reason="begin_nonce != previous nonce; resubscribing")
                    await self.ws.resubscribe(f"order_book/{mid}",
                                              {"type": "unsubscribe", "channel": f"order_book/{mid}"})
            await self._emit("book", self.base_by_id.get(mid, str(mid)), sync, res, recv_us, snapshot, m)
        elif name == "ticker":
            mid = int(rest)
            self.ws.touch(f"ticker/{mid}", recv_us)
            await self._emit("ticker", self.base_by_id.get(mid, str(mid)), m, recv_us)
        elif name == "trade":
            mid = int(rest)
            self.ws.touch(f"trade/{mid}", recv_us)
            if not snapshot:  # the subscribe frame replays recent history; recorder dedupes by trade_id anyway
                await self._emit("trades", self.base_by_id.get(mid, str(mid)), m.get("trades") or [],
                                 m.get("liquidation_trades") or [], recv_us)
        elif name == "market_stats":
            self.ws.touch(f"market_stats/{rest}", recv_us)
            stats = m.get("market_stats") or {}
            if rest == "all":
                for k, s in stats.items():
                    await self._emit("market_stats", self.base_by_id.get(int(k), k), s, recv_us)
            else:
                await self._emit("market_stats", self.base_by_id.get(int(rest), rest), stats, recv_us)
        elif name in ("account_all", "account_all_orders", "account_all_trades", "account_all_positions",
                      "user_stats", "account_tx", "notification", "account_market"):
            await self._emit(name, m, recv_us)
