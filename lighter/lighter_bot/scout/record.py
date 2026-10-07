"""The recorder: every active Lighter perp, around the clock, into the tape (lighter_bot/scout/tape.py).

Two WebSocket connections: one carries the order books (heavy: a change every 50 ms per market), the other the
tickers (best bid/offer on every change), the trades and market_stats/all (mark, index, funding). Rows are buffered
and written as a new part per market and kind every `flush_s` (and on exit), so a crash loses at most that much.
The market list is re-read every 10 minutes (one request): new listings are added, delisted ones dropped.
Recording pauses under `min_free_gb` of free disk. data/recorder.json shows its health.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from lighter_bot.log import Log
from lighter_bot.scout.tape import DEPTH, FIELDS, Tape, day_of
from lighter_bot.venue.book import Book
from lighter_bot.venue.market import Market, parse_markets
from lighter_bot.venue.rest import Rest
from lighter_bot.venue.ws import WsClient

log = Log("record")
US = 1_000_000


def grp_of(tx_hash: str) -> int:
    return int.from_bytes(hashlib.blake2b(tx_hash.encode(), digest_size=8).digest(), "little") & ((1 << 62) - 1)


def trade_row(t: dict[str, Any], liq: bool = False) -> tuple[Any, ...]:
    """One Trade JSON -> a tape row (ts, px, sz, buy, grp, taker, maker, liq, tid). is_maker_ask: the resting order
    was the ask, so the taker bought."""
    ts = int(t.get("transaction_time") or 0) or int(t.get("timestamp") or 0) * 1000
    buy = bool(t.get("is_maker_ask"))
    bid_acct, ask_acct = int(t.get("bid_account_id") or -1), int(t.get("ask_account_id") or -1)
    taker, maker = (bid_acct, ask_acct) if buy else (ask_acct, bid_acct)
    return (ts, float(t["price"]), float(t["size"]), buy, grp_of(str(t.get("tx_hash", ""))), taker, maker,
            liq or t.get("type") == "liquidation", int(t.get("trade_id") or 0))


def save_markets(data_dir: Path, markets: dict[str, Market]) -> None:
    p = data_dir / "markets.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v.as_dict() for k, v in markets.items()}, indent=1))
    os.replace(tmp, p)


def load_markets(data_dir: Path) -> dict[str, Market]:
    try:
        return {k: Market.from_dict(v) for k, v in json.loads((data_dir / "markets.json").read_text()).items()}
    except (OSError, ValueError):
        return {}


class Recorder:
    def __init__(self, data_dir: Path, rest: Rest, ws_url: str, *, markets: list[str] | None = None,
                 flush_s: float = 300.0, min_free_gb: float = 5.0, depth: bool = True) -> None:
        self.data_dir = data_dir
        self.tape = Tape(data_dir / "tape")
        self.rest = rest
        self.only = set(markets) if markets else None
        self.flush_s = flush_s
        self.min_free_gb = min_free_gb
        self.depth_on = depth
        self.part = f"rec{time.strftime('%H%M%S')}{os.getpid() % 1000:03d}"
        self.seq = 0
        self.markets: dict[str, Market] = {}
        self.by_id: dict[int, str] = {}
        self.books: dict[int, Book] = {}
        self.dirty: set[int] = set()
        self.buf: dict[tuple[str, str], list[tuple[Any, ...]]] = defaultdict(list)
        self.rows = 0
        self.paused_for_disk = False
        self.ws_books = WsClient(ws_url + "?readonly=true", name="books")
        self.ws_flow = WsClient(ws_url + "?readonly=true", name="flow")
        self.ws_books.add_handler(self.on_msg)
        self.ws_flow.add_handler(self.on_msg)
        self._stop = asyncio.Event()
        self._last_stats: dict[str, tuple[int, float, float, float]] = {}

    # ---------------------------------------------------------------- markets
    async def refresh_markets(self) -> None:
        try:
            ms = parse_markets(await self.rest.markets())
        except Exception as e:
            log.warn("markets_refresh_failed", err=str(e))
            return
        save_markets(self.data_dir, ms)
        want = {s: m for s, m in ms.items() if m.active and not m.hidden and (self.only is None or s in self.only)}
        for sym, m in want.items():
            if sym not in self.markets:
                self.markets[sym] = m
                self.by_id[m.market_id] = sym
                if self.depth_on:
                    self.books[m.market_id] = Book(m.market_id)
                    await self.ws_books.subscribe(f"order_book/{m.market_id}")
                await self.ws_flow.subscribe(f"ticker/{m.market_id}")
                await self.ws_flow.subscribe(f"trade/{m.market_id}")
                log.info("market_added", market=sym, id=m.market_id)
        for sym in [s for s in self.markets if s not in want]:
            m = self.markets.pop(sym)
            self.by_id.pop(m.market_id, None)
            self.books.pop(m.market_id, None)
            await self.ws_books.unsubscribe(f"order_book/{m.market_id}")
            await self.ws_flow.unsubscribe(f"ticker/{m.market_id}")
            await self.ws_flow.unsubscribe(f"trade/{m.market_id}")
            log.info("market_dropped", market=sym)
        if "market_stats/all" not in self.ws_flow.subs:
            await self.ws_flow.subscribe("market_stats/all")

    # ---------------------------------------------------------------- messages
    def _add(self, sym: str, kind: str, row: tuple[Any, ...]) -> None:
        if not self.paused_for_disk:
            self.buf[(sym, kind)].append(row)
            self.rows += 1

    def on_msg(self, msg: dict[str, Any]) -> None:
        ch = msg.get("channel", "")
        typ = msg.get("type", "")
        if ch.startswith("order_book:"):
            mid = int(ch.split(":")[1])
            book = self.books.get(mid)
            ob = msg.get("order_book") or {}
            if book is None:
                return
            ts_ms = int(msg.get("timestamp") or 0)
            if typ.startswith("subscribed"):
                book.snapshot(ob, ts_ms)
            elif book.update(ob, ts_ms) == "gap":
                log.info("book_gap", market=self.by_id.get(mid))
                asyncio.get_event_loop().create_task(self.ws_books.resubscribe(f"order_book/{mid}"))
                return
            self.dirty.add(mid)
        elif ch.startswith("ticker:"):
            sym = self.by_id.get(int(ch.split(":")[1]))
            tk = msg.get("ticker") or {}
            a, b = tk.get("a") or {}, tk.get("b") or {}
            if sym is None or not a or not b:
                return
            ts = int(tk.get("last_updated_at") or msg.get("last_updated_at") or 0)
            if ts < 10**15:
                ts *= 1000
            self._add(sym, "bbo", (ts, float(b["price"]), float(a["price"]), float(b["size"]), float(a["size"])))
        elif ch.startswith("trade:"):
            sym = self.by_id.get(int(ch.split(":")[1]))
            if sym is None:
                return
            for liq, key in ((False, "trades"), (True, "liquidation_trades")):
                for t in msg.get(key) or []:
                    self._add(sym, "trades", trade_row(t, liq))
        elif ch.startswith("market_stats:"):
            stats = msg.get("market_stats") or {}
            items = stats.values() if isinstance(stats, dict) and "symbol" not in stats else [stats]
            ts = int(msg.get("timestamp") or time.time() * 1000) * 1000
            for s in items:
                if not isinstance(s, dict):
                    continue
                sym = self.by_id.get(int(s.get("market_id", -1)))
                if sym is None:
                    continue
                self._last_stats[sym] = (ts, float(s.get("mark_price") or "nan"), float(s.get("index_price") or "nan"),
                                         float(s.get("current_funding_rate") or 0) / 100)

    # ---------------------------------------------------------------- once a second: depth rows and stats rows
    async def sampler(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1.0 - (time.time() % 1.0))
            now_us = int(time.time() * US)
            for mid in list(self.dirty):
                book = self.books.get(mid)
                sym = self.by_id.get(mid)
                if book is None or sym is None or not book.synced:
                    continue
                bids, asks = book.depth(DEPTH)
                self._add(sym, "depth", (now_us, bids, asks))
            self.dirty.clear()
            for sym, row in list(self._last_stats.items()):
                self._add(sym, "stats", row)
            self._last_stats.clear()

    # ---------------------------------------------------------------- writing
    def flush(self) -> int:
        buf, self.buf = self.buf, defaultdict(list)
        n = 0
        self.seq += 1
        for (sym, kind), rows in buf.items():
            if not rows:
                continue
            by_day: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
            for r in rows:
                by_day[day_of(int(r[0]))].append(r)
            for day, rs in by_day.items():
                if kind == "depth":
                    k = len(rs)
                    arr = {x: np.zeros((k, DEPTH)) for x in ("bp", "bs", "ap", "as_")}
                    for i, (_, bids, asks) in enumerate(rs):
                        for j, (p, s) in enumerate(bids):
                            arr["bp"][i, j], arr["bs"][i, j] = p, s
                        for j, (p, s) in enumerate(asks):
                            arr["ap"][i, j], arr["as_"][i, j] = p, s
                    cols = {"ts": np.asarray([r[0] for r in rs], np.int64), **arr}
                else:
                    cols = {name: np.asarray([r[i] for r in rs], dtype=dt) for i, (name, dt) in enumerate(FIELDS[kind])}
                self.tape.write(sym, day, kind, cols, part=f"{self.part}-{self.seq:05d}")
                n += len(rs)
        return n

    def check_disk(self) -> None:
        free = shutil.disk_usage(self.data_dir).free / 2**30
        was = self.paused_for_disk
        self.paused_for_disk = free < self.min_free_gb
        if self.paused_for_disk != was:
            (log.warn if self.paused_for_disk else log.info)("disk", free_gb=round(free, 1),
                                                             paused=self.paused_for_disk)

    def health(self) -> dict[str, Any]:
        return {"t": time.time(), "markets": len(self.markets), "rows_total": self.rows,
                "last_msg_age_s": round(time.time() - max(self.ws_books.last_msg, self.ws_flow.last_msg), 1),
                "reconnects": self.ws_books.reconnects + self.ws_flow.reconnects,
                "book_gaps": sum(b.gaps for b in self.books.values()), "paused_for_disk": self.paused_for_disk}

    async def run(self, seconds: float | None = None) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.check_disk()
        tasks = [asyncio.create_task(self.ws_books.run()), asyncio.create_task(self.ws_flow.run()),
                 asyncio.create_task(self.sampler())]
        await self.refresh_markets()
        end = time.time() + seconds if seconds else None
        last_flush = last_refresh = time.time()
        try:
            while not self._stop.is_set():
                await asyncio.sleep(1.0)
                now = time.time()
                if now - last_flush >= self.flush_s:
                    n = self.flush()
                    last_flush = now
                    self.check_disk()
                    (self.data_dir / "recorder.json").write_text(json.dumps(self.health()))
                    log.info("flushed", rows=n, markets=len(self.markets))
                if now - last_refresh >= 600:
                    await self.refresh_markets()
                    last_refresh = now
                if end and now >= end:
                    break
        finally:
            self._stop.set()
            self.ws_books.stop()
            self.ws_flow.stop()
            for t in tasks:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            n = self.flush()
            (self.data_dir / "recorder.json").write_text(json.dumps(self.health()))
            log.info("stopped", rows_flushed=n)

    def stop(self) -> None:
        self._stop.set()
