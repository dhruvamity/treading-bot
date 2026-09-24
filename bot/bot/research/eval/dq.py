"""Data-quality report (P0 task 9): per table/market/day - row counts, time coverage, max silence, sequence gaps,
duplicate trade ids, timestamp-unit sanity, funding hours missing. Markdown to reports/dq/{date}.md."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bot.common.time import looks_like_unit
from bot.research.loaders.read import read_table

TABLES = ("book_deltas", "book_snapshots", "bbo", "trades", "mark_oracle_index", "funding_pred", "funding_paid",
          "market_attrs", "recorder_health")


def _ts_col(df: pl.DataFrame) -> str | None:
    for c in ("recv_ts_us", "funding_ts_us", "ts_us"):
        if c in df.columns:
            return c
    return None


def dq_report(root: Path, date: str, reports: Path) -> Path:
    lines = [f"# Data quality {date}", "", "| Table | Venue | Market | Rows | First | Last | Coverage h | Max silence s |"
             " Issues |", "|---|---|---|---|---|---|---|---|---|"]
    issues_total = 0
    for t in TABLES:
        df = read_table(root, t, where=f"date = '{date}'") if (root / t).exists() else pl.DataFrame()
        if df.is_empty():
            lines.append(f"| {t} | - | - | 0 | | | | | no data |")
            continue
        tc = _ts_col(df)
        for (venue, market), g in df.group_by(["venue", "market"], maintain_order=True):
            issues = []
            ts = g[tc].drop_nulls().sort() if tc else pl.Series([], dtype=pl.Int64)
            first = int(ts[0]) if len(ts) else 0
            last = int(ts[-1]) if len(ts) else 0
            cov_h = (last - first) / 3.6e9 if len(ts) else 0.0
            gaps = ts.diff().drop_nulls()
            gmax = gaps.max()
            max_sil = float(gmax) / 1e6 if len(gaps) and isinstance(gmax, int | float) else 0.0
            if len(ts) and looks_like_unit(first) != "us":
                issues.append(f"timestamp unit looks like {looks_like_unit(first)}")
            if t == "trades" and "trade_id" in g.columns:
                dups = g.height - g["trade_id"].n_unique()
                if dups:
                    issues.append(f"{dups} duplicate trade ids")
            if t == "recorder_health" and "gaps" in g.columns:
                gm = g["gaps"].max()
                mg = int(gm) if isinstance(gm, int | float) else 0
                if mg:
                    issues.append(f"{mg} sequence gaps (resubscribed)")
            if t in ("bbo", "book_deltas") and max_sil > 60:
                issues.append(f"silent {max_sil:.0f}s")
            if t == "funding_paid" and cov_h > 0:
                hours = g["funding_ts_us"].n_unique()
                expect = int(cov_h) + 1
                if hours < expect:
                    issues.append(f"{expect - hours} funding hours missing")
            issues_total += len(issues)
            lines.append(f"| {t} | {venue} | {market} | {g.height} | {first} | {last} | {cov_h:.2f} | {max_sil:.1f} | "
                         f"{'; '.join(issues)} |")
    lines += ["", f"Issues flagged: {issues_total}.", ""]
    out = reports / "dq"
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{date}.md"
    p.write_text("\n".join(lines))
    return p
