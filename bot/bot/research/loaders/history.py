"""Historical loaders (P0 task 8).

- Funding history on both venues from the documented start dates (Arcus 2026-06-24 05:00 UTC, Lighter
  2026-06-26 07:00 UTC). Lighter: store both the rounded percent `rate` and the precise `value / index`
  reconstruction. Index history is not published, so the pay price comes from 1h MARK-price candles (open of the
  funding hour; flagged index_source="mark_candle_open"); the exact index is recorded live from market_stats.
- Arcus trades paging backwards (µs windows, newest first, overlapping pages deduped by tradeId).
- Candles: Arcus -> `candles_oracle` (OHLC is oracle-priced: NEVER simulate fills on them); Lighter ->
  `candles_trade`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.common.logging import Log
from bot.common.time import now_us
from bot.research.recorder.writer import ParquetWriter
from bot.venues.arcus.rest import ArcusRest
from bot.venues.lighter_rh.models import funding_sign
from bot.venues.lighter_rh.rest import LighterRest

log = Log("loaders")

ARCUS_FUNDING_START_US = 1_782_277_200_000_000  # 2026-06-24 05:00 UTC
LIGHTER_FUNDING_START_S = 1_782_457_200  # 2026-06-26 07:00 UTC
H_US = 3_600_000_000


@dataclass
class LoadStats:
    rows: int = 0
    calls: int = 0


async def load_arcus_funding(rest: ArcusRest, writer: ParquetWriter, base: str, display: str,
                             start_us: int = ARCUS_FUNDING_START_US, end_us: int | None = None) -> LoadStats:
    st = LoadStats()
    to_us = end_us or now_us()
    seen: set[int] = set()
    while to_us > start_us:
        rows = await rest.funding_rates(display, from_us=start_us, to_us=to_us, limit=1000)
        st.calls += 1
        if not rows:
            break
        for f in rows:
            t = int(f["time"])
            if t in seen:
                continue
            seen.add(t)
            writer.add("funding_paid", {"funding_ts_us": t, "venue": "arcus", "market": base,
                                        "rate_h": float(f["fundingRate"]), "rate_raw": f["fundingRate"],
                                        "value_per_unit": None, "pay_price": None, "pay_price_type": "oracle",
                                        "index_source": None}, ts_us=t, venue="arcus", market=base)
            st.rows += 1
        oldest = min(int(f["time"]) for f in rows)
        if oldest >= to_us or len(rows) < 1000:
            break
        to_us = oldest - 1
    return st


async def load_lighter_funding(rest: LighterRest, writer: ParquetWriter, base: str, market_id: int,
                               start_s: int = LIGHTER_FUNDING_START_S, end_s: int | None = None) -> LoadStats:
    st = LoadStats()
    end = end_s or now_us() // 1_000_000
    fundings: dict[int, dict[str, Any]] = {}
    marks: dict[int, float] = {}
    t = start_s
    while t < end:
        t2 = min(end, t + 480 * 3600)  # markPriceCandles returns at most 500 bars per call
        for f in await rest.fundings(market_id, start_s=t, end_s=t2, count_back=750):
            fundings[int(f["timestamp"])] = f
        st.calls += 1
        body = await rest._call("GET", "/api/v1/markPriceCandles", params={
            "market_id": market_id, "resolution": "1h", "start_timestamp": t, "end_timestamp": t2, "count_back": 750})
        st.calls += 1
        for c in body.get("c") or []:
            ts = int(c["t"])
            ts_s = ts // 1000 if ts > 10**11 else ts
            marks[ts_s] = float(c["o"])
        t = t2
    for ts_s, f in sorted(fundings.items()):
        pay = marks.get(ts_s)
        sgn = funding_sign(f["direction"])
        rate = sgn * float(f["value"]) / pay if pay else sgn * float(f["rate"]) / 100
        writer.add("funding_paid", {"funding_ts_us": ts_s * 1_000_000, "venue": "lighter_rh", "market": base,
                                    "rate_h": rate, "rate_raw": f["rate"], "value_per_unit": f["value"],
                                    "pay_price": pay, "pay_price_type": "index",
                                    "index_source": "mark_candle_open" if pay else "rounded_rate_pct"},
                   ts_us=ts_s * 1_000_000, venue="lighter_rh", market=base)
        st.rows += 1
    return st


async def load_arcus_trades(rest: ArcusRest, writer: ParquetWriter, base: str, display: str, *, start_us: int,
                            end_us: int | None = None, max_pages: int = 10_000) -> LoadStats:
    st = LoadStats()
    to_us = end_us or now_us()
    seen: set[str] = set()
    for _ in range(max_pages):
        rows = await rest.trades(display, from_us=start_us, to_us=to_us, limit=1000)
        st.calls += 1
        new = 0
        for t in rows:
            tid = str(t["tradeId"])
            if tid in seen:
                continue
            seen.add(tid)
            new += 1
            ts = int(t["timestamp"])
            writer.add("trades", {"recv_ts_us": None, "venue_ts_us": ts, "venue": "arcus", "market": base,
                                  "trade_id": tid, "price": t["price"], "size": t["size"],
                                  "taker_side": "buy" if t["side"] == "BUY" else "sell",
                                  "maker_address": t.get("makerAddress"), "taker_address": t.get("takerAddress"),
                                  "maker_order_id": t.get("makerOrderId"), "seq": t.get("sequenceNumber"),
                                  "is_liquidation": False}, ts_us=ts, venue="arcus", market=base)
        st.rows += new
        if not rows or new == 0:
            break
        oldest = min(int(t["timestamp"]) for t in rows)
        if oldest <= start_us:
            break
        to_us = oldest  # inclusive bound; overlap deduped by tradeId
    return st


async def load_candles(arcus: ArcusRest | None, lighter: LighterRest | None, writer: ParquetWriter, base: str,
                       *, display: str | None, market_id: int | None, start_us: int, resolution: str = "1h") -> LoadStats:
    st = LoadStats()
    end = now_us()
    if arcus is not None and display:
        to = end
        while to > start_us:
            rows = await arcus.candles(display, resolution, to_us=to, from_us=start_us)
            st.calls += 1
            if not rows:
                break
            for c in rows:
                writer.add("candles_oracle", {
                    "open_ts_us": int(c["openTime"]), "venue": "arcus", "market": base, "resolution": resolution,
                    "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]), "c": float(c["close"]),
                    "volume": float(c["volume"]), "notional_volume": float(c["notionalVolume"]),
                    "trade_count": int(c["tradeCount"]), "taker_buy_volume": float(c["takerBuyVolume"])},
                    ts_us=int(c["openTime"]), venue="arcus", market=base)
                st.rows += 1
            oldest = min(int(c["openTime"]) for c in rows)
            if oldest <= start_us or len(rows) < 1500:
                break
            to = oldest
    if lighter is not None and market_id is not None:
        t = start_us // 1_000_000
        e = end // 1_000_000
        step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[resolution] * 480
        while t < e:
            rows = await lighter.candles(market_id, resolution, start_s=t, end_s=min(e, t + step), count_back=500)
            st.calls += 1
            for c in rows:
                ts = int(c["t"]) * (1000 if int(c["t"]) > 10**11 else 1_000_000)
                writer.add("candles_trade", {
                    "open_ts_us": ts, "venue": "lighter_rh", "market": base, "resolution": resolution,
                    "o": float(c.get("o") or 0), "h": float(c.get("h") or 0), "l": float(c.get("l") or 0),
                    "c": float(c.get("c") or 0), "volume": float(c.get("v") or 0),
                    "notional_volume": float(c.get("V") or 0), "trade_count": None, "taker_buy_volume": None},
                    ts_us=ts, venue="lighter_rh", market=base)
                st.rows += 1
            t += step
    return st


async def load_all_funding(bases: list[str], data_dir: Path, *, arcus_url: str = "https://api.arcus.xyz",
                           lighter_url: str = "https://api.rh.lighter.xyz") -> dict[str, Any]:
    from bot.common.ratelimit import TokenBucket
    from bot.core.liveparams import LiveParams
    from bot.venues.base import Venue

    arcus = ArcusRest(arcus_url, ip_bucket=TokenBucket(600, 10))
    lighter = LighterRest(lighter_url, rest_per_min=40)
    writer = ParquetWriter(data_dir / "history", flush_s=1e9, max_rows=10**9)
    lp = LiveParams(arcus=arcus, lighter=lighter, out_dir=data_dir / "param_changes_jsonl")
    out: dict[str, Any] = {}
    try:
        await lp.refresh()
        sm = lp.symbol_map
        for b in bases:
            res: dict[str, Any] = {}
            if sm.has(b, Venue.ARCUS):
                res["arcus"] = (await load_arcus_funding(arcus, writer, b, sm.get(b, Venue.ARCUS).venue_symbol)).rows
            if sm.has(b, Venue.LIGHTER_RH):
                res["lighter_rh"] = (await load_lighter_funding(lighter, writer, b,
                                                                sm.get(b, Venue.LIGHTER_RH).venue_market_id)).rows
            out[b] = res
            log.info("funding_loaded", market=b, data=res)
        writer.flush()
    finally:
        await arcus.close()
        await lighter.close()
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys

    bases = sys.argv[1:] or ["BTC", "ETH", "SOL", "HYPE", "SPY", "QQQ", "NVDA", "TSLA"]
    print(asyncio.run(load_all_funding(bases, Path("data"))))
