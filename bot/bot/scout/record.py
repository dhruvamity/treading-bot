"""Records the best bid/offer and every trade for all online Arcus perps (public data, no keys).

With `depth=True` it also keeps each market's order book and samples the top 10 levels once a second when the book
changed (queue-position data for sizing fills of larger orders).

Two WebSocket connections (three with depth; Arcus allows 100 subscriptions per connection). Rows are buffered per market and written
every `flush_s` to the TapeStore as one part per market per hour, so a crash loses at most one flush. The market list
is re-read hourly: new listings are picked up, delisted ones dropped. Recording pauses when free disk falls under
`min_free_gb`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from bot.common.logging import Log
from bot.core.book import ArcusBookSync, SyncResult
from bot.scout.tape import DEPTH_N, BboBuffer, DepthBuffer, TapeStore, TradeBuffer
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import Venue
from bot.venues.symbols import canonical_base

log = Log("scout.record")
SUBS_PER_CONN = 90


class ScoutRecorder:
    def __init__(self, root: Path, *, rest_url: str, ws_url: str, flush_s: float = 300.0,
                 min_free_gb: float = 5.0, depth: bool = False) -> None:
        self.root = root
        self.store = TapeStore(root / "tape")
        self.rest = ArcusRest(rest_url)
        self.ws_url = ws_url
        self.flush_s = flush_s
        self.min_free_gb = min_free_gb
        self.session = time.strftime("%H%M%S", time.gmtime())
        self.display: dict[str, str] = {}          # base -> "BTC-USD"
        self.bbo: dict[str, BboBuffer] = {}
        self.trades: dict[str, TradeBuffer] = {}
        self.depth = depth
        self.depth_buf: dict[str, DepthBuffer] = {}
        self.books: dict[str, ArcusBookSync] = {}   # display -> live book (depth only)
        self.dirty: set[str] = set()                 # books changed since the last depth sample
        self.parts: dict[tuple[str, str], list[dict[str, np.ndarray]]] = {}   # (market, kind) -> this hour's rows
        self.part_hour = -1
        self.conns: list[ArcusWS] = []
        self.rows = 0
        self.last_msg = 0.0
        self.paused_disk = False
        self._stop = asyncio.Event()

    # ---------------------------------------------------------------- markets
    async def refresh_markets(self) -> list[str]:
        ms = await self.rest.markets()
        (self.root / "markets.json").write_text(json.dumps({"markets": ms, "fetched": time.time()}))
        online = sorted(m["marketDisplayName"] for m in ms if m.get("status") == "ONLINE")
        for d in online:
            base = canonical_base(Venue.ARCUS, d)
            self.display[base] = d
            self.bbo.setdefault(d, BboBuffer())
            self.trades.setdefault(d, TradeBuffer())
            if self.depth:
                self.depth_buf.setdefault(d, DepthBuffer())
        return online

    def _on_bbo(self, base: str, c: dict[str, Any], recv_us: int) -> None:
        d = self.display.get(base)
        b, a = (c or {}).get("bestBid") or {}, (c or {}).get("bestAsk") or {}
        if d is None or self.paused_disk or not b.get("price") or not a.get("price"):
            return
        self.last_msg = time.time()
        self.bbo[d].add(int(c.get("timestamp") or recv_us), float(b["price"]), float(a["price"]),
                        float(b.get("size") or 0), float(a.get("size") or 0))

    def _on_trades(self, base: str, rows: list[dict[str, Any]], recv_us: int) -> None:
        d = self.display.get(base)
        if d is None or self.paused_disk:
            return
        self.last_msg = time.time()
        for t in rows:
            try:
                self.trades[d].add_raw(t)
            except (KeyError, TypeError, ValueError):
                continue

    def _on_book(self, base: str, sync: ArcusBookSync, res: SyncResult, recv_us: int, *_: Any) -> None:
        d = self.display.get(base)
        if d is not None and res is SyncResult.APPLIED and not self.paused_disk:
            self.books[d] = sync
            self.dirty.add(d)

    def sample_depth(self, now_us: int) -> None:
        """One row per changed book: the top levels as floats, best first."""
        for d in list(self.dirty):
            sync = self.books.get(d)
            buf = self.depth_buf.get(d)
            if sync is None or buf is None:
                continue
            bk = sync.book
            buf.add(int(bk.ts_us or now_us), [(float(p), float(q)) for p, q in bk.levels(True, DEPTH_N)],
                    [(float(p), float(q)) for p, q in bk.levels(False, DEPTH_N)])
        self.dirty.clear()

    async def _sample_loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            self.sample_depth(int(time.time() * 1e6))

    async def _connect(self, markets: list[str]) -> None:
        for c in self.conns:
            await c.stop()
        self.conns = []
        per = SUBS_PER_CONN // (3 if self.depth else 2)
        for i in range(0, len(markets), per):
            ws = ArcusWS(self.ws_url, n_levels=DEPTH_N if self.depth else 1)
            ws.on("bbo", self._on_bbo)
            ws.on("trades", self._on_trades)
            if self.depth:
                ws.on("book", self._on_book)
            ws.start()
            for d in markets[i:i + per]:
                await ws.subscribe_market(d, book=self.depth, bbo=True, trades=True, predicted_funding=False)
            self.conns.append(ws)
        log.info("scout_record_subscribed", data={"markets": len(markets), "connections": len(self.conns)})

    # ---------------------------------------------------------------- writing
    def flush(self) -> int:
        free_gb = shutil.disk_usage(self.root).free / 1e9
        self.paused_disk = free_gb < self.min_free_gb
        hour = int(time.time() // 3600)
        if hour != self.part_hour:
            self.parts.clear()
            self.part_hour = hour
        n = 0
        for d in list(self.bbo):
            bufs: list[tuple[str, Any]] = [("bbo", self.bbo[d]), ("trades", self.trades[d])]
            if d in self.depth_buf:
                bufs.append(("depth", self.depth_buf[d]))
            for kind, buf in bufs:
                new = buf.take()
                if not len(new["ts"]):
                    continue
                n += len(new["ts"])
                chunks = self.parts.setdefault((d, kind), [])
                chunks.append(new)
                rows = {k: np.concatenate([c[k] for c in chunks]) for k in new}
                self.store.write_part(d, kind, f"rec{self.session}-{hour}", rows)
        self.rows += n
        status = {"ts": time.time(), "markets": len(self.bbo), "rows_total": self.rows, "rows_last_flush": n,
                  "last_msg_age_s": round(time.time() - self.last_msg, 1) if self.last_msg else None,
                  "free_gb": round(free_gb, 1), "paused_for_disk": self.paused_disk}
        (self.root / "recorder.json").write_text(json.dumps(status))
        if self.paused_disk:
            log.warning("scout_record_disk", reason=f"only {free_gb:.1f} GB free: recording paused")
        return n

    async def run(self, duration_s: float | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        markets = await self.refresh_markets()
        await self._connect(markets)
        sampler = asyncio.create_task(self._sample_loop()) if self.depth else None
        start = last_mk = time.time()
        try:
            while not self._stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.flush_s)
                self.flush()
                if time.time() - last_mk > 3600:
                    last_mk = time.time()
                    now = await self.refresh_markets()
                    if now != markets:
                        markets = now
                        await self._connect(markets)
                if duration_s is not None and time.time() - start >= duration_s:
                    break
        finally:
            if sampler is not None:
                self._stop.set()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(sampler, 5)
            self.flush()
            for c in self.conns:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(c.stop(), 5)
            await self.rest.close()

    def stop(self) -> None:
        self._stop.set()
