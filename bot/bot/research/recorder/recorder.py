"""24/7 market-data recorder (P0 task 7). PUBLIC data only: no keys, no orders.

Tables: book_deltas, book_snapshots (60 s, top 100), bbo, trades, mark_oracle_index, funding_pred, funding_paid,
market_attrs, param_changes, clock_offset, recorder_health (latency_probe is written by probes, not here).
Every row carries recv_ts_us plus the venue's own timestamp/sequence fields.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.common.config import ArcusVenueConfig, LighterVenueConfig
from bot.common.decimal import D
from bot.common.logging import Log
from bot.common.ratelimit import TokenBucket
from bot.common.time import now_us, ntp_offset_us
from bot.core.alerts import Alerter
from bot.core.book import ArcusBookSync, LighterBookSync, SyncResult
from bot.core.liveparams import LiveParams, ParamChange
from bot.research.recorder.writer import ParquetWriter, disk_usage_frac
from bot.venues.arcus.rest import ArcusRest
from bot.venues.arcus.ws import ArcusWS
from bot.venues.base import Venue
from bot.venues.lighter_rh.models import funding_sign
from bot.venues.lighter_rh.rest import LighterRest
from bot.venues.lighter_rh.ws import LighterWS
from bot.venues.symbols import canonical_base

log = Log("recorder")
A, L = Venue.ARCUS.value, Venue.LIGHTER_RH.value
SILENCE_S = 60.0
BOOK_SNAPSHOT_S = 60.0


def _s(x: Any) -> str | None:
    return None if x is None else str(x)


class Recorder:
    def __init__(self, universe: list[str], arcus_cfg: ArcusVenueConfig, lighter_cfg: LighterVenueConfig, *,
                 data_dir: Path, alerter: Alerter | None = None, n_levels: int = 100,
                 flush_s: float = 30.0) -> None:
        self.universe = [u.upper() for u in universe]
        self.data_dir = data_dir
        self.writer = ParquetWriter(data_dir, flush_s=flush_s)
        self.alerter = alerter or Alerter()
        self.n_levels = n_levels
        # The recorder uses at most 50% of Arcus's per-IP weight bucket (P0 task 5).
        self.arcus_rest = ArcusRest(arcus_cfg.rest.mainnet, ip_bucket=TokenBucket(750, 12.5))
        self.lighter_rest = LighterRest(lighter_cfg.rest.mainnet, rest_per_min=30)
        self.arcus_ws = ArcusWS(arcus_cfg.ws.mainnet, n_levels=n_levels)
        self.lighter_ws = LighterWS(lighter_cfg.ws.mainnet, readonly=True)
        self.params = LiveParams(arcus=self.arcus_rest, lighter=self.lighter_rest,
                                 out_dir=data_dir / "param_changes_jsonl")
        self.params.listeners.append(self._on_param_changes)
        self.arcus_display: dict[str, str] = {}  # base -> "BTC-USD"
        self.lighter_ids: dict[str, int] = {}  # base -> market_id
        self._msg_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._seen_trades: dict[tuple[str, str], deque[str]] = defaultdict(lambda: deque(maxlen=5000))
        self._seen_funding: set[tuple[str, str, int]] = set()
        self._last_attrs: dict[tuple[str, ...], tuple[Any, ...]] = {}
        self._last_lighter_attrs_us: dict[str, int] = {}
        self._last_index: dict[str, tuple[int, Decimal]] = {}  # lighter base -> (ts, index)
        self._silent: set[str] = set()
        self._tasks: list[asyncio.Task[Any]] = []
        self._stop = asyncio.Event()
        self.started_us = 0

    # ---------------------------------------------------------------- setup
    async def start(self) -> None:
        self.started_us = now_us()
        await self.params.refresh()
        sm = self.params.symbol_map
        for base in self.universe:
            if sm.has(base, Venue.ARCUS):
                self.arcus_display[base] = sm.get(base, Venue.ARCUS).venue_symbol
            if sm.has(base, Venue.LIGHTER_RH):
                self.lighter_ids[base] = sm.get(base, Venue.LIGHTER_RH).venue_market_id
        missing = [b for b in self.universe if b not in self.arcus_display or b not in self.lighter_ids]
        if missing:
            log.warning("universe_missing", reason="not listed on both venues", data={"markets": missing})
        self._wire_arcus()
        self._wire_lighter()
        for disp in self.arcus_display.values():
            await self.arcus_ws.subscribe_market(disp)
        await self.arcus_ws.subscribe_global()
        for base, mid in self.lighter_ids.items():
            await self.lighter_ws.subscribe_market(mid, base)
        self.arcus_ws.start()
        self.lighter_ws.start()
        loops = [self._flusher(), self._snapshotter(), self._health(), self._funding_poller(), self._params_loop(),
                 self._ntp_loop(), self._disk_guard()]
        self._tasks = [asyncio.create_task(c) for c in loops]
        log.info("recorder_started", data={"arcus": self.arcus_display, "lighter": self.lighter_ids,
                                           "data_dir": str(self.data_dir)})

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await self.arcus_ws.stop()
        await self.lighter_ws.stop()
        self.writer.flush()
        await self.arcus_rest.close()
        await self.lighter_rest.close()
        await self.alerter.close()

    async def run_for(self, seconds: float) -> None:
        await self.start()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        await self.stop()

    # ---------------------------------------------------------------- Arcus handlers
    def _wire_arcus(self) -> None:
        ws = self.arcus_ws
        ws.on("book", self._arcus_book)
        ws.on("bbo", self._arcus_bbo)
        ws.on("trades", self._arcus_trades)
        ws.on("oracle", self._arcus_oracle)
        ws.on("predicted_funding", self._arcus_pred_funding)
        ws.on("markets", self._arcus_markets)
        ws.on("market_attrs", self._arcus_market_attrs)

    def _count(self, venue: str, market: str, ch: str) -> None:
        self._msg_counts[(venue, market, ch)] += 1

    def _arcus_book(self, base: str, sync: ArcusBookSync, res: SyncResult, recv: int, snapshot: bool,
                    c: dict[str, Any]) -> None:
        self._count(A, base, "book")
        if res not in (SyncResult.APPLIED,):
            return
        seq = int(c.get("lastSequenceId") or 0)
        gseq = int(c.get("globalSequenceId") or 0)
        vts = int(c.get("timestamp") or 0)
        for side, rows in (("b", c.get("bids") or []), ("a", c.get("asks") or [])):
            for p, s in rows:
                self.writer.add("book_deltas", {"recv_ts_us": recv, "venue_ts_us": vts, "venue": A, "market": base,
                                                "seq": seq, "global_seq": gseq, "side": side, "price": p, "size": s,
                                                "is_snapshot": snapshot}, ts_us=recv, venue=A, market=base)

    def _arcus_bbo(self, base: str, c: dict[str, Any], recv: int) -> None:
        self._count(A, base, "bbo")
        b, a = c.get("bestBid") or {}, c.get("bestAsk") or {}
        self.writer.add("bbo", {"recv_ts_us": recv, "venue_ts_us": int(c.get("timestamp") or 0), "venue": A,
                                "market": base, "seq": int(c.get("lastSequenceId") or 0), "bid_px": b.get("price"),
                                "bid_sz": b.get("size"), "ask_px": a.get("price"), "ask_sz": a.get("size")},
                        ts_us=recv, venue=A, market=base)

    def _arcus_trades(self, base: str, rows: list[dict[str, Any]], recv: int) -> None:
        self._count(A, base, "trades")
        seen = self._seen_trades[(A, base)]
        for t in rows:
            tid = str(t.get("tradeId"))
            if tid in seen:
                continue
            seen.append(tid)
            self.writer.add("trades", {
                "recv_ts_us": recv, "venue_ts_us": int(t.get("timestamp") or 0), "venue": A, "market": base,
                "trade_id": tid, "price": t.get("price"), "size": t.get("size"),
                "taker_side": "buy" if t.get("side") == "BUY" else "sell", "maker_address": t.get("makerAddress"),
                "taker_address": t.get("takerAddress"), "maker_order_id": t.get("makerOrderId"),
                "seq": t.get("sequenceNumber"), "is_liquidation": False}, ts_us=recv, venue=A, market=base)

    def _arcus_oracle(self, prices: list[dict[str, Any]], recv: int) -> None:
        for p in prices:
            base = canonical_base(Venue.ARCUS, p.get("marketDisplayName", ""))
            if base not in self.arcus_display:
                continue
            self._count(A, base, "oracle")
            vts = int(p.get("markEpochNanos") or 0) // 1000
            self.writer.add("mark_oracle_index", {"recv_ts_us": recv, "venue_ts_us": vts, "venue": A, "market": base,
                                                  "mark": p.get("markPrice"), "oracle": p.get("price"), "index": None,
                                                  "impact_bid": None, "impact_ask": None},
                            ts_us=recv, venue=A, market=base)

    def _arcus_pred_funding(self, base: str, c: dict[str, Any], recv: int) -> None:
        self._count(A, base, "predicted_funding")
        nxt = (self.params.markets.get(Venue.ARCUS, {}).get(base))
        nfa = int(nxt.extra.get("nextFundingAt") or 0) * 1_000_000 if nxt else 0  # type: ignore[call-overload]
        self.writer.add("funding_pred", {"recv_ts_us": recv, "venue": A, "market": base,
                                         "predicted_rate_h": float(c.get("rate1h") or 0), "next_funding_ts_us": nfa,
                                         "source": "ws.predictedFunding", "premium": None},
                        ts_us=recv, venue=A, market=base)

    def _attrs_row(self, venue: str, base: str, recv: int, m: dict[str, Any], event: str | None) -> None:
        row = {"recv_ts_us": recv, "venue": venue, "market": base, "is_outside_rth": m.get("isOutsideRth"),
               "settlement_price": _s(m.get("currentSettlementPrice")), "upper_bound": _s(m.get("upperTradingBound")),
               "lower_bound": _s(m.get("lowerTradingBound")), "next_upper": _s(m.get("nextUpperTradingBound")),
               "next_lower": _s(m.get("nextLowerTradingBound")), "upper_in_zone": m.get("isUpperInExpansionZone"),
               "lower_in_zone": m.get("isLowerInExpansionZone"),
               "upper_zone_entered_ts": _ts_us(m.get("upperZoneEnteredAt")),
               "lower_zone_entered_ts": _ts_us(m.get("lowerZoneEnteredAt")), "bound_event": event,
               "imf": _s(m.get("initialMarginFraction")), "offhours_imf": _s(m.get("offHoursInitialMarginFraction")),
               "oi": _s(m.get("openInterest")), "oi_cap": _s(m.get("openInterestCapNotional"))}
        key = (venue, base)
        sig = tuple(v for k, v in row.items() if k not in ("recv_ts_us", "oi"))
        oi_changed = self._last_attrs.get((*key, "oi")) != (row["oi"],)
        if event is None and self._last_attrs.get(key) == sig and not oi_changed:
            return
        self._last_attrs[key] = sig
        self._last_attrs[(*key, "oi")] = (row["oi"],)
        self.writer.add("market_attrs", row, ts_us=recv, venue=venue, market=base)

    def _arcus_markets(self, markets: dict[str, dict[str, Any]], recv: int) -> None:
        for m in markets.values():
            base = canonical_base(Venue.ARCUS, m.get("marketDisplayName", ""))
            if base in self.arcus_display:
                self._count(A, base, "markets")
                self._attrs_row(A, base, recv, m, None)

    def _arcus_market_attrs(self, c: dict[str, Any], recv: int) -> None:
        for e in c.get("entries") or []:
            base = canonical_base(Venue.ARCUS, e.get("marketDisplayName", ""))
            if base in self.arcus_display:
                ev = e.get("boundEvent") or ("snapshot" if c.get("isSnapshot") else "update")
                self._attrs_row(A, base, recv, e, ev)

    # ---------------------------------------------------------------- Lighter handlers
    def _wire_lighter(self) -> None:
        ws = self.lighter_ws
        ws.on("book", self._lighter_book)
        ws.on("ticker", self._lighter_ticker)
        ws.on("trades", self._lighter_trades)
        ws.on("market_stats", self._lighter_stats)

    def _lighter_book(self, base: str, sync: LighterBookSync, res: SyncResult, recv: int, snapshot: bool,
                      m: dict[str, Any]) -> None:
        self._count(L, base, "book")
        if res is not SyncResult.APPLIED:
            return
        ob = m.get("order_book") or {}
        nonce = int(ob.get("nonce") or 0)
        vts = int(ob.get("last_updated_at") or m.get("last_updated_at") or 0)
        for side, rows in (("b", ob.get("bids") or []), ("a", ob.get("asks") or [])):
            for lv in rows:
                self.writer.add("book_deltas", {"recv_ts_us": recv, "venue_ts_us": vts, "venue": L, "market": base,
                                                "seq": nonce, "global_seq": int(ob.get("begin_nonce") or 0),
                                                "side": side, "price": lv["price"], "size": lv["size"],
                                                "is_snapshot": snapshot}, ts_us=recv, venue=L, market=base)

    def _lighter_ticker(self, base: str, m: dict[str, Any], recv: int) -> None:
        self._count(L, base, "bbo")
        t = m.get("ticker") or {}
        b, a = t.get("b") or {}, t.get("a") or {}
        self.writer.add("bbo", {"recv_ts_us": recv, "venue_ts_us": int(t.get("last_updated_at") or 0), "venue": L,
                                "market": base, "seq": int(m.get("nonce") or 0), "bid_px": b.get("price"),
                                "bid_sz": b.get("size"), "ask_px": a.get("price"), "ask_sz": a.get("size")},
                        ts_us=recv, venue=L, market=base)

    def _lighter_trades(self, base: str, trades: list[dict[str, Any]], liq: list[dict[str, Any]], recv: int) -> None:
        self._count(L, base, "trades")
        seen = self._seen_trades[(L, base)]
        for t, is_liq in [(x, False) for x in trades] + [(x, True) for x in liq]:
            tid = str(t.get("trade_id_str") or t.get("trade_id"))
            if tid in seen:
                continue
            seen.append(tid)
            vts = int(t.get("transaction_time") or 0) or int(t.get("timestamp") or 0) * 1000
            self.writer.add("trades", {
                "recv_ts_us": recv, "venue_ts_us": vts, "venue": L, "market": base, "trade_id": tid,
                "price": t.get("price"), "size": t.get("size"),
                "taker_side": "buy" if t.get("is_maker_ask") else "sell",
                "maker_address": str(t.get("ask_account_id") if t.get("is_maker_ask") else t.get("bid_account_id")),
                "taker_address": str(t.get("bid_account_id") if t.get("is_maker_ask") else t.get("ask_account_id")),
                "maker_order_id": str(t.get("ask_id") if t.get("is_maker_ask") else t.get("bid_id")),
                "seq": t.get("block_height"), "is_liquidation": is_liq or t.get("type") in ("liquidation", "deleverage"),
            }, ts_us=recv, venue=L, market=base)

    def _lighter_stats(self, base: str, s: dict[str, Any], recv: int) -> None:
        if base not in self.lighter_ids:
            return
        self._count(L, base, "market_stats")
        idx = s.get("index_price")
        if idx:
            self._last_index[base] = (recv, D(idx))
        self.writer.add("mark_oracle_index", {"recv_ts_us": recv, "venue_ts_us": recv, "venue": L, "market": base,
                                              "mark": s.get("mark_price"), "oracle": None, "index": idx,
                                              "impact_bid": None, "impact_ask": None}, ts_us=recv, venue=L, market=base)
        cfr, prem = s.get("current_funding_rate"), s.get("premium")
        self.writer.add("funding_pred", {
            "recv_ts_us": recv, "venue": L, "market": base,
            "predicted_rate_h": float(cfr) / 100 if cfr not in (None, "") else None,  # percent per hour -> fraction
            "next_funding_ts_us": ((recv // 3_600_000_000) + 1) * 3_600_000_000,
            "source": "ws.market_stats.current_funding_rate(pct,rounded)",
            "premium": float(prem) / 100 if prem not in (None, "") else None}, ts_us=recv, venue=L, market=base)
        if recv - self._last_lighter_attrs_us.get(base, 0) >= 60_000_000:
            self._last_lighter_attrs_us[base] = recv
            self.writer.add("market_attrs", {"recv_ts_us": recv, "venue": L, "market": base, "is_outside_rth": None,
                                             "oi": _s(s.get("open_interest")), "oi_cap": _s(s.get("open_interest_limit")),
                                             "bound_event": None}, ts_us=recv, venue=L, market=base)

    # ---------------------------------------------------------------- periodic loops
    async def _flusher(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.writer.flush_s)
            n = await asyncio.to_thread(self.writer.flush)
            if n:
                log.debug("flushed", data={"rows": n})

    async def _snapshotter(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(BOOK_SNAPSHOT_S)
            recv = now_us()
            for disp, sync in list(self.arcus_ws.books.items()):
                self._snapshot_row(A, canonical_base(Venue.ARCUS, disp), sync.book, recv)
            for mid, lsync in list(self.lighter_ws.books.items()):
                self._snapshot_row(L, self.lighter_ws.base_by_id.get(mid, str(mid)), lsync.book, recv)

    def _snapshot_row(self, venue: str, base: str, book: Any, recv: int) -> None:
        if book.seq is None:
            return
        bids = [{"price": str(p), "size": str(s)} for p, s in book.levels(True, self.n_levels)]
        asks = [{"price": str(p), "size": str(s)} for p, s in book.levels(False, self.n_levels)]
        self.writer.add("book_snapshots", {"recv_ts_us": recv, "venue_ts_us": book.ts_us, "venue": venue,
                                           "market": base, "seq": book.seq, "bids": bids, "asks": asks,
                                           "n_levels": self.n_levels}, ts_us=recv, venue=venue, market=base)

    async def _health(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(60)
            ts = now_us()
            counts = dict(self._msg_counts)
            self._msg_counts.clear()
            sources: list[tuple[str, Any, Callable[[str], int]]] = [(A, self.arcus_ws.ws, self._arcus_gaps),
                                                                    (L, self.lighter_ws.ws, self._lighter_gaps)]
            for venue, ws, gaps_fn in sources:
                for key, last in list(ws.last_msg_us.items()):
                    ch, _, mk = key.partition(":") if venue == A else key.partition("/")
                    base = self._base_for(venue, mk) if mk else "_all"
                    age_ms = (ts - last) // 1000
                    self.writer.add("recorder_health", {
                        "ts_us": ts, "venue": venue, "market": base, "channel": ch, "last_msg_age_ms": age_ms,
                        "msgs_per_min": counts.get((venue, base, _ch_norm(ch)), 0), "resubscribes": ws.resubscribes,
                        "gaps": gaps_fn(mk)}, ts_us=ts, venue=venue, market=base)
                    is_book = ch in ("l2u", "order_book")
                    skey = f"{venue}:{key}"
                    if is_book and age_ms > SILENCE_S * 1000 and skey not in self._silent:
                        self._silent.add(skey)
                        self.alerter.warn(f"silent:{skey}", f"recorder: {venue} {key} silent for {age_ms // 1000}s")
                    elif age_ms <= SILENCE_S * 1000 and skey in self._silent:
                        self._silent.discard(skey)
                        self.alerter.info(f"recovered:{skey}", f"recorder: {venue} {key} recovered")

    def _arcus_gaps(self, disp: str) -> int:
        s = self.arcus_ws.books.get(disp)
        return s.gaps if s else 0

    def _lighter_gaps(self, mk: str) -> int:
        s = self.lighter_ws.books.get(int(mk)) if mk.isdigit() else None
        return s.gaps if s else 0

    def _base_for(self, venue: str, mk: str) -> str:
        if venue == A:
            return canonical_base(Venue.ARCUS, mk)
        return self.lighter_ws.base_by_id.get(int(mk), mk) if mk.isdigit() else mk

    async def _funding_poller(self) -> None:
        await asyncio.sleep(5)
        while not self._stop.is_set():
            try:
                await self.poll_funding_once()
            except Exception as e:
                log.warning("funding_poll_failed", reason=type(e).__name__, data={"err": str(e)[:200]})
            await asyncio.sleep(600)

    async def poll_funding_once(self) -> None:
        for base, disp in self.arcus_display.items():
            for f in await self.arcus_rest.funding_rates(disp, limit=3):
                key = (A, base, int(f["time"]))
                if key in self._seen_funding:
                    continue
                self._seen_funding.add(key)
                self.writer.add("funding_paid", {"funding_ts_us": int(f["time"]), "venue": A, "market": base,
                                                 "rate_h": float(f["fundingRate"]), "rate_raw": f["fundingRate"],
                                                 "value_per_unit": None, "pay_price": None, "pay_price_type": "oracle",
                                                 "index_source": None}, ts_us=int(f["time"]), venue=A, market=base)
        end = int(time.time())
        for base, mid in self.lighter_ids.items():
            for f in await self.lighter_rest.fundings(mid, start_s=end - 3 * 3600, end_s=end, count_back=3):
                ts = int(f["timestamp"]) * 1_000_000
                key = (L, base, ts)
                if key in self._seen_funding:
                    continue
                self._seen_funding.add(key)
                idx = self._last_index.get(base)
                pay = float(idx[1]) if idx and abs(idx[0] - ts) < 3_600_000_000 else None
                rate = funding_sign(f["direction"]) * float(f["value"]) / pay if pay else None
                self.writer.add("funding_paid", {
                    "funding_ts_us": ts, "venue": L, "market": base,
                    "rate_h": rate if rate is not None else funding_sign(f["direction"]) * float(f["rate"]) / 100,
                    "rate_raw": f["rate"], "value_per_unit": f["value"], "pay_price": pay, "pay_price_type": "index",
                    "index_source": "recorded_market_stats" if pay else "rounded_rate_pct"}, ts_us=ts, venue=L,
                    market=base)

    async def _params_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(3600)
            try:
                await self.params.refresh()
            except Exception as e:
                log.warning("params_refresh_failed", reason=type(e).__name__)

    def _on_param_changes(self, changes: list[ParamChange]) -> None:
        for c in changes:
            self.writer.add("param_changes", asdict(c), ts_us=c.ts_us, venue=c.venue)
        real = [c for c in changes if c.field not in ("__listed__",)]
        if real and self.started_us and changes[0].ts_us > self.started_us + 60_000_000:
            self.alerter.warn("param_change", f"venue parameter changes: {[(c.venue, c.market, c.field) for c in real][:10]}")

    async def _ntp_loop(self) -> None:
        while not self._stop.is_set():
            off = await asyncio.to_thread(ntp_offset_us)
            ts = now_us()
            self.writer.add("clock_offset", {"ts_us": ts, "ntp_offset_us": off if off is not None else None,
                                             "source": "pool.ntp.org" if off is not None else "unreachable"},
                            ts_us=ts, venue="local")
            if off is not None and abs(off) > 250_000:
                self.alerter.warn("clock", f"clock offset {off / 1000:.0f} ms vs NTP: fix chrony before trading")
            await asyncio.sleep(600)

    async def _disk_guard(self) -> None:
        while not self._stop.is_set():
            frac = disk_usage_frac(self.data_dir)
            if frac >= 0.95 and "book_deltas" not in self.writer.disabled_tables:
                self.writer.disabled_tables.add("book_deltas")
                self.alerter.crit("disk", f"disk {frac:.0%} full: stopped writing book_deltas (others continue)")
            elif frac >= 0.80:
                self.alerter.warn("disk", f"disk {frac:.0%} full")
            elif frac < 0.90 and "book_deltas" in self.writer.disabled_tables:
                self.writer.disabled_tables.discard("book_deltas")
            await asyncio.sleep(300)


def _ts_us(v: Any) -> int | None:
    """Arcus zone timestamps: accept s/ms/µs integers."""
    if v in (None, ""):
        return None
    n = int(v)
    if n < 10**11:
        return n * 1_000_000
    if n < 10**14:
        return n * 1_000
    return n


def _ch_norm(ch: str) -> str:
    return {"l2u": "book", "order_book": "book", "ticker": "bbo", "trade": "trades", "pf": "predicted_funding",
            "oraclePrices": "oracle"}.get(ch, ch)
