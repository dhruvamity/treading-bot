"""`deltaneutral fetch`: what the backtest and the research read, from both venues' public endpoints (no key), into
deltaneutral/data/:

    lighter_<SYM>_5m.csv        ts (bar start, unix s), open, high, low, close, volume: Lighter's trades, 5 minutes
    lighter_<SYM>_mark5m.csv    ts, open, high, low, close, samples: Lighter's mark price (what it liquidates on)
    arcus_<SYM>_oracle5m.csv    ts, open, high, low, close, volume: Arcus's candles. Their price is the oracle's while
                                the stock market is open and Arcus's own mark while it is closed (docs: prices)
    arcus_<SYM>_perp1m.csv      ts, open, high, low, close, dollars, trades: the Arcus perp's own trades by the minute
    lighter_<SYM>_mark1h.csv,   the same marks and candles by the hour since both venues listed the markets (late June
    arcus_<SYM>_oracle1h.csv    2026), for the longer look at liquidations (`fetch_hours`)
    <venue>_<SYM>_funding.csv   ts (payment hour), rate_per_hour (a fraction, + = longs pay)
    markets.json                both venues' market details at the time of the download (leverage, margin, OI)

A second run asks only for what came after each file's last row. Paced for the venues' limits (Lighter 60 requests a
minute, Arcus 1,500 weight a minute; a page of Arcus trades weighs 70): Lighter and Arcus are fetched side by side.
Arcus's trades are most of the time, about one request per thousand trades (a month of the eight markets: about 1.5 h).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from arbitrage import history as H

from . import DATA, TOKENS

ARCUS, LIGHTER = H.ARCUS, H.LIGHTER
DAY = 86_400
FIVE = 300
Row = tuple


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def path(name: str, root: Path | None = None) -> Path:
    return (root or DATA) / f"{name}.csv"


def last_ts(name: str, root: Path | None = None) -> int | None:
    rows = H.read_rows(path(name, root))
    return int(rows[-1][0]) if rows else None


def store(name: str, header: list[str], rows: list[Row], root: Path | None = None) -> int:
    """Merge `rows` into the file (a time both have keeps the new row: the old file's last bar may have been open)."""
    p = path(name, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = H.merged(H.read_rows(p), rows)
    H.write(p, header, merged)
    return len(merged)


def since_for(name: str, first: int, root: Path | None = None) -> int:
    """Where a download starts: the file's last row (asked again: it may have been an open bar), else `first`."""
    t = last_ts(name, root)
    return first if t is None else max(first, t - 3600)


# ------------------------------------------------------------------------------------------------ the requests
def lighter_walk(endpoint: str, market_id: int, since: int, until: int, resolution: str = "5m",
                 pause: float = 1.6) -> list[Row]:
    """Lighter's candles (`candles`: its trades; `markPriceCandles`: its mark) in [since, until), walking back 500
    bars a request. Each request names a window of at least one bar: Lighter refuses a shorter one (HTTP 400)."""
    bar = {"1m": 60, "5m": 300, "1h": 3600}[resolution]
    step = bar * 500
    rows: list[Row] = []
    end, empty = until, 0
    while end - since >= bar and empty < 5:
        c = H.get(f"{LIGHTER}/api/v1/{endpoint}", {
            "market_id": market_id, "resolution": resolution, "start_timestamp": max(since, end - step),
            "end_timestamp": end, "count_back": 500}, pause).get("c") or []
        last = "sc" if endpoint == "markPriceCandles" else "V"
        got = [(int(x["t"]) // 1000, x["o"], x["h"], x["l"], x["c"], x.get(last, 0)) for x in c]
        rows += got
        empty = 0 if got else empty + 1
        end -= step
    out = {r[0]: r for r in rows if since <= r[0] < until}
    return [out[k] for k in sorted(out)]


def lighter_mark(market_id: int, since: int, until: int, resolution: str = "5m", pause: float = 1.6) -> list[Row]:
    return lighter_walk("markPriceCandles", market_id, since, until, resolution, pause)


def arcus_candles(market: str, since: int, timeframe: str = "5m", pause: float = 6.0) -> list[Row]:
    us = 1_000_000
    rows = H.paged(lambda to: H.get(f"{ARCUS}/v1/candles", {
        "market": market, "timeframe": timeframe, "to": to, "countback": 1500}, pause).get("candles") or [],
        lambda c: int(c["openTime"]), int(time.time() * us), since * us, 1500)
    out = {int(c["openTime"]) // us: (int(c["openTime"]) // us, c["open"], c["high"], c["low"], c["close"],
                                      c.get("notionalVolume", 0)) for c in rows}
    return [out[k] for k in sorted(out) if k >= since]


def markets() -> tuple[dict, dict]:
    """({symbol: Arcus market}, {symbol: Lighter market}) for the perpetuals both list."""
    am = {m["baseAsset"]: m for m in H.get(f"{ARCUS}/v1/markets", {}, 1.0).get("markets", [])
          if m.get("type") == "PERPETUAL" and str(m.get("marketDisplayName", "")).endswith("-USD")}
    lm = {m["symbol"]: m for m in H.get(f"{LIGHTER}/api/v1/orderBookDetails", {}, 1.6).get("order_book_details", [])
          if m.get("market_type", "perp") == "perp"}
    return am, lm


def save_markets(symbols: list[str], root: Path | None = None) -> tuple[dict, dict]:
    """Both venues' details of `symbols` into data/markets.json, merged with the markets already there."""
    root = root or DATA
    am, lm = markets()
    p = root / "markets.json"
    try:
        cur = json.loads(p.read_text())
    except (OSError, ValueError):
        cur = {}
    for venue, got in (("arcus", am), ("lighter", lm)):
        cur.setdefault(venue, {}).update({s: got[s] for s in symbols if s in got})
    cur["at"] = int(time.time())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=1))
    return am, lm


# ------------------------------------------------------------------------------------------------ the download
PX = ["ts", "open", "high", "low", "close", "volume"]


def fetch(symbols: list[str] | None = None, days: float = 35, root: Path | None = None,
          parts: tuple[str, ...] = ("lighter", "arcus", "trades"), say: Callable[[str], None] = log) -> None:
    """Everything above for `symbols` (default: the owner's eight), the last `days` days or since each file's end.
    `parts`: "lighter" (its candles, mark and funding), "arcus" (candles and funding), "trades" (Arcus's trades)."""
    root = root or DATA
    root.mkdir(parents=True, exist_ok=True)
    syms = list(symbols or TOKENS)
    am, lm = save_markets(syms, root)
    missing = [s for s in syms if s not in am or s not in lm]
    if missing:
        say(f"not on both venues, left out: {', '.join(missing)}")
    syms = [s for s in syms if s in am and s in lm]
    first = int(time.time()) - int(days * DAY)
    now5 = int(time.time()) // FIVE * FIVE
    errors: list[str] = []

    def guard(fn: Callable[[], None], what: str) -> Callable[[], None]:
        def run() -> None:
            try:
                fn()
            except Exception as e:              # one venue failing must not lose the other's download
                errors.append(f"{what}: {type(e).__name__} {e}")
                say(f"{what} FAILED: {e}")
        return run

    def lighter_all() -> None:
        for s in syms:
            mid = int(lm[s]["market_id"])
            n = store(f"lighter_{s}_5m", PX, lighter_walk("candles", mid, since_for(f"lighter_{s}_5m", first, root),
                                                          now5), root)
            m = store(f"lighter_{s}_mark5m", ["ts", "open", "high", "low", "close", "samples"],
                      lighter_mark(mid, since_for(f"lighter_{s}_mark5m", first, root), now5), root)
            f = store(f"lighter_{s}_funding", ["ts", "rate_per_hour"],
                      H.lighter_funding(mid, since=since_for(f"lighter_{s}_funding", first, root)), root)
            say(f"lighter {s}: {n:,} trade bars, {m:,} mark bars, {f:,} funding hours")

    def arcus_bars() -> None:
        for s in syms:
            mk = f"{s}-USD"
            n = store(f"arcus_{s}_oracle5m", PX, [r for r in arcus_candles(mk, since_for(f"arcus_{s}_oracle5m", first,
                                                                                         root)) if r[0] < now5], root)
            f = store(f"arcus_{s}_funding", ["ts", "rate_per_hour"],
                      H.arcus_funding(mk, since=since_for(f"arcus_{s}_funding", first, root)), root)
            say(f"arcus {s}: {n:,} candles, {f:,} funding hours")

    def arcus_trades() -> None:
        for s in syms:
            name = f"arcus_{s}_perp1m"
            rows = H.arcus_trades_1m(f"{s}-USD", since=since_for(name, first, root), say=say)
            n = store(name, ["ts", "open", "high", "low", "close", "dollars", "trades"],
                      [r for r in rows if r[0] < now5], root)
            say(f"arcus {s}: {n:,} minutes with trades")

    jobs = [guard(fn, name) for name, fn in (("lighter", lighter_all), ("arcus", arcus_bars), ("trades", arcus_trades))
            if name in parts]
    threads = [threading.Thread(target=j) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise RuntimeError("; ".join(errors))


LISTED = 1_782_777_600          # 30 Jun 2026 00:00 UTC: both venues trade these markets from about then


def fetch_hours(symbols: list[str] | None = None, root: Path | None = None, say: Callable[[str], None] = log) -> None:
    """Hourly mark (Lighter) and candles (Arcus) since the markets were listed: about 2,600 hours a market."""
    root = root or DATA
    syms = list(symbols or TOKENS)
    _, lm = save_markets(syms, root)
    now_h = int(time.time()) // 3600 * 3600
    for s in syms:
        if s not in lm:
            continue
        n = store(f"lighter_{s}_mark1h", ["ts", "open", "high", "low", "close", "samples"],
                  lighter_walk("markPriceCandles", int(lm[s]["market_id"]), since_for(f"lighter_{s}_mark1h", LISTED,
                                                                                         root), now_h, "1h"), root)
        m = store(f"arcus_{s}_oracle1h", PX, [r for r in arcus_candles(f"{s}-USD", since_for(f"arcus_{s}_oracle1h",
                                                                                            LISTED, root), "1h")
                                              if r[0] < now_h], root)
        say(f"{s}: {n:,} Lighter hours, {m:,} Arcus hours")

