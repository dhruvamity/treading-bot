"""One market's public data for a running bot: the synced order book and the trades, from Lighter's WebSocket.

The book is the order_book channel kept in sync by nonce (lbot/venue/book.py); a gap re-subscribes (one at a time)
for a fresh snapshot. Trades are passed to listeners (the paper exchange fills its orders from them) and summed for
the market's taker flow. `fresh()` says whether the book is current enough to quote on.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from lbot.log import Log
from lbot.venue.book import Book
from lbot.venue.market import Market
from lbot.venue.ws import WsClient

log = Log("feed")
TradeListener = Callable[[list[dict[str, Any]]], None]
RESYNC_RETRY_S = 5.0     # a re-subscribe whose snapshot never came is sent again after this long


class MarketFeed:
    def __init__(self, ws_url: str, market: Market, *, readonly: bool = True) -> None:
        self.market = market
        self.book = Book(market.market_id)
        self.ws = WsClient(ws_url + ("?readonly=true" if readonly else ""), name=f"feed-{market.symbol}")
        self.ws.add_handler(self.on_msg)
        self.trade_listeners: list[TradeListener] = []
        self.book_listeners: list[Callable[[], None]] = []
        self.flow: deque[tuple[float, float]] = deque()      # (time, taker usd) over the last 5 minutes
        self.book_at = 0.0
        self.resync_at = 0.0          # when the running book re-subscribe was sent (0: none running)
        self._task: asyncio.Task[None] | None = None
        self._resync_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.ws.subscribe(f"order_book/{self.market.market_id}")
        await self.ws.subscribe(f"trade/{self.market.market_id}")
        await self.ws.subscribe("height")          # a frame every 0.5 s: proof the feed is alive
        self._task = asyncio.create_task(self.ws.run())

    async def stop(self) -> None:
        self.ws.stop()
        if self._task:
            self._task.cancel()

    def on_msg(self, msg: dict[str, Any]) -> None:
        ch = msg.get("channel", "")
        mid = str(self.market.market_id)
        if ch == f"order_book:{mid}":
            ob = msg.get("order_book") or {}
            ts = int(msg.get("timestamp") or 0)
            if msg.get("type", "").startswith("subscribed"):
                self.book.snapshot(ob, ts)
                self.resync_at = 0.0
            elif self.book.update(ob, ts) == "gap":
                self.resync(mid)
                return
            self.book_at = time.time()
            for f in self.book_listeners:
                f()
        elif ch == f"trade:{mid}":
            trades = list(msg.get("trades") or []) + list(msg.get("liquidation_trades") or [])
            if not trades or msg.get("type", "").startswith("subscribed"):
                return     # the subscribe reply repeats recent trades: history, not new flow
            now = time.time()
            for t in trades:
                self.flow.append((now, float(t.get("usd_amount") or 0)))
            while self.flow and self.flow[0][0] < now - 300:
                self.flow.popleft()
            for f in self.trade_listeners:
                f(trades)

    def resync(self, mid: str) -> None:
        """Re-subscribe the book for a fresh snapshot, one at a time. Lighter sends a book update every 50 ms, and each
        one that arrives before the snapshot also reads as a gap: a re-subscribe for each would burst unsubscribe /
        subscribe pairs (200 client messages a minute per IP), and a late unsubscribe could drop the new snapshot."""
        now = time.time()
        if self.resync_at and now - self.resync_at < RESYNC_RETRY_S:
            return
        self.resync_at = now
        log.info("book_gap", market=self.market.symbol, gaps=self.book.gaps)
        self._resync_task = asyncio.get_event_loop().create_task(self.ws.resubscribe(f"order_book/{mid}"))

    def fresh(self, max_age_s: float = 10.0) -> bool:
        """The book is synced and the connection alive (the height channel sends a frame every 0.5 s). A quiet book may
        not change for many seconds; that is no reason to pull the quotes, a dead feed is."""
        return self.book.synced and self.ws.connected.is_set() and time.time() - self.ws.last_msg < max_age_s

    def bbo(self) -> tuple[float, float, float, float] | None:
        b, a = self.book.best_bid(), self.book.best_ask()
        if b is None or a is None or b[0] >= a[0]:
            return None
        return b[0], a[0], b[1], a[1]

    def flow_usd_5m(self) -> float:
        return sum(u for _, u in self.flow)
