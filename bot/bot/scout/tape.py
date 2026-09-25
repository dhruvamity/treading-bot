"""Compact market tape for the scout: best bid/offer changes and trades, per market per UTC day.

Layout: <root>/<MARKET>/<YYYY-MM-DD>/{bbo,trades}-<part>.npz. The recorder appends a part every few minutes (parts
named `rec...`) and the importers write one part per source (`arcusmm-...`), so writers never touch each other's
files; `load_day` concatenates, sorts and de-duplicates. All times are the venue's own timestamps in int64 µs UTC.

    bbo:    ts, bid, ask, bid_sz, ask_sz        a row when either price changes, or sizes change and >= 1 s passed
    trades: ts, px, sz, buy, seq, tid           buy = the TAKER bought; seq = Arcus sequenceNumber (one taker order)
    depth:  ts, bp, bs, ap, as_                 optional: the top DEPTH_N levels, sampled once a second when the book
                                                changed ([rows, DEPTH_N] arrays, best first, 0 where no level)
"""

from __future__ import annotations

import datetime as dt
import gzip
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import orjson

US_DAY = 86_400_000_000
BBO_FIELDS = ("ts", "bid", "ask", "bid_sz", "ask_sz")
DEPTH_N = 10
REC_PART = "rec"   # part-name prefix of everything the scout's own recorder writes (imports use other names)
MIN_REAL_US = 1_750_000_000_000_000  # Arcus REST returns placeholder rows dated 2026-01-01 and earlier


def day_str(ts_us: int) -> str:
    return dt.datetime.fromtimestamp(ts_us / 1e6, dt.UTC).strftime("%Y-%m-%d")


def day_start_us(day: str) -> int:
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    return int(d.timestamp()) * 1_000_000


@dataclass
class DayTape:
    market: str
    day: str
    bbo: dict[str, np.ndarray]
    trades: dict[str, np.ndarray]

    @property
    def n_bbo(self) -> int:
        return len(self.bbo["ts"])

    @property
    def n_trades(self) -> int:
        return len(self.trades["ts"])

    def bbo_hours(self) -> float:
        """Hours of the day covered by BBO data (gaps over 10 minutes count as missing)."""
        ts = self.bbo["ts"]
        if len(ts) < 2:
            return 0.0
        gaps = np.diff(ts)
        return float(np.minimum(gaps, 600_000_000).sum()) / 3.6e9


def empty_bbo() -> dict[str, np.ndarray]:
    return {"ts": np.zeros(0, np.int64), **{k: np.zeros(0, np.float64) for k in BBO_FIELDS[1:]}}


def empty_depth() -> dict[str, np.ndarray]:
    z = np.zeros((0, DEPTH_N))
    return {"ts": np.zeros(0, np.int64), "bp": z, "bs": z, "ap": z, "as_": z}


def empty_trades() -> dict[str, np.ndarray]:
    return {"ts": np.zeros(0, np.int64), "px": np.zeros(0), "sz": np.zeros(0), "buy": np.zeros(0, bool),
            "seq": np.zeros(0, np.int64), "tid": np.zeros(0, np.int64)}


class TapeStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ---------------------------------------------------------------- paths
    def day_dir(self, market: str, day: str) -> Path:
        return self.root / market / day

    def markets(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def days(self, market: str) -> list[str]:
        d = self.root / market
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and any(p.glob("bbo-*.npz")))

    def first_recorded_us(self, market: str) -> int | None:
        """Time of the market's first best bid/offer row written by the scout's recorder. Imported history (the
        arcus-mm import covers 20 markets from 2026-09-19) does not count: it says nothing about when the recorder
        started or when it first saw a market."""
        for day in self.days(market):
            first = []
            for p in sorted(self.day_dir(market, day).glob(f"bbo-{REC_PART}*.npz")):
                if p.name.endswith(".tmp.npz"):
                    continue
                with np.load(p) as z:
                    ts = z["ts"]
                if len(ts):
                    first.append(int(ts.min()))
            if first:
                return min(first)
        return None

    # ---------------------------------------------------------------- write
    def write_part(self, market: str, kind: str, part: str, arrays: dict[str, np.ndarray]) -> list[Path]:
        """Split rows by UTC day and write one part file per day. Atomic (tmp + rename)."""
        ts = arrays["ts"]
        if len(ts) == 0:
            return []
        out = []
        days = (ts // US_DAY).astype(np.int64)
        for d in np.unique(days):
            sel = days == d
            day = day_str(int(d) * US_DAY)
            path = self.day_dir(market, day) / f"{kind}-{part}.npz"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp.npz")
            np.savez_compressed(tmp, **{k: v[sel] for k, v in arrays.items()})
            os.replace(tmp, path)
            out.append(path)
        return out

    # ---------------------------------------------------------------- read
    def load_day(self, market: str, day: str) -> DayTape:
        d = self.day_dir(market, day)
        bbo = _concat([_load(p) for p in sorted(d.glob("bbo-*.npz")) if not p.name.endswith(".tmp.npz")],
                      empty_bbo())
        trades = _concat([_load(p) for p in sorted(d.glob("trades-*.npz")) if not p.name.endswith(".tmp.npz")],
                         empty_trades())
        if len(bbo["ts"]):
            o = np.argsort(bbo["ts"], kind="stable")
            bbo = {k: v[o] for k, v in bbo.items()}
            keep = np.ones(len(o), bool)
            keep[1:] = (np.diff(bbo["ts"]) != 0) | (np.diff(bbo["bid"]) != 0) | (np.diff(bbo["ask"]) != 0)
            bbo = {k: v[keep] for k, v in bbo.items()}
        if len(trades["ts"]):
            _, first = np.unique(trades["tid"], return_index=True)
            trades = {k: v[first] for k, v in trades.items()}
            o = np.lexsort((trades["tid"], trades["ts"]))
            trades = {k: v[o] for k, v in trades.items()}
        return DayTape(market, day, bbo, trades)

    def load_depth(self, market: str, start_us: int, end_us: int) -> dict[str, np.ndarray]:
        """Depth rows in [start_us, end_us), sorted by time (empty when none were recorded)."""
        parts = []
        d = start_us - start_us % US_DAY
        while d < end_us:
            dd = self.day_dir(market, day_str(d))
            parts += [_load(p) for p in sorted(dd.glob("depth-*.npz")) if not p.name.endswith(".tmp.npz")]
            d += US_DAY
        out = _concat(parts, empty_depth())
        o = np.argsort(out["ts"], kind="stable")
        sel = o[(out["ts"][o] >= start_us) & (out["ts"][o] < end_us)]
        return {k: v[sel] for k, v in out.items()}

    def load_range(self, market: str, start_us: int, end_us: int) -> DayTape:
        """Rows in [start_us, end_us) across day boundaries (for rolling windows)."""
        parts = []
        d = start_us - start_us % US_DAY
        while d < end_us:
            parts.append(self.load_day(market, day_str(d)))
            d += US_DAY
        bbo = _concat([p.bbo for p in parts], empty_bbo())
        tr = _concat([p.trades for p in parts], empty_trades())
        sb = (bbo["ts"] >= start_us) & (bbo["ts"] < end_us)
        st = (tr["ts"] >= start_us) & (tr["ts"] < end_us)
        return DayTape(market, day_str(start_us), {k: v[sb] for k, v in bbo.items()},
                       {k: v[st] for k, v in tr.items()})


def _load(p: Path) -> dict[str, np.ndarray]:
    with np.load(p) as z:
        return {k: z[k] for k in z.files}


def _concat(parts: list[dict[str, np.ndarray]], empty: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    parts = [p for p in parts if len(p.get("ts", ())) > 0]
    if not parts:
        return empty
    return {k: np.concatenate([p[k] for p in parts]) for k in empty}


# ------------------------------------------------------------------------------------------------ builders
class BboBuffer:
    """Accumulates BBO rows, keeping a row when a price changes, or sizes change and >= 1 s has passed."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, float, float, float, float]] = []
        self._last: tuple[int, float, float] | None = None

    def add(self, ts: int, bid: float, ask: float, bid_sz: float, ask_sz: float) -> None:
        last = self._last
        if last is not None and last[1] == bid and last[2] == ask and ts - last[0] < 1_000_000:
            return
        self.rows.append((ts, bid, ask, bid_sz, ask_sz))
        self._last = (ts, bid, ask)

    def take(self) -> dict[str, np.ndarray]:
        rows, self.rows = self.rows, []
        if not rows:
            return empty_bbo()
        a = np.array(rows, dtype=np.float64)
        return {"ts": a[:, 0].astype(np.int64), "bid": a[:, 1], "ask": a[:, 2], "bid_sz": a[:, 3], "ask_sz": a[:, 4]}


class DepthBuffer:
    """Top-of-book levels, one row per sample."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, list[float], list[float], list[float], list[float]]] = []

    def add(self, ts: int, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> None:
        def side(lv: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
            px = [p for p, _ in lv[:DEPTH_N]]
            sz = [q for _, q in lv[:DEPTH_N]]
            pad = DEPTH_N - len(px)
            return px + [0.0] * pad, sz + [0.0] * pad
        bp, bs = side(bids)
        ap, as_ = side(asks)
        self.rows.append((ts, bp, bs, ap, as_))

    def take(self) -> dict[str, np.ndarray]:
        rows, self.rows = self.rows, []
        if not rows:
            return empty_depth()
        return {"ts": np.array([r[0] for r in rows], np.int64), "bp": np.array([r[1] for r in rows]),
                "bs": np.array([r[2] for r in rows]), "ap": np.array([r[3] for r in rows]),
                "as_": np.array([r[4] for r in rows])}


class TradeBuffer:
    def __init__(self) -> None:
        self.rows: list[tuple[int, float, float, bool, int, int]] = []

    def add_raw(self, t: dict[str, Any]) -> None:
        ts = int(t["timestamp"])
        if ts < MIN_REAL_US:
            return
        self.rows.append((ts, float(t["price"]), float(t["size"]), str(t["side"]).upper() == "BUY",
                          int(t.get("sequenceNumber") or 0), int(t["tradeId"])))

    def take(self) -> dict[str, np.ndarray]:
        rows, self.rows = self.rows, []
        if not rows:
            return empty_trades()
        ts, px, sz, buy, seq, tid = zip(*rows, strict=True)
        return {"ts": np.array(ts, np.int64), "px": np.array(px), "sz": np.array(sz), "buy": np.array(buy, bool),
                "seq": np.array(seq, np.int64), "tid": np.array(tid, np.int64)}


# ------------------------------------------------------------------------------------------------ import
def _lines(path: Path) -> Iterator[bytes]:
    opener: Any = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        yield from f


def import_recorded_bbo(paths: Iterable[Path]) -> dict[str, np.ndarray]:
    """Recorder JSONL frames ({"data": {"contents": {"bestBid": {...}, "bestAsk": {...}, "timestamp"}}})."""
    buf = BboBuffer()
    for path in paths:
        for line in _lines(path):
            try:
                c = orjson.loads(line)["data"]["contents"]
                b, a = c.get("bestBid") or {}, c.get("bestAsk") or {}
                if not b.get("price") or not a.get("price"):
                    continue
                buf.add(int(c["timestamp"]), float(b["price"]), float(a["price"]), float(b.get("size") or 0),
                        float(a.get("size") or 0))
            except (KeyError, TypeError, ValueError, orjson.JSONDecodeError):
                continue
    out = buf.take()
    o = np.argsort(out["ts"], kind="stable")
    return {k: v[o] for k, v in out.items()}


def import_trade_lines(paths: Iterable[Path]) -> dict[str, np.ndarray]:
    """Recorder frames ({"data": {"contents": [trade, ...]}}) or REST history lines (one trade object per line)."""
    buf = TradeBuffer()
    for path in paths:
        for line in _lines(path):
            try:
                obj = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            rows = obj.get("data", {}).get("contents") if "data" in obj else [obj]
            for t in rows or []:
                try:
                    buf.add_raw(t)
                except (KeyError, TypeError, ValueError):
                    continue
    return buf.take()
