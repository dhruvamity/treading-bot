"""`arbitrage history`: everything both venues publish about funding and hourly prices, as far back as they go, for
every market both list. Public endpoints only; `arbitrage backtest` reads what this writes (arbitrage/data/history/):

    <venue>_<SYM>_funding.csv   ts (payment time, unix s), rate_per_hour (a fraction, + = longs pay)
    lighter_<SYM>_px.csv        ts (hour start), open, high, low, close: the Lighter perp's trades
    arcus_<SYM>_px.csv          the same from Arcus's candles, which are its ORACLE price
    arcus_<SYM>_perp.csv        the Arcus perp's own traded price, from the Arcus bot's recorded trades
                                (arcus/data/scout/tape) where this machine has them; the backtest prefers it
    markets.json                each venue's market details at the time of the download

Paced for the venues' limits (Arcus 1,500 weight a minute per IP, Lighter 60 requests a minute): a full download of
38 markets takes about an hour, a refresh as long (the files are rewritten whole).
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ARCUS = "https://api.arcus.xyz"
LIGHTER = "https://api.rh.lighter.xyz"
START_S = int(dt.datetime(2026, 6, 1, tzinfo=dt.UTC).timestamp())      # before either venue listed anything
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
Row = tuple[Any, ...]


def get(url: str, params: dict[str, Any], pause: float) -> dict[str, Any]:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    err = ""
    for attempt in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{url}?{q}" if q else url, headers=HEADERS),
                                        timeout=40) as r:
                body = json.loads(r.read())
            time.sleep(pause)
            return dict(body)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            err = str(e)
        time.sleep(6 * (attempt + 1))
    raise RuntimeError(f"{url} {params}: {err}")


def write(path: Path, header: list[str], rows: list[Row]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def paged(fetch: Callable[[int], list[dict[str, Any]]], key: Callable[[dict[str, Any]], int], end: int, stop_at: int,
          full: int) -> list[dict[str, Any]]:
    """Walk back from `end`: fetch(end) returns up to `full` rows at or before it; a short page is the last."""
    out: list[dict[str, Any]] = []
    while end > stop_at:
        rows = fetch(end)
        if not rows:
            break
        out += rows
        first = min(key(r) for r in rows)
        if len(rows) < full or first >= end:
            break
        end = first - 1
    return out


def lighter_funding(market_id: int, pause: float = 1.6) -> list[Row]:
    rows = paged(lambda end: get(f"{LIGHTER}/api/v1/fundings", {
        "market_id": market_id, "resolution": "1h", "start_timestamp": START_S, "end_timestamp": end,
        "count_back": 750}, pause).get("fundings") or [], lambda r: int(r["timestamp"]), int(time.time()), START_S, 700)
    out = {int(r["timestamp"]) // 3600 * 3600: float(r["rate"]) / 100 * (1 if r["direction"] == "long" else -1)
           for r in rows}
    return sorted(out.items())


def lighter_px(market_id: int, pause: float = 1.6) -> list[Row]:
    rows = paged(lambda end: get(f"{LIGHTER}/api/v1/candles", {
        "market_id": market_id, "resolution": "1h", "start_timestamp": START_S, "end_timestamp": end,
        "count_back": 500}, pause).get("c") or [], lambda c: int(c["t"]) // 1000, int(time.time()), START_S, 450)
    out = {int(c["t"]) // 1000: (int(c["t"]) // 1000, c["o"], c["h"], c["l"], c["c"]) for c in rows}
    return [out[k] for k in sorted(out)]


def arcus_funding(market: str, pause: float = 6.0) -> list[Row]:
    us = 1_000_000
    rows = paged(lambda to: get(f"{ARCUS}/v1/fundingRates", {
        "market": market, "from": START_S * us, "to": to, "limit": 1000}, pause).get("fundingRates") or [],
        lambda r: int(r["time"]), int(time.time() * us), START_S * us, 1000)
    out = {int(r["time"]) // us // 3600 * 3600: float(r["fundingRate"]) for r in rows}
    return sorted(out.items())


def arcus_px(market: str, pause: float = 6.0) -> list[Row]:
    us = 1_000_000
    rows = paged(lambda to: get(f"{ARCUS}/v1/candles", {
        "market": market, "timeframe": "1h", "to": to, "countback": 1500}, pause).get("candles") or [],
        lambda c: int(c["openTime"]), int(time.time() * us), START_S * us, 1500)
    out = {int(c["openTime"]) // us: (int(c["openTime"]) // us, c["open"], c["high"], c["low"], c["close"])
           for c in rows}
    return [out[k] for k in sorted(out)]


def arcus_perp(tape_root: Path, market: str) -> list[Row]:
    """Hourly open, high, low, close of the Arcus perp from recorded trades; [] when this machine has no tape."""
    try:
        import numpy as np
    except ImportError:
        return []
    mdir = tape_root / market
    if not mdir.is_dir():
        return []
    hour_us = 3_600_000_000
    rows: dict[int, list[Any]] = {}
    for d in sorted(p for p in mdir.iterdir() if p.is_dir()):
        ts, px = [], []
        for f in d.glob("trades-*.npz"):
            if not f.name.endswith(".tmp.npz"):
                with np.load(f) as z:
                    ts.append(z["ts"])
                    px.append(z["px"])
        if not ts:
            continue
        t, p = np.concatenate(ts), np.concatenate(px)
        o = np.argsort(t, kind="stable")
        t, p = t[o], p[o]
        hour = t // hour_us
        for h in np.unique(hour):
            x = p[hour == h]
            k = int(h) * 3600
            r = rows.get(k)
            rows[k] = [k, float(x[0]), float(x.max()), float(x.min()), float(x[-1]), len(x)] if r is None else \
                [k, r[1], max(r[2], float(x.max())), min(r[3], float(x.min())), float(x[-1]), r[5] + len(x)]
    return [tuple(rows[k]) for k in sorted(rows)]


def download(out: Path, *, venues: tuple[str, ...] = ("lighter", "arcus"), symbols: list[str] | None = None,
             tape_root: Path | None = None, say: Callable[[str], None] = print) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    am = {m["baseAsset"]: m for m in get(f"{ARCUS}/v1/markets", {}, 1.0).get("markets", [])
          if m.get("type") == "PERPETUAL" and str(m.get("marketDisplayName", "")).endswith("-USD")}
    lm = {m["symbol"]: m for m in get(f"{LIGHTER}/api/v1/orderBookDetails", {}, 1.6).get("order_book_details", [])
          if m.get("market_type", "perp") == "perp"}
    common = sorted(set(am) & set(lm))
    (out / "markets.json").write_text(json.dumps({s: {"arcus": am[s], "lighter": lm[s]} for s in common}))
    todo = [s for s in (x.upper() for x in symbols)] if symbols else common
    say(f"{len(common)} markets on both venues; downloading {len(todo)}")
    for sym in todo:
        if sym not in common:
            continue
        line = [sym]
        if "lighter" in venues:
            mid = int(lm[sym]["market_id"])
            f, p = lighter_funding(mid), lighter_px(mid)
            write(out / f"lighter_{sym}_funding.csv", ["ts", "rate_per_hour"], f)
            write(out / f"lighter_{sym}_px.csv", ["ts", "open", "high", "low", "close"], p)
            line.append(f"Lighter {len(f)} h")
        if "arcus" in venues:
            f, p = arcus_funding(f"{sym}-USD"), arcus_px(f"{sym}-USD")
            write(out / f"arcus_{sym}_funding.csv", ["ts", "rate_per_hour"], f)
            write(out / f"arcus_{sym}_px.csv", ["ts", "open", "high", "low", "close"], p)
            line.append(f"Arcus {len(f)} h")
        if tape_root is not None:
            perp = arcus_perp(tape_root, f"{sym}-USD")
            if perp:
                write(out / f"arcus_{sym}_perp.csv", ["ts", "open", "high", "low", "close", "trades"], perp)
                line.append(f"Arcus perp from the tape {len(perp)} h")
        say(" · ".join(line))
    return todo
