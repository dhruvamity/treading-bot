"""Lighter WebSocket client: subscribe, keep alive, reconnect, dispatch.

Frames (docs/websocket, and captured live): the first frame is {"type": "connected"}; a subscription is
{"type": "subscribe", "channel": "order_book/1"} (plus "auth" for account channels); the reply names the channel with
a colon ("order_book:1") and has type "subscribed/<name>", later frames "update/<name>". The server closes a
connection that sends nothing for 2 minutes, so the client sends {"type": "ping"} every 30 s. A connection may drop
during Lighter's deployments: the client reconnects with backoff and subscribes again (each snapshot arrives anew).
Limits per IP: 500 subscriptions per connection, 200 client messages a minute.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import orjson

from lbot.log import Log

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]
log = Log("ws")


def channel_key(channel: str) -> str:
    """'order_book:1' and 'order_book/1' -> 'order_book/1'; 'account_orders:1' (the server drops the account) stays."""
    return channel.replace(":", "/")


class WsClient:
    def __init__(self, url: str, *, name: str = "ws", ping_s: float = 30.0) -> None:
        self.url = url
        self.name = name
        self.ping_s = ping_s
        self.subs: dict[str, str | None] = {}          # channel -> auth token (None: public)
        self.handlers: list[Handler] = []
        self.on_connect: list[Callable[[], Awaitable[None] | None]] = []
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stop = asyncio.Event()
        self.connected = asyncio.Event()
        self.last_msg = 0.0
        self.reconnects = 0
        self.msgs = 0

    def add_handler(self, h: Handler) -> None:
        self.handlers.append(h)

    async def subscribe(self, channel: str, auth: str | None = None) -> None:
        self.subs[channel] = auth
        await self._send({"type": "subscribe", "channel": channel, **({"auth": auth} if auth else {})})

    async def unsubscribe(self, channel: str) -> None:
        self.subs.pop(channel, None)
        await self._send({"type": "unsubscribe", "channel": channel})

    async def resubscribe(self, channel: str) -> None:
        """A fresh snapshot for one channel (e.g. after a gap in the order book's nonces)."""
        auth = self.subs.get(channel)
        await self._send({"type": "unsubscribe", "channel": channel})
        await asyncio.sleep(0.05)
        await self.subscribe(channel, auth)

    async def send_json(self, msg: dict[str, Any]) -> None:
        await self._send(msg)

    async def _send(self, msg: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return          # sent again on (re)connect from self.subs
        try:
            await ws.send_str(orjson.dumps(msg).decode())
        except (ConnectionError, RuntimeError, aiohttp.ClientError) as e:
            log.warn("ws_send_failed", name=self.name, err=str(e))

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(self.url, heartbeat=None, compress=15,
                                                                      max_msg_size=64 * 2**20) as ws:
                    self._ws = ws
                    backoff = 1.0
                    for ch, auth in list(self.subs.items()):
                        await self._send({"type": "subscribe", "channel": ch, **({"auth": auth} if auth else {})})
                    self.connected.set()
                    for cb in self.on_connect:
                        r = cb()
                        if asyncio.iscoroutine(r):
                            await r
                    log.info("ws_connected", name=self.name, subs=len(self.subs))
                    pinger = asyncio.create_task(self._pinger(ws))
                    try:
                        await self._read(ws)
                    finally:
                        pinger.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pinger
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                log.warn("ws_error", name=self.name, err=f"{type(e).__name__}: {e}")
            finally:
                self._ws = None
                self.connected.clear()
            if self._stop.is_set():
                break
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    async def _pinger(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(self.ping_s)
            try:
                await ws.send_str('{"type":"ping"}')
            except (ConnectionError, RuntimeError, aiohttp.ClientError):
                return

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for m in ws:
            if self._stop.is_set():
                await ws.close()
                return
            if m.type != aiohttp.WSMsgType.TEXT:
                if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    return
                continue
            self.last_msg = time.time()
            self.msgs += 1
            try:
                msg = orjson.loads(m.data)
            except orjson.JSONDecodeError:
                continue
            t = msg.get("type", "")
            if t == "ping":
                await self._send({"type": "pong"})
                continue
            if t in ("pong", "connected"):
                continue
            if t == "error" or ("error" in msg and not msg.get("channel")):
                log.warn("ws_error_frame", name=self.name, msg=str(msg)[:300])
                continue
            for h in self.handlers:
                try:
                    r = h(msg)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as e:   # one bad handler must not kill the feed
                    log.error("ws_handler_error", name=self.name, err=f"{type(e).__name__}: {e}",
                              type=t, channel=msg.get("channel"))
