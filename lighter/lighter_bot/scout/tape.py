"""The recorded Lighter market tape: per market per UTC day, compact NumPy files.

Layout: <data>/tape/<SYMBOL>/<YYYY-MM-DD>/<kind>-<part>.npz. The recorder writes a new part every few minutes
(each start has its own part names), so writers never touch each other's files; `load_day` joins, sorts and de-duplicates.
Times are Lighter's own, in int64 microseconds UTC.

    bbo:    ts, bid, ask, bid_sz, ask_sz        a row per ticker change (every nonce that moved the best bid or ask)
    trades: ts, px, sz, buy, grp, taker, maker, liq, tid
            buy = the taker bought; grp = one taker transaction (its prints share it); taker/maker = account indexes;
            liq = a liquidation; tid = Lighter's trade id
    depth:  ts, bp, bs, ap, as_                 the top DEPTH levels each side ([rows, DEPTH], best first, 0 = none),
                                                once a second when the book changed
    stats:  ts, mark, index, funding            once a second: mark and index price, the next funding rate (a fraction
                                                per hour)
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

US = 1_000_000
US_DAY = 86_400 * US
DEPTH = 20

FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "bbo": (("ts", "i8"), ("bid", "f8"), ("ask", "f8"), ("bid_sz", "f8"), ("ask_sz", "f8")),
    "trades": (("ts", "i8"), ("px", "f8"), ("sz", "f8"), ("buy", "?"), ("grp", "i8"), ("taker", "i8"),
               ("maker", "i8"), ("liq", "?"), ("tid", "i8")),
    "stats": (("ts", "i8"), ("mark", "f8"), ("index", "f8"), ("funding", "f8")),
}
KINDS = ("bbo", "trades", "depth", "stats")


def day_of(ts_us: int) -> str:
    return dt.datetime.fromtimestamp(ts_us / US, dt.UTC).strftime("%Y-%m-%d")


def day_start(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp()) * US


def empty(kind: str, n_depth: int = DEPTH) -> dict[str, np.ndarray]:
    if kind == "depth":
        z = np.zeros((0, n_depth))
        return {"ts": np.zeros(0, np.int64), "bp": z, "bs": z, "ap": z, "as_": z.copy()}
    return {k: np.zeros(0, t) for k, t in FIELDS[kind]}


@dataclass
class DayTape:
    market: str
    day: str
    bbo: dict[str, np.ndarray] = field(default_factory=lambda: empty("bbo"))
    trades: dict[str, np.ndarray] = field(default_factory=lambda: empty("trades"))
    depth: dict[str, np.ndarray] = field(default_factory=lambda: empty("depth"))
    stats: dict[str, np.ndarray] = field(default_factory=lambda: empty("stats"))

    def hours(self) -> float:
        """Hours of the day the recording covers: time between rows (BBO or trades), gaps over 10 minutes excluded."""
        ts = np.sort(np.concatenate([self.bbo["ts"], self.trades["ts"]]))
        if len(ts) < 2:
            return 0.0
        return float(np.minimum(np.diff(ts), 600 * US).sum()) / 3600 / US


class Tape:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def markets(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def days(self, market: str) -> list[str]:
        d = self.root / market
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and len(p.name) == 10)

    def write(self, market: str, day: str, kind: str, cols: dict[str, np.ndarray], part: str | None = None) -> Path:
        d = self.root / market / day
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{kind}-{part or uuid.uuid4().hex[:12]}.npz"
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, **cols)
        os.replace(tmp, p)
        return p

    def _parts(self, market: str, day: str, kind: str) -> Iterator[dict[str, np.ndarray]]:
        d = self.root / market / day
        if not d.exists():
            return
        for p in sorted(d.glob(f"{kind}-*.npz")):
            try:
                with np.load(p) as z:
                    yield {k: z[k] for k in z.files}
            except (OSError, ValueError, EOFError):
                continue   # a part cut off by a crash; the rest of the day still loads

    def load(self, market: str, day: str, kind: str) -> dict[str, np.ndarray]:
        parts = [p for p in self._parts(market, day, kind) if len(p.get("ts", ())) > 0]
        if not parts:
            return empty(kind)
        keys = parts[0].keys()
        cols = {k: np.concatenate([p[k] for p in parts if k in p]) for k in keys}
        order = np.argsort(cols["ts"], kind="stable")
        cols = {k: v[order] for k, v in cols.items()}
        # de-duplicate what two writers both recorded: trades by their id, other rows when every column repeats
        n = len(cols["ts"])
        if kind == "trades" and "tid" in cols and n:
            _, first = np.unique(cols["tid"], return_index=True)
            keep = np.zeros(n, bool)
            keep[first] = True
        else:
            keep = np.ones(n, bool)
            if n > 1:
                same = cols["ts"][1:] == cols["ts"][:-1]
                for k, v in cols.items():
                    if k != "ts":
                        a, b = v[1:], v[:-1]
                        same &= (a == b).all(axis=1) if a.ndim == 2 else (a == b)
                keep[1:] = ~same
        return {k: v[keep] for k, v in cols.items()}

    def load_day(self, market: str, day: str, kinds: tuple[str, ...] = KINDS) -> DayTape:
        t = DayTape(market, day)
        for k in kinds:
            setattr(t, k, self.load(market, day, k))
        return t
