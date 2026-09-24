"""Read recorded / historical Parquet with DuckDB or polars (hive partitions: venue, market, date)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


def glob_for(root: Path, table: str) -> str:
    return str(root / table / "**" / "*.parquet")


def has_table(root: Path, table: str) -> bool:
    return any((root / table).glob("**/*.parquet"))


def read_table(root: Path, table: str, *, venue: str | None = None, market: str | None = None,
               where: str | None = None) -> pl.DataFrame:
    if not has_table(root, table):
        return pl.DataFrame()
    conds = []
    if venue:
        conds.append(f"venue = '{venue}'")
    if market:
        conds.append(f"market = '{market}'")
    if where:
        conds.append(where)
    q = f"SELECT * FROM read_parquet('{glob_for(root, table)}', hive_partitioning=true, union_by_name=true)"
    if conds:
        q += " WHERE " + " AND ".join(conds)
    con = duckdb.connect()
    try:
        return con.execute(q).pl()
    finally:
        con.close()


def funding_panel(root: Path, market: str) -> pl.DataFrame:
    """Hourly panel F[h] = (r_A, r_L, spread) for one market from funding_paid (history + live)."""
    frames = []
    for sub in ("history", ""):
        r = root / sub if sub else root
        df = read_table(r, "funding_paid", market=market)
        if not df.is_empty():
            frames.append(df.select(["funding_ts_us", "venue", "rate_h", "pay_price", "index_source"]))
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames, how="vertical_relaxed").unique(subset=["funding_ts_us", "venue"], keep="first")
    a = df.filter(pl.col("venue") == "arcus").select(pl.col("funding_ts_us"), pl.col("rate_h").alias("r_a"))
    lr = df.filter(pl.col("venue") == "lighter_rh").select(pl.col("funding_ts_us"), pl.col("rate_h").alias("r_l"),
                                                           pl.col("pay_price").alias("px_l"))
    return a.join(lr, on="funding_ts_us", how="inner").sort("funding_ts_us").with_columns(
        (pl.col("r_l") - pl.col("r_a")).alias("spread_l_minus_a"))
