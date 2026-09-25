"""Reconnecting WebSocket client base.

- exponential backoff with jitter on disconnect; subscriptions are replayed on every (re)connect;
- proactive rotation before a venue's connection lifetime (Arcus closes after 24 h);
- application-level keepalive when a venue wants one;
- per-key "last message" timestamps for staleness checks and recorder health;
- close code 1001 (Arcus restart drain) is treated as a normal reconnect;
- an exception in the message handler is logged and that message skipped: it never drops the connection (a frame
  that fails every time, e.g. a snapshot for a market that went offline, used to cause an endless reconnect loop,
  since every reconnect replays the subscriptions and gets the same frame again).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import orjson

from bot.common.logging import Log
from bot.common.ratelimit import RollingWindow

MessageHandler = Callable[[dict[str, Any], int], Awaitable[None] | None]


class ReconnectingWS:
    def __init__(
        self,
        name: str,
        url: str,
        on_message: MessageHandler,
        *,
        ping_payload: dict[str, Any] | None = None,
        ping_every_s: float = 0.0,
        max_lifetime_s: float = 0.0,
        client_msgs_per_min: int = 0,
        on_connect: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self._on_message = on_message
        self._ping_payload = ping_payload
        self._ping_every = ping_every_s
        self._max_lifetime = max_lifetime_s
        self._on_connect = on_connect
        self._subs: dict[str, dict[str, Any]] = {}  # key -> subscribe frame
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._msg_window = RollingWindow(client_msgs_per_min) if client_msgs_per_min else None
        self.log = Log(f"ws.{name}")
        self.last_msg_us: dict[str, int] = {}
        self.reconnects = 0
        self.resubscribes = 0
        self.connected_at: float = 0.0
        self.msgs = 0
        self.handler_errors = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"ws-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._session:
            await self._session.close()

    async def wait_connected(self, timeout: float = 15.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except TimeoutError:
            return False

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    # ---------------------------------------------------------------- subscriptions
    async def subscribe(self, key: str, frame: dict[str, Any]) -> None:
        self._subs[key] = frame
        if self.is_connected:
            await self.send(frame)

    async def resubscribe(self, key: str, unsubscribe_frame: dict[str, Any] | None = None) -> None:
        """Fresh snapshot for one stream (book gap)."""
        frame = self._subs.get(key)
        if frame is None or not self.is_connected:
            return
        self.resubscribes += 1
        if unsubscribe_frame is not None:
            await self.send(unsubscribe_frame)
        await self.send(frame)

    async def send(self, frame: dict[str, Any], *, counted: bool = True) -> None:
        if self._msg_window is not None and counted:
            await self._msg_window.take(1)
        async with self._send_lock:
            if self._ws is None or self._ws.closed:
                raise ConnectionError(f"{self.name}: not connected")
            await self._ws.send_str(orjson.dumps(frame).decode())

    def stale_keys(self, max_age_s: float) -> list[str]:
        now = time.time_ns() // 1000
        return [k for k, t in self.last_msg_us.items() if now - t > max_age_s * 1e6]

    def touch(self, key: str, ts_us: int) -> None:
        self.last_msg_us[key] = ts_us

    # ---------------------------------------------------------------- loop
    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # reconnect on any transport error; logged
                self.log.warning("ws_error", reason=type(e).__name__, data={"err": str(e)[:200]})
            finally:
                self._connected.clear()
            if self._stop.is_set():
                break
            self.reconnects += 1
            delay = min(30.0, 0.5 * 2**attempt) * (1 + random.uniform(-0.2, 0.2))
            attempt = min(attempt + 1, 6)
            await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        async with self._session.ws_connect(self.url, heartbeat=20.0, max_msg_size=0, compress=15) as ws:
            self._ws = ws
            self.connected_at = time.monotonic()
            self.log.info("ws_connected", data={"url": self.url, "subs": len(self._subs)})
            for frame in list(self._subs.values()):
                await self.send(frame)
            if self._on_connect:
                await self._on_connect()
            self._connected.set()
            pinger = asyncio.create_task(self._pinger(ws)) if self._ping_every and self._ping_payload else None
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        recv = time.time_ns() // 1000
                        self.msgs += 1
                        try:
                            data = orjson.loads(msg.data)
                        except orjson.JSONDecodeError:
                            continue
                        try:
                            r = self._on_message(data, recv)
                            if asyncio.iscoroutine(r):
                                await r
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:  # skip this frame, keep the connection (see module docstring)
                            self.handler_errors += 1
                            if self.handler_errors <= 5 or self.handler_errors % 1000 == 0:
                                self.log.error("ws_handler_error", reason=type(e).__name__,
                                               data={"err": str(e)[:200], "n": self.handler_errors,
                                                     "frame": str(data)[:300]}, exc_info=self.handler_errors <= 5)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    if self._max_lifetime and time.monotonic() - self.connected_at > self._max_lifetime:
                        self.log.info("ws_rotate", reason="max_lifetime")
                        break
            finally:
                if pinger:
                    pinger.cancel()
                code = ws.close_code
                if code == 1001:
                    self.log.info("ws_going_away", reason="server restart drain (1001)")
                self._ws = None

    async def _pinger(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        assert self._ping_payload is not None
        while not ws.closed:
            await asyncio.sleep(self._ping_every)
            with contextlib.suppress(Exception):
                await self.send(self._ping_payload)
