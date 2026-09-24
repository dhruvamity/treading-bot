"""Partitioned Parquet writer.

Layout: {root}/{table}/venue={v}/market={BASE}/date={YYYY-MM-DD}/part-{HH}-{uuid}.parquet (zstd).
Rows are buffered per partition+hour and flushed every `flush_s` seconds or `max_rows` rows; files are written to a
temp name and atomically renamed, so readers never see partial files. The hour in the file name rolls files hourly.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from bot.common.time import us_to_dt
from bot.research.recorder.schemas import GLOBAL_TABLES, SCHEMAS

Key = tuple[str, str, str, str, int]  # table, venue, market, date, hour


@dataclass
class ParquetWriter:
    root: Path
    flush_s: float = 30.0
    max_rows: int = 50_000
    disabled_tables: set[str] = field(default_factory=set)
    _buf: dict[Key, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    _rows_total: int = 0
    rows_written: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bytes_written: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    files_written: int = 0

    def add(self, table: str, row: dict[str, Any], *, ts_us: int, venue: str, market: str = "_all") -> None:
        if table in self.disabled_tables:
            return
        if table not in SCHEMAS:
            raise KeyError(f"unknown table {table}")
        dt = us_to_dt(ts_us)
        key = (table, venue, "_all" if table in GLOBAL_TABLES else market, dt.strftime("%Y-%m-%d"), dt.hour)
        self._buf[key].append(row)
        self._rows_total += 1
        if len(self._buf[key]) >= self.max_rows:
            self._flush_key(key)

    def pending_rows(self) -> int:
        return sum(len(v) for v in self._buf.values())

    def flush(self) -> int:
        n = 0
        for key in list(self._buf):
            n += self._flush_key(key)
        return n

    def _flush_key(self, key: Key) -> int:
        rows = self._buf.pop(key, None)
        if not rows:
            return 0
        table, venue, market, date, hour = key
        schema = SCHEMAS[table]
        cols = {name: [r.get(name) for r in rows] for name in schema.names}
        tbl = pa.Table.from_pydict(cols, schema=schema)
        d = self.root / table / f"venue={venue}" / f"market={market}" / f"date={date}"
        d.mkdir(parents=True, exist_ok=True)
        final = d / f"part-{hour:02d}-{uuid.uuid4().hex[:12]}.parquet"
        tmp = d / f".{final.name}.tmp"
        pq.write_table(tbl, tmp, compression="zstd")
        os.replace(tmp, final)
        self.rows_written[table] += len(rows)
        self.bytes_written[table] += final.stat().st_size
        self.files_written += 1
        return len(rows)


def disk_usage_frac(path: Path) -> float:
    u = shutil.disk_usage(path)
    return u.used / u.total


def compact_date_dir(d: Path) -> tuple[int, int]:
    """Merge a closed day's part files into one zstd file (per-file metadata dominated the small flushes).
    Writes the merged file first, then removes the parts; safe to re-run. Returns (parts_merged, rows)."""
    parts = sorted(p for p in d.glob("part-*.parquet"))
    if len(parts) <= 1:
        return 0, 0
    tables = [pq.read_table(p) for p in parts]
    merged = pa.concat_tables(tables, promote_options="default")
    if "recv_ts_us" in merged.column_names:
        merged = merged.sort_by("recv_ts_us")
    out = d / f"compacted-{uuid.uuid4().hex[:12]}.parquet"
    tmp = d / f".{out.name}.tmp"
    pq.write_table(merged, tmp, compression="zstd", row_group_size=250_000)
    os.replace(tmp, out)
    for p in parts:
        p.unlink()
    return len(parts), merged.num_rows


def compact_closed_days(root: Path, today: str) -> dict[str, int]:
    """Compact every date partition strictly before `today` (YYYY-MM-DD)."""
    stats = {"dirs": 0, "parts": 0, "rows": 0}
    for d in root.glob("*/venue=*/market=*/date=*"):
        if d.name.removeprefix("date=") >= today:
            continue
        n, rows = compact_date_dir(d)
        if n:
            stats["dirs"] += 1
            stats["parts"] += n
            stats["rows"] += rows
    return stats
