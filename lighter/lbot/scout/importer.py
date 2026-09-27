"""Import the old recorder's Parquet files (treading-bot's Docker-era recorder, venue=lighter_rh) into the tape.

Source layout: <root>/<table>/venue=lighter_rh/market=<SYM>/date=<YYYY-MM-DD>/part-*.parquet with the tables bbo,
trades, book_deltas (snapshots flagged is_snapshot), mark_oracle_index and funding_pred; prices are decimal strings.
The depth rows are rebuilt from the book changes: the book as it stood at the end of each second in which it changed,
its top DEPTH levels per side (the same thing the live recorder writes).
"""

from __future__ import annotations

import glob
import hashlib
from pathlib import Path

import numpy as np

from lbot.scout.tape import DEPTH, Tape, day_of
from lbot.venue.book import Book

US = 1_000_000


def _read(root: Path, table: str, market: str, day: str, cols: list[str]) -> dict[str, np.ndarray]:
    import pyarrow.parquet as pq  # research extra: pip install '.[research]'

    files = sorted(glob.glob(str(root / table / "venue=lighter_rh" / f"market={market}" / f"date={day}" / "*.parquet")))
    if not files:
        return {}
    tabs = [pq.read_table(f, columns=[c for c in cols if c in pq.read_schema(f).names]) for f in files]
    out: dict[str, list[np.ndarray]] = {}
    for t in tabs:
        for c in t.column_names:
            out.setdefault(c, []).append(t.column(c).to_numpy(zero_copy_only=False))
    return {k: np.concatenate(v) for k, v in out.items()}


def _num(a: np.ndarray) -> np.ndarray:
    return np.asarray([float(x) if x not in (None, "") else np.nan for x in a], dtype=np.float64) \
        if a.dtype == object else a.astype(np.float64)


def _grp(taker: np.ndarray, seq: np.ndarray) -> np.ndarray:
    """One taker transaction: the taker's account and the trade message's nonce."""
    out = np.empty(len(taker), np.int64)
    for i, (a, s) in enumerate(zip(taker, seq, strict=True)):
        h = hashlib.blake2b(f"{a}:{s}".encode(), digest_size=8).digest()
        out[i] = int.from_bytes(h, "little") & ((1 << 62) - 1)
    return out


def import_day(src: Path, tape: Tape, market: str, day: str) -> dict[str, int]:
    """One market-day from the Parquet root into the tape; returns rows written per kind."""
    counts: dict[str, int] = {}
    # ---- best bid and offer
    b = _read(src, "bbo", market, day, ["venue_ts_us", "bid_px", "bid_sz", "ask_px", "ask_sz"])
    if b:
        o = np.argsort(b["venue_ts_us"], kind="stable")
        cols = {"ts": b["venue_ts_us"][o].astype(np.int64), "bid": _num(b["bid_px"])[o], "ask": _num(b["ask_px"])[o],
                "bid_sz": _num(b["bid_sz"])[o], "ask_sz": _num(b["ask_sz"])[o]}
        ok = (cols["bid"] > 0) & (cols["ask"] > cols["bid"])
        cols = {k: v[ok] for k, v in cols.items()}
        tape.write(market, day, "bbo", cols, part="import")
        counts["bbo"] = int(ok.sum())
    # ---- trades
    t = _read(src, "trades", market, day, ["venue_ts_us", "trade_id", "price", "size", "taker_side", "maker_address",
                                           "taker_address", "seq", "is_liquidation"])
    if t:
        o = np.argsort(t["venue_ts_us"], kind="stable")
        taker = np.asarray([int(x) if str(x).lstrip("-").isdigit() else -1 for x in t["taker_address"]], np.int64)[o]
        maker = np.asarray([int(x) if str(x).lstrip("-").isdigit() else -1 for x in t["maker_address"]], np.int64)[o]
        seq = np.asarray(t["seq"], np.int64)[o]
        cols = {"ts": t["venue_ts_us"][o].astype(np.int64), "px": _num(t["price"])[o], "sz": _num(t["size"])[o],
                "buy": (t["taker_side"][o] == "buy"), "grp": _grp(taker, seq), "taker": taker, "maker": maker,
                "liq": np.asarray(t["is_liquidation"], bool)[o], "tid": np.asarray(t["trade_id"], np.int64)[o]}
        tape.write(market, day, "trades", cols, part="import")
        counts["trades"] = len(cols["ts"])
    # ---- depth, rebuilt from the book changes
    d = _read(src, "book_deltas", market, day, ["venue_ts_us", "seq", "side", "price", "size", "is_snapshot"])
    if d:
        counts["depth"] = _depth(d, tape, market, day)
    # ---- mark, index, funding
    s = _read(src, "mark_oracle_index", market, day, ["venue_ts_us", "mark", "index"])
    f = _read(src, "funding_pred", market, day, ["recv_ts_us", "predicted_rate_h"])
    if s:
        o = np.argsort(s["venue_ts_us"], kind="stable")
        ts = s["venue_ts_us"][o].astype(np.int64)
        sec = ts // US
        keep = np.ones(len(sec), bool)
        keep[1:] = sec[1:] != sec[:-1]
        fund = np.full(int(keep.sum()), np.nan)
        if f:
            fo = np.argsort(f["recv_ts_us"])
            fts, fr = f["recv_ts_us"][fo], _num(f["predicted_rate_h"])[fo]
            k = np.searchsorted(fts, ts[keep], side="right") - 1
            fund = np.where(k >= 0, fr[np.clip(k, 0, None)], np.nan)
        cols = {"ts": ts[keep], "mark": _num(s["mark"])[o][keep], "index": _num(s["index"])[o][keep], "funding": fund}
        tape.write(market, day, "stats", cols, part="import")
        counts["stats"] = int(keep.sum())
    return counts


def _depth(d: dict[str, np.ndarray], tape: Tape, market: str, day: str) -> int:
    o = np.lexsort((d["seq"], d["venue_ts_us"]))
    ts = d["venue_ts_us"][o].astype(np.int64)
    seq = np.asarray(d["seq"], np.int64)[o]
    side = d["side"][o]
    px = _num(d["price"])[o]
    sz = _num(d["size"])[o]
    snap = np.asarray(d["is_snapshot"], bool)[o]
    book = Book(0)
    rows_ts: list[int] = []
    rows: list[tuple[list[float], list[float], list[float], list[float]]] = []
    cur_sec = -1
    dirty = False
    last_snap_seq = -1
    for i in range(len(ts)):
        sec = int(ts[i] // US)
        if sec != cur_sec:
            if dirty and cur_sec >= 0:
                bids, asks = book.depth(DEPTH)
                rows_ts.append((cur_sec + 1) * US - 1)
                rows.append(([p for p, _ in bids], [q for _, q in bids], [p for p, _ in asks], [q for _, q in asks]))
            cur_sec, dirty = sec, False
        if snap[i] and seq[i] != last_snap_seq:
            book.bids.clear()
            book.asks.clear()
            last_snap_seq = int(seq[i])
        (book.bids if side[i] == "b" else book.asks).set(float(px[i]), float(sz[i]))
        dirty = True
    if dirty and cur_sec >= 0:
        bids, asks = book.depth(DEPTH)
        rows_ts.append((cur_sec + 1) * US - 1)
        rows.append(([p for p, _ in bids], [q for _, q in bids], [p for p, _ in asks], [q for _, q in asks]))
    n = len(rows)
    arr = {k: np.zeros((n, DEPTH)) for k in ("bp", "bs", "ap", "as_")}
    for j, (bp, bs, ap, as_) in enumerate(rows):
        arr["bp"][j, :len(bp)] = bp
        arr["bs"][j, :len(bs)] = bs
        arr["ap"][j, :len(ap)] = ap
        arr["as_"][j, :len(as_)] = as_
    tape.write(market, day, "depth", {"ts": np.asarray(rows_ts, np.int64), **arr}, part="import")
    return n


def import_all(src: Path, tape: Tape, markets: list[str] | None = None) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    base = src / "bbo" / "venue=lighter_rh"
    found = sorted(p.name.removeprefix("market=") for p in base.glob("market=*"))
    for m in markets or found:
        for dd in sorted((base / f"market={m}").glob("date=*")):
            day = dd.name.removeprefix("date=")
            out[f"{m} {day}"] = import_day(src, tape, m, day)
    return out


__all__ = ["day_of", "import_all", "import_day"]
