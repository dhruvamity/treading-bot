"""Download one-minute history into data/bars/ (public data only; no venue key, no order).

    alpaca_SPY, alpaca_QQQ    the ETFs as traded in New York (Alpaca SIP, 04:00-20:00 ET), from 2020
    duka_XAUUSD               spot gold around the clock (Dukascopy bid), from 2020
    lighter_SPY/QQQ/XAU       Lighter's mark price, since its markets opened (26 Jun 2026)
    arcus_SPY/QQQ/GLD         Arcus's oracle price, since its markets opened (24 Jun 2026)

Alpaca needs a free data key (APCA_API_KEY_ID, APCA_API_SECRET_KEY) in the environment, in arcus/.env or in
antigravity/.env. Every source is paced well under its limit, and a second run only adds what is new.
"""

from __future__ import annotations

import datetime as dt
import json
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import ROOT
from . import bars as B

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
START = int(dt.datetime(2020, 1, 1, tzinfo=dt.UTC).timestamp())
VENUES_OPEN = int(dt.datetime(2026, 6, 24, tzinfo=dt.UTC).timestamp())
LIGHTER = "https://api.rh.lighter.xyz/api/v1"
LIGHTER_IDS = {"SPY": 26, "QQQ": 25, "XAU": 40}
ARCUS = "https://api.arcus.xyz/v1"
DUKA = "https://datafeed.dukascopy.com/datafeed"
DUKA_SCALE = {"XAUUSD": 1000.0}
QUIET = False                          # the paper bot updates every minute: no line per update


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def http(url: str, params: dict | None = None, headers: dict | None = None, pause: float = 0.0,
         raw: bool = False, timeout: float = 60, tries: int = 8) -> object:
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    err = ""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url + q, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            time.sleep(pause)
            return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b"" if raw else {}
            err = f"HTTP {e.code}"
            time.sleep(min(60, (30 if e.code == 429 else 5) * (attempt + 1)))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            err = str(e)
            time.sleep(min(60, 5 * (attempt + 1)))
    raise RuntimeError(f"{url}{q}: {err}")


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for f in (ROOT.parent / "arcus" / ".env", ROOT.parent / "antigravity" / ".env"):
        if f.exists():
            for line in f.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == name and v.strip():
                    return v.strip()
    return ""


def _since(name: str, default: int) -> tuple[B.Bars, int]:
    have = B.load(name)
    return have, (int(have.t[-1]) + 60 if len(have) else default)


def _store(name: str, have: B.Bars, rows: list[tuple]) -> None:
    b = B.concat(have, B.from_rows(rows))
    B.save(name, b)
    if len(b) and not QUIET:
        log(f"{name}: {len(b):,} bars {B.day_str(int(b.t[0]) // 86400)} .. "
            f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(int(b.t[-1])))} UTC (+{len(rows):,})")


# ---- Alpaca: SPY, QQQ as traded in New York ---------------------------------------------------------------------

def alpaca(sym: str) -> None:
    name = f"alpaca_{sym}"
    have, since = _since(name, START)
    hdr = {"APCA-API-KEY-ID": env_value("APCA_API_KEY_ID"), "APCA-API-SECRET-KEY": env_value("APCA_API_SECRET_KEY")}
    if not hdr["APCA-API-KEY-ID"]:
        raise SystemExit("no Alpaca key: set APCA_API_KEY_ID and APCA_API_SECRET_KEY (free data plan)")
    end = int(time.time()) - 16 * 60                 # the free plan serves SIP data from 15 minutes ago
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))   # noqa: E731
    params = {"symbols": sym, "timeframe": "1Min", "start": iso(since), "end": iso(end), "feed": "sip",
              "adjustment": "raw", "limit": 10000, "sort": "asc"}
    rows, token = [], None
    while True:
        d = http("https://data.alpaca.markets/v2/stocks/bars", dict(params, **({"page_token": token} if token else {})),
                 hdr, pause=0.35)
        for x in (d.get("bars") or {}).get(sym, []):
            t = int(dt.datetime.fromisoformat(x["t"].replace("Z", "+00:00")).timestamp())
            rows.append((t, x["o"], x["h"], x["l"], x["c"], x["v"]))
        token = d.get("next_page_token")
        if not token:
            break
    _store(name, have, rows)


# ---- Dukascopy: spot gold, 24 hours ----------------------------------------------------------------------------

def dukascopy(sym: str = "XAUUSD", side: str = "BID", start: int = START) -> None:
    """One file a day (bi5 = LZMA of 24-byte rows: second of the day, open, close, low, high as integers, volume).
    Minutes with no ticks (the daily break, weekends) are left out. Dukascopy throttles hard (about one request in
    three answered in Oct 2026), so this is slow: it is the check on the gold history, not the history itself."""
    name = f"duka_{sym}" if side == "BID" else f"duka_{sym}_{side.lower()}"
    have, since = _since(name, start)
    scale = DUKA_SCALE[sym]
    day = since // 86400
    today = int(time.time()) // 86400
    rows: list[tuple] = []
    while day < today:                                # today's file is incomplete until tomorrow
        date = dt.date(1970, 1, 1) + dt.timedelta(days=day)
        if date.weekday() != 5:                       # nothing trades on Saturday
            url = f"{DUKA}/{sym}/{date.year}/{date.month - 1:02d}/{date.day:02d}/{side}_candles_min_1.bi5"
            body = http(url, raw=True, pause=1.5, headers={"Accept": "*/*"}, timeout=20, tries=20)
            if body:
                data = lzma.decompress(body)
                for k in range(len(data) // 24):
                    s, o, c, lo, hi, v = struct.unpack(">iiiiif", data[k * 24:(k + 1) * 24])
                    if v > 0:
                        rows.append((day * 86400 + s, o / scale, hi / scale, lo / scale, c / scale, v))
        day += 1
        if date.day == 1 and rows:
            log(f"{name}: {date:%Y-%m} ...")
        if len(rows) > 200_000:                       # save as it goes: a stopped run keeps what it has
            have = B.concat(have, B.from_rows(rows))
            B.save(name, have)
            rows = []
    _store(name, have, rows)


# ---- Binance bulk files: tokenized gold (PAXG) and Binance's gold perpetual -------------------------------------

BINANCE_BULK = "https://data.binance.vision/data"


def binance(sym: str, kind: str = "spot", start: str = "2020-01") -> None:
    """Monthly zip files of one-minute klines (and daily ones for the current month), from data.binance.vision.
    PAXGUSDT (spot, one token = one troy ounce of gold, from 2020) and XAUUSDT (Binance's gold perpetual)."""
    import csv
    import io
    import zipfile

    name = f"binance_{sym}" if kind == "spot" else f"binance_{sym}_perp"
    have, since = _since(name, int(dt.datetime.strptime(start, "%Y-%m").replace(tzinfo=dt.UTC).timestamp()))
    base = f"{BINANCE_BULK}/spot" if kind == "spot" else f"{BINANCE_BULK}/futures/um"
    rows: list[tuple] = []

    def take(url: str) -> bool:
        body = http(url, raw=True, pause=0.2, tries=4)
        if not body:
            return False
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for line in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                if not line or not line[0].isdigit():
                    continue                      # futures files carry a header row
                ts = int(line[0])
                ts = ts // 1_000_000 if ts > 10**14 else ts // 1000      # microseconds since 2025 in spot files
                if ts >= since:
                    rows.append((ts, float(line[1]), float(line[2]), float(line[3]), float(line[4]), float(line[5])))
        return True

    d = dt.datetime.fromtimestamp(since, dt.UTC).date().replace(day=1)
    today = dt.datetime.now(dt.UTC).date()
    while d < today.replace(day=1):
        take(f"{base}/monthly/klines/{sym}/1m/{sym}-1m-{d:%Y-%m}.zip")
        d = (d + dt.timedelta(days=32)).replace(day=1)
    day = max(today.replace(day=1), dt.datetime.fromtimestamp(since, dt.UTC).date())
    while day < today:
        take(f"{base}/daily/klines/{sym}/1m/{sym}-1m-{day:%Y-%m-%d}.zip")
        day += dt.timedelta(days=1)
    _store(name, have, rows)


# ---- the venues' own prices ------------------------------------------------------------------------------------

def _lighter_walk(sym: str, lo: int, hi: int) -> list[tuple]:
    """Every one-minute candle in [lo, hi). Lighter answers at most the 500 minutes before `end_timestamp`, and fewer
    where the market paused (gold at weekends), so this steps back 500 minutes a request whatever came back, and
    stops after ten empty steps in a row (about three and a half days: before the market existed)."""
    rows, end, empty = [], hi, 0
    while end > lo and empty < 10:
        c = http(f"{LIGHTER}/markPriceCandles", {"market_id": LIGHTER_IDS[sym], "resolution": "1m",
                                                 "start_timestamp": max(lo, end - 500 * 60), "end_timestamp": end,
                                                 "count_back": 500}, pause=1.7).get("c") or []
        got = [(int(x["t"]) // 1000, x["o"], x["h"], x["l"], x["c"], x.get("sc", 0)) for x in c]
        rows += got
        empty = 0 if any(r[5] for r in got) else empty + 1
        end -= 500 * 60
    return rows


def lighter(sym: str, backfill: bool = False) -> None:
    """Lighter's mark price (what it liquidates on): what is new since the file's last bar, and with `backfill` also
    anything missing before its first bar back to the venues' opening (60 requests a minute are allowed)."""
    name = f"lighter_{sym}"
    have, since = _since(name, VENUES_OPEN)
    now_min = int(time.time()) // 60 * 60
    rows = _lighter_walk(sym, since, now_min)
    if backfill and len(have) and int(have.t[0]) > VENUES_OPEN + B.DAY:
        rows += _lighter_walk(sym, VENUES_OPEN, int(have.t[0]))
    _store(name, have, [r for r in rows if r[0] < now_min and r[5]])


def arcus(sym: str) -> None:
    """Arcus's candles (its oracle price), walking back from now, 1,500 a request (one every 6 s: the weight limit)."""
    name = f"arcus_{sym}"
    have, since = _since(name, VENUES_OPEN)
    us = 1_000_000
    to, rows = int(time.time() * us), []
    while to > since * us:
        c = http(f"{ARCUS}/candles", {"market": f"{sym}-USD", "timeframe": "1m", "to": to, "countback": 1500},
                 pause=6.0).get("candles") or []
        if not c:
            break
        rows += [(int(x["openTime"]) // us, x["open"], x["high"], x["low"], x["close"], x.get("volume", 0)) for x in c]
        first = min(int(x["openTime"]) for x in c)
        if len(c) < 1500 or first >= to:
            break
        to = first - 1
    now_min = int(time.time()) // 60 * 60
    _store(name, have, [r for r in rows if since <= r[0] < now_min])


# ---- longer history for the swing strategies (swing.py) ----------------------------------------------------------

def duka_hours(sym: str = "XAUUSD", first_year: int = 2024, workers: int = 4) -> None:
    """Dukascopy's hourly candles, one file a month (24-byte rows: second of the month, open, close, low, high,
    volume), from `first_year` (2024: the backtests use at most the last two years, the owner's rule of 15 Oct 2026;
    the file may also hold older years), `workers` at a time. Dukascopy's speed
    varies from 1 to 30 s a request (Oct 2026), so each is tried up to 30 times; months already held are not asked
    for again, and the file is saved after every twelve months fetched."""
    from concurrent.futures import ThreadPoolExecutor

    name = f"duka_{sym}_1h"
    have = B.load(name)
    scale = DUKA_SCALE[sym]
    held = {time.strftime("%Y-%m", time.gmtime(int(x))) for x in have.t[::24]} if len(have) else set()
    d = dt.date(first_year, 1, 1)
    this_month = dt.datetime.now(dt.UTC).date().replace(day=1)
    months = []
    while d < this_month:                           # this month's file is incomplete until it ends
        if f"{d:%Y-%m}" not in held:
            months.append(d)
        d = (d + dt.timedelta(days=32)).replace(day=1)

    def one(m: dt.date) -> list[tuple]:
        url = f"{DUKA}/{sym}/{m.year}/{m.month - 1:02d}/BID_candles_hour_1.bi5"
        body = http(url, raw=True, pause=1.0, headers={"Accept": "*/*", "User-Agent": BROWSER}, timeout=60, tries=30)
        start = int(dt.datetime(m.year, m.month, 1, tzinfo=dt.UTC).timestamp())
        out = []
        if body:
            data = lzma.decompress(body)
            for k in range(len(data) // 24):
                s, o, c, lo, hi, v = struct.unpack(">iiiiif", data[k * 24:(k + 1) * 24])
                if v > 0:
                    out.append((start + s, o / scale, hi / scale, lo / scale, c / scale, v))
        return out

    rows: list[tuple] = []
    with ThreadPoolExecutor(workers) as ex:
        for k, (m, got) in enumerate(zip(months, ex.map(one, months), strict=True)):
            rows += got
            if (k + 1) % 12 == 0:                   # a stopped run keeps what it has
                have = B.concat(have, B.from_rows(rows))
                B.save(name, have)
                rows = []
                log(f"{name}: to {m:%Y-%m} ({len(have):,} hours)")
    _store(name, have, rows)


BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 "
           "Safari/537.36")
DAILY = B.DATA / "daily"
FUNDING = B.DATA / "funding"


def yahoo_daily(sym: str) -> None:
    """The whole daily history from Yahoo's chart API: prices as they traded (splits taken out, dividends NOT, which
    is what a perpetual follows), with each dividend on its ex-date. Writes data/daily/yahoo_<sym>.csv."""
    import csv

    d = http(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
             {"period1": 0, "period2": int(time.time()), "interval": "1d", "events": "div"},
             pause=1.0)["chart"]["result"][0]                  # a plain user agent: Yahoo refuses a browser's
    q = d["indicators"]["quote"][0]
    tz = d["meta"].get("exchangeTimezoneName", "America/New_York")
    from zoneinfo import ZoneInfo
    day = [dt.datetime.fromtimestamp(t, ZoneInfo(tz)).date().isoformat() for t in d["timestamp"]]
    div = {dt.datetime.fromtimestamp(int(x["date"]), ZoneInfo(tz)).date().isoformat(): x["amount"]
           for x in (d.get("events", {}).get("dividends") or {}).values()}
    DAILY.mkdir(parents=True, exist_ok=True)
    path = DAILY / f"yahoo_{sym.replace('=', '')}.csv"
    n = 0
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "dividend"])
        for k, ds in enumerate(day):
            o, h, lo, c = q["open"][k], q["high"][k], q["low"][k], q["close"][k]
            if None in (o, h, lo, c) or not o:
                continue
            w.writerow([ds, f"{o:.4f}", f"{h:.4f}", f"{lo:.4f}", f"{c:.4f}", f"{div.get(ds, 0.0):.4f}"])
            n += 1
    log(f"{path.name}: {n:,} days {day[0]} .. {day[-1]}")


def funding(venue: str, sym: str) -> None:
    """Hourly funding as far back as the venue answers: data/funding/<venue>_<sym>.csv with ts (unix s, the hour) and
    rate_per_hour (a fraction of the position's value; + = longs pay shorts). Binance pays every 8 hours (4 for some
    markets): its rate is spread evenly over the hours it covers."""
    import csv

    out: dict[int, float] = {}
    first = VENUES_OPEN - 30 * B.DAY
    if venue == "lighter":
        end = int(time.time())
        while end > first:
            rows = http(f"{LIGHTER}/fundings", {"market_id": LIGHTER_IDS[sym], "resolution": "1h",
                                                "start_timestamp": first, "end_timestamp": end, "count_back": 750},
                        pause=1.7).get("fundings") or []
            for r in rows:
                sign = 1 if r["direction"] == "long" else -1
                out[int(r["timestamp"]) // 3600 * 3600] = float(r["rate"]) / 100 * sign
            if len(rows) < 700:
                break
            end = min(int(r["timestamp"]) for r in rows) - 1
    elif venue == "arcus":
        us = 1_000_000
        to = int(time.time() * us)
        while to > first * us:
            rows = http(f"{ARCUS}/fundingRates", {"market": f"{sym}-USD", "from": first * us, "to": to, "limit": 1000},
                        pause=6.0).get("fundingRates") or []
            for r in rows:
                out[int(r["time"]) // us // 3600 * 3600] = float(r["fundingRate"])
            if len(rows) < 1000:
                break
            to = min(int(r["time"]) for r in rows) - 1
    elif venue == "binance":
        start, pays = 1_700_000_000_000, []          # ms; startTime 0 returns only the latest
        while True:
            rows = http("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym, "startTime": start,
                                                                         "limit": 1000}, pause=0.5)
            pays += [(int(r["fundingTime"]) // 1000, float(r["fundingRate"])) for r in rows]
            if len(rows) < 1000:
                break
            start = int(rows[-1]["fundingTime"]) + 1
        for (t0, _), (t1, rate) in zip(pays, pays[1:], strict=False):
            hours = max(1, round((t1 - t0) / 3600))
            for k in range(hours):
                out[(t1 - (k + 1) * 3600) // 3600 * 3600] = rate / hours
    FUNDING.mkdir(parents=True, exist_ok=True)
    path = FUNDING / f"{venue}_{sym}.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "rate_per_hour"])
        w.writerows(sorted(out.items()))
    if out:
        log(f"{path.name}: {len(out):,} hours from {time.strftime('%Y-%m-%d', time.gmtime(min(out)))}, "
            f"mean {sum(out.values()) / len(out) * 1e4:+.3f} bp an hour")


SOURCES = {
    "alpaca": lambda: [alpaca(s) for s in ("SPY", "QQQ")],
    "gold": lambda: [binance("PAXGUSDT"), binance("XAUUSDT", "futures", "2025-11")],
    "duka": lambda: dukascopy("XAUUSD", start=int(dt.datetime(2025, 9, 1, tzinfo=dt.UTC).timestamp())),
    "lighter": lambda: [lighter(s, backfill=True) for s in ("SPY", "QQQ", "XAU")],
    "arcus": lambda: [arcus(s) for s in ("SPY", "QQQ", "GLD")],
    "daily": lambda: [yahoo_daily(s) for s in ("SPY", "QQQ", "GLD", "GC=F", "TLT", "EEM", "IEF", "SHY")],
    "gold_hours": lambda: duka_hours("XAUUSD"),
    "funding": lambda: [funding("lighter", s) for s in ("SPY", "QQQ", "XAU")]
    + [funding("arcus", s) for s in ("SPY", "QQQ", "GLD")] + [funding("binance", "XAUUSDT")],
}


def main(what: list[str]) -> None:
    for w in what or list(SOURCES):
        SOURCES[w]()
    log("done")


if __name__ == "__main__":
    main(sys.argv[1:])
