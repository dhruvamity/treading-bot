"""`arbitrage history`: everything both venues publish about funding and hourly prices, as far back as they go, for
every market both list. Public endpoints only; `arbitrage backtest` reads what this writes (arbitrage/data/history/):

    <venue>_<SYM>_funding.csv   ts (payment time, unix s), rate_per_hour (a fraction, + = longs pay)
    lighter_<SYM>_px.csv        ts (hour start), open, high, low, close, volume: the Lighter perp's trades, and the
                                dollars traded in the hour
    arcus_<SYM>_px.csv          the same from Arcus's candles: the prices are its ORACLE's, the volume its perp's
    arcus_<SYM>_perp.csv        the Arcus perp's own traded price, from the Arcus bot's recorded trades
                                (arcus/data/scout/tape) where this machine has them; the backtest prefers it
    markets.json                each venue's market details at the time of the download
    profunding_<venue>_<SYM>_funding.csv   the same hourly rates as ProFunding recorded them (`--profunding`): a
                                second source to check the venues' own numbers against, never what a backtest reads

How far back each goes (checked 10 Oct 2026): Arcus's funding starts with its first payment on 24 Jun 2026, Lighter's
(Robinhood Chain) on 26 Jun 2026, and both answer nothing before that; Lighter's candles start on 30 Jun 2026.
ProFunding gives the last 30 days and no more (`days` above 30 is refused), so its files grow only by being asked
again within 30 days. Arcus's candles carry its oracle's price, but its public trades can be asked for any window
(`/v1/trades`), which is where the Arcus perp's own price by the minute comes from (`--minutes`, below). Neither venue
publishes past order books: those exist only where a recorder was running.

    lighter_<SYM>_px1m.csv      Lighter's one-minute candles                       } `arbitrage history --minutes SYM`,
    arcus_<SYM>_perp1m.csv      the Arcus perp's own trades by the minute          } read by `arbitrage basis`

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


def lighter_funding(market_id: int, pause: float = 1.6, since: int = START_S) -> list[Row]:
    rows = paged(lambda end: get(f"{LIGHTER}/api/v1/fundings", {
        "market_id": market_id, "resolution": "1h", "start_timestamp": since, "end_timestamp": end,
        "count_back": 750}, pause).get("fundings") or [], lambda r: int(r["timestamp"]), int(time.time()), since, 700)
    out = {int(r["timestamp"]) // 3600 * 3600: float(r["rate"]) / 100 * (1 if r["direction"] == "long" else -1)
           for r in rows}
    return sorted(out.items())


def lighter_px(market_id: int, pause: float = 1.6, since: int = START_S, resolution: str = "1h") -> list[Row]:
    rows = paged(lambda end: get(f"{LIGHTER}/api/v1/candles", {
        "market_id": market_id, "resolution": resolution, "start_timestamp": since, "end_timestamp": end,
        "count_back": 500}, pause).get("c") or [], lambda c: int(c["t"]) // 1000, int(time.time()), since, 450)
    out = {int(c["t"]) // 1000: (int(c["t"]) // 1000, c["o"], c["h"], c["l"], c["c"], c.get("V", 0)) for c in rows}
    return [out[k] for k in sorted(out)]


def arcus_funding(market: str, pause: float = 6.0, since: int = START_S) -> list[Row]:
    us = 1_000_000
    rows = paged(lambda to: get(f"{ARCUS}/v1/fundingRates", {
        "market": market, "from": since * us, "to": to, "limit": 1000}, pause).get("fundingRates") or [],
        lambda r: int(r["time"]), int(time.time() * us), since * us, 1000)
    out = {int(r["time"]) // us // 3600 * 3600: float(r["fundingRate"]) for r in rows}
    return sorted(out.items())


def arcus_px(market: str, pause: float = 6.0, since: int = START_S) -> list[Row]:
    us = 1_000_000
    rows = paged(lambda to: get(f"{ARCUS}/v1/candles", {
        "market": market, "timeframe": "1h", "to": to, "countback": 1500}, pause).get("candles") or [],
        lambda c: int(c["openTime"]), int(time.time() * us), since * us, 1500)
    out = {int(c["openTime"]) // us: (int(c["openTime"]) // us, c["open"], c["high"], c["low"], c["close"],
                                      c.get("notionalVolume", 0)) for c in rows}
    return [out[k] for k in sorted(out)]


def arcus_trades_1m(market: str, pause: float = 2.9, since: int = START_S, until: int | None = None,
                    say: Callable[[str], None] | None = None) -> list[Row]:
    """The Arcus perp's own traded price by the minute, from its public trades (`/v1/trades` answers any window,
    newest first, 1,000 at a time): ts (minute start), open, high, low, close, volume (dollars), trades. Its candles
    cannot give this: their prices are the oracle's. A busy market has about a hundred thousand trades a day, so this
    is one request per thousand trades, and a page of a thousand costs 70 of the 1,500 weight an IP has a minute:
    one request every 2.9 s, about a quarter of an hour for a busy market's week."""
    us = 1_000_000
    to = int((until or time.time()) * us)
    bars: dict[int, list[float]] = {}
    seen: set[str] = set()
    pages = 0
    while to > since * us:
        rows = get(f"{ARCUS}/v1/trades", {"market": market, "from": since * us, "to": to, "limit": 1000},
                   pause).get("trades") or []
        if not rows:
            break
        for r in rows:                                   # newest first
            if r["tradeId"] in seen:
                continue
            seen.add(r["tradeId"])
            t, px, sz = int(r["timestamp"]) // us // 60 * 60, float(r["price"]), float(r["size"])
            bar = bars.get(t)
            if bar is None:
                bars[t] = [t, px, px, px, px, px * sz, 1]
            else:                                        # an earlier trade of the same minute: it is the new open
                bar[1], bar[2], bar[3] = px, max(bar[2], px), min(bar[3], px)
                bar[5] += px * sz
                bar[6] += 1
        first = min(int(r["timestamp"]) for r in rows)
        if len(rows) < 1000 or first >= to:
            break
        to = first - 1
        if len(seen) > 200_000:                          # the ids are only needed across a page boundary
            seen = {r["tradeId"] for r in rows}
        pages += 1
        if say and pages % 100 == 0:
            say(f"{market}: back to {dt.datetime.fromtimestamp(first / us, dt.UTC):%d %b %H:%M}, {len(bars):,} minutes")
    return [tuple(bars[k]) for k in sorted(bars)]


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


def arcus_tape_1m(tape_root: Path, market: str) -> list[Row]:
    """The same minute bars as arcus_trades_1m, from the Arcus bot's recorded trades where this machine has them (no
    requests): ts, open, high, low, close, volume, trades. [] without a tape."""
    try:
        import numpy as np
    except ImportError:
        return []
    mdir = tape_root / market
    if not mdir.is_dir():
        return []
    out: dict[int, Row] = {}
    for d in sorted(x for x in mdir.iterdir() if x.is_dir()):
        cols: dict[str, list[Any]] = {"ts": [], "px": [], "sz": [], "tid": []}
        for f in d.glob("trades-*.npz"):
            if not f.name.endswith(".tmp.npz"):
                with np.load(f) as z:
                    for k in cols:
                        cols[k].append(z[k])
        if not cols["ts"]:
            continue
        ts, px, sz, tid = (np.concatenate(cols[k]) for k in ("ts", "px", "sz", "tid"))
        _, keep = np.unique(tid, return_index=True)             # a refill repeats trades the stream already had
        keep = keep[np.argsort(ts[keep], kind="stable")]
        ts, px, sz = ts[keep], px[keep], sz[keep]
        minute = ts // 60_000_000
        edges = np.flatnonzero(np.diff(minute)) + 1
        for i, j in zip(np.concatenate(([0], edges)), np.concatenate((edges, [len(ts)])), strict=True):
            x = px[i:j]
            k = int(minute[i]) * 60
            out[k] = (k, float(x[0]), float(x.max()), float(x.min()), float(x[-1]), float((x * sz[i:j]).sum()),
                      int(j - i))
    return [out[k] for k in sorted(out)]


def read_rows(path: Path) -> list[Row]:
    """A file `write` made, as rows again (the first column an int); [] when there is none."""
    try:
        with path.open() as fh:
            rows = list(csv.reader(fh))[1:]
    except OSError:
        return []
    return [(int(float(r[0])), *r[1:]) for r in rows if r]


def merged(old: list[Row], new: list[Row]) -> list[Row]:
    """Both, one row per time; a time both have keeps the new row (the last hour of the old file may have been open)."""
    rows = {int(r[0]): r for r in old}
    rows.update({int(r[0]): r for r in new})
    return [rows[k] for k in sorted(rows)]


OVERLAP_S = 6 * 3600        # an update asks again for the last hours it already has


def download(out: Path, *, venues: tuple[str, ...] = ("lighter", "arcus"), symbols: list[str] | None = None,
             tape_root: Path | None = None, say: Callable[[str], None] = print, update: bool = False) -> list[str]:
    """update: keep what the files hold and ask the venues only for the hours after it (minutes, not an hour); a market
    with no file yet is downloaded whole."""
    out.mkdir(parents=True, exist_ok=True)
    am = {m["baseAsset"]: m for m in get(f"{ARCUS}/v1/markets", {}, 1.0).get("markets", [])
          if m.get("type") == "PERPETUAL" and str(m.get("marketDisplayName", "")).endswith("-USD")}
    lm = {m["symbol"]: m for m in get(f"{LIGHTER}/api/v1/orderBookDetails", {}, 1.6).get("order_book_details", [])
          if m.get("market_type", "perp") == "perp"}
    common = sorted(set(am) & set(lm))
    (out / "markets.json").write_text(json.dumps({s: {"arcus": am[s], "lighter": lm[s]} for s in common}))
    todo = [s for s in (x.upper() for x in symbols)] if symbols else common
    say(f"{len(common)} markets on both venues; {'updating' if update else 'downloading'} {len(todo)}")

    def take(path: Path, header: list[str], fetch: Callable[..., list[Row]], key: Any) -> int:
        old = read_rows(path) if update else []
        if old and len(old[0]) < len(header):       # a file from before a column was added: fetched whole again
            old = []
        since = max(START_S, int(old[-1][0]) - OVERLAP_S) if old else START_S
        rows = merged(old, fetch(key, since=since))
        write(path, header, rows)
        return len(rows)

    px_head = ["ts", "open", "high", "low", "close", "volume"]
    for sym in todo:
        if sym not in common:
            continue
        line = [sym]
        if "lighter" in venues:
            mid = int(lm[sym]["market_id"])
            n = take(out / f"lighter_{sym}_funding.csv", ["ts", "rate_per_hour"], lighter_funding, mid)
            take(out / f"lighter_{sym}_px.csv", px_head, lighter_px, mid)
            line.append(f"Lighter {n} h")
        if "arcus" in venues:
            n = take(out / f"arcus_{sym}_funding.csv", ["ts", "rate_per_hour"], arcus_funding, f"{sym}-USD")
            take(out / f"arcus_{sym}_px.csv", px_head, arcus_px, f"{sym}-USD")
            line.append(f"Arcus {n} h")
        if tape_root is not None:
            perp = arcus_perp(tape_root, f"{sym}-USD")
            if perp and update:          # this machine's tape may hold fewer days than the file already does
                perp = merged(read_rows(out / f"arcus_{sym}_perp.csv"), perp)
            if perp:
                write(out / f"arcus_{sym}_perp.csv", ["ts", "open", "high", "low", "close", "trades"], perp)
                line.append(f"Arcus perp from the tape {len(perp)} h")
        say(" · ".join(line))
    return todo


def minutes(out: Path, symbols: list[str], *, tape_root: Path | None = None,
            say: Callable[[str], None] = print) -> None:
    """Both venues' prices by the minute, added to what the files hold: lighter_<SYM>_px1m.csv from Lighter's
    candles, arcus_<SYM>_perp1m.csv from the Arcus perp's own trades (this machine's tape first, which costs no
    requests, then Arcus's public trades for the time after it). The first time is slow: Lighter gives 500 minutes a
    request, and a busy week on Arcus is some hundreds of requests at one every 2.9 s."""
    out.mkdir(parents=True, exist_ok=True)
    try:
        mk = json.loads((out / "markets.json").read_text())
    except (OSError, ValueError):
        say("no markets.json yet: run `arbitrage history` first")
        return
    head = ["ts", "open", "high", "low", "close", "volume"]
    for sym in symbols:
        if sym not in mk:
            say(f"{sym}: not a market both venues list")
            continue
        path = out / f"lighter_{sym}_px1m.csv"
        old = read_rows(path)
        since = max(START_S, int(old[-1][0]) - 3600) if old else START_S
        rows = merged(old, lighter_px(int(mk[sym]["lighter"]["market_id"]), since=since, resolution="1m"))
        write(path, head, rows)
        line = [sym, f"Lighter {len(rows):,} minutes"]
        path = out / f"arcus_{sym}_perp1m.csv"
        old = read_rows(path)
        if tape_root is not None:
            old = merged(old, arcus_tape_1m(tape_root, f"{sym}-USD"))
        since = max(START_S, int(old[-1][0]) - 3600) if old else START_S
        rows = merged(old, arcus_trades_1m(f"{sym}-USD", since=since, say=say))
        write(path, [*head, "trades"], rows)
        line.append(f"Arcus {len(rows):,} minutes with a trade")
        say(" · ".join(line))


# ------------------------------------------------------------------------------------------------ ProFunding
PROFUNDING = "https://profunding.pro/api/api"
PF_VENUES = {"arcus": "arcus", "lighter": "lighterrh"}       # ours -> ProFunding's exchange names
PF_DAYS = 30                                                 # the most it gives


def _pf(path: str, params: dict[str, Any], key: str) -> Any:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{PROFUNDING}{path}?{q}" if q else f"{PROFUNDING}{path}",
                                 headers={**HEADERS, "X-API-Key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def pf_rows(body: dict[str, Any]) -> list[Row]:
    """ProFunding's /rates/historical answer as (payment time, rate per hour) rows."""
    out = {}
    for r in body.get("rates") or []:
        t = dt.datetime.fromisoformat(str(r["timestamp"]))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.UTC)
        out[int(t.timestamp()) // 3600 * 3600] = float(r["rate"])
    return sorted(out.items())


def profunding(out: Path, key: str, *, symbols: list[str] | None = None, say: Callable[[str], None] = print,
               pause: float = 1.0) -> int:
    """The last 30 days of both venues' hourly rates as ProFunding recorded them, added to what the files already
    hold. One request per market and venue (the free key allows 100 a day); only the data key is sent, never an
    exchange key. Returns the number of files written."""
    if not key:
        say("PROFUNDING_API_KEY is not set in .env: nothing asked")
        return 0
    try:
        todo = symbols or sorted(json.loads((out / "markets.json").read_text()))
    except (OSError, ValueError):
        say("no markets.json yet: run `arbitrage history` first")
        return 0
    try:
        v = _pf("/mcp/validate", {}, key)
        left = int(v.get("daily_limit", 0)) - int(v.get("requests_today", 0))
        say(f"ProFunding: {v.get('requests_today')} of {v.get('daily_limit')} requests used today")
        if left < 2 * len(todo):
            say(f"{2 * len(todo)} requests needed, {left} left today: name fewer markets with --symbols, or wait")
            return 0
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        say(f"ProFunding did not answer: {e}")
        return 0
    n = 0
    for sym in (x.upper() for x in todo):
        line = [sym]
        for ours, theirs in PF_VENUES.items():
            try:
                rows = pf_rows(_pf("/rates/historical", {"symbol": f"{sym}/USDC", "exchange": theirs,
                                                         "days": PF_DAYS}, key))
            except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError) as e:
                line.append(f"{ours}: {str(e)[:60]}")
                continue
            time.sleep(pause)
            if rows:
                path = out / f"profunding_{ours}_{sym}_funding.csv"
                rows = merged(read_rows(path), rows)
                write(path, ["ts", "rate_per_hour"], rows)
                n += 1
            line.append(f"{ours} {len(rows)} h")
        say(" · ".join(line))
    return n
