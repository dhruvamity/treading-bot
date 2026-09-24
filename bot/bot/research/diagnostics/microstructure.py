"""Microstructure diagnostics on recorded data (P4 task 1), per venue x market x session:
spread and displayed depth by hour of week, realised volatility, OER at candidate spacings, ER / variance ratio,
Arcus taker concentration (HHI over takerAddress), and the cross-venue basis (mean, sigma, OU half-life).
These pick the universe before any strategy is trusted. Markdown + Parquet under reports/diagnostics/{date}/."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from bot.autopilot.features import efficiency_ratio, oer, variance_ratio
from bot.research.diagnostics.carry import ar1_halflife, session_of
from bot.research.loaders.read import read_table

DELTAS_BPS = (5, 10, 15, 25, 40, 60, 100)


def _mid_series(root: Path, venue: str, market: str) -> pl.DataFrame:
    bbo = read_table(root, "bbo", venue=venue, market=market)
    if bbo.is_empty():
        return bbo
    return (bbo.filter(pl.col("bid_px").is_not_null() & pl.col("ask_px").is_not_null())
            .with_columns(bid=pl.col("bid_px").cast(pl.Float64), ask=pl.col("ask_px").cast(pl.Float64))
            .with_columns(mid=(pl.col("bid") + pl.col("ask")) / 2,
                          spread_bps=(pl.col("ask") - pl.col("bid")) / ((pl.col("bid") + pl.col("ask")) / 2) * 1e4)
            .select(["recv_ts_us", "mid", "spread_bps"]).sort("recv_ts_us"))


def _minute_closes(mids: pl.DataFrame) -> list[float]:
    if mids.is_empty():
        return []
    m = mids.with_columns(minute=(pl.col("recv_ts_us") // 60_000_000)).group_by("minute").agg(pl.col("mid").last()).sort("minute")
    return [float(x) for x in m["mid"].to_list()]


def _depth(root: Path, venue: str, market: str) -> tuple[float, float]:
    snaps = read_table(root, "book_snapshots", venue=venue, market=market)
    if snaps.is_empty():
        return math.nan, math.nan
    d10 = []
    for bids, asks in zip(snaps["bids"].to_list(), snaps["asks"].to_list(), strict=True):
        if not bids or not asks:
            continue
        bb, ba = float(bids[0]["price"]), float(asks[0]["price"])
        mid = (bb + ba) / 2
        tot = sum(float(x["price"]) * float(x["size"]) for x in bids if float(x["price"]) >= mid * (1 - 1e-3))
        tot += sum(float(x["price"]) * float(x["size"]) for x in asks if float(x["price"]) <= mid * (1 + 1e-3))
        d10.append(tot)
    return (float(np.median(d10)) if d10 else math.nan, float(np.percentile(d10, 10)) if d10 else math.nan)


def _taker_hhi(root: Path, market: str) -> tuple[float, str | None, float]:
    tr = read_table(root, "trades", venue="arcus", market=market)
    if tr.is_empty() or "taker_address" not in tr.columns:
        return math.nan, None, math.nan
    g = (tr.with_columns(notional=pl.col("price").cast(pl.Float64) * pl.col("size").cast(pl.Float64))
         .group_by("taker_address").agg(pl.col("notional").sum()).sort("notional", descending=True))
    tot = g["notional"].sum()
    if not tot:
        return math.nan, None, math.nan
    shares = (g["notional"] / tot).to_numpy()
    return float((shares ** 2).sum()), g["taker_address"][0], float(shares[0])


def diagnose(root: Path, market: str) -> dict[str, object]:
    out: dict[str, object] = {"market": market}
    mids = {}
    for v in ("arcus", "lighter_rh"):
        ms = _mid_series(root, v, market)
        mids[v] = ms
        if ms.is_empty():
            continue
        closes = _minute_closes(ms)
        rets = np.diff(np.log(closes)) if len(closes) > 2 else np.array([])
        out[f"{v}_minutes"] = len(closes)
        med, q90 = ms["spread_bps"].median(), ms["spread_bps"].quantile(0.9)
        out[f"{v}_spread_bps_p50"] = float(med) if isinstance(med, int | float) else math.nan
        out[f"{v}_spread_bps_p90"] = float(q90) if q90 is not None else math.nan
        out[f"{v}_vol_1m_bps"] = float(rets.std() * 1e4) if len(rets) > 2 else math.nan
        out[f"{v}_er_60m"] = efficiency_ratio(closes, 60) if len(closes) > 61 else math.nan
        out[f"{v}_variance_ratio"] = variance_ratio(closes) if len(closes) > 61 else math.nan
        for d in DELTAS_BPS:
            out[f"{v}_oer_{d}bp"] = oer(closes[-240:], d * 1e-4) if len(closes) > 3 else math.nan
        out[f"{v}_depth10bp_p50_usd"], out[f"{v}_depth10bp_p10_usd"] = _depth(root, v, market)
        sess = ms.with_columns(session=pl.col("recv_ts_us").map_elements(session_of, return_dtype=pl.String))
        out[f"{v}_spread_by_session"] = {r["session"]: round(r["spread_bps"], 3) for r in
                                         sess.group_by("session").agg(pl.col("spread_bps").median()).to_dicts()}
    hhi, top, top_share = _taker_hhi(root, market)
    out["arcus_taker_hhi"], out["arcus_top_taker"], out["arcus_top_taker_share"] = hhi, top, top_share
    a, lt = mids.get("arcus"), mids.get("lighter_rh")
    if a is not None and lt is not None and not a.is_empty() and not lt.is_empty():
        j = (a.with_columns(s=pl.col("recv_ts_us") // 1_000_000).group_by("s").agg(pl.col("mid").last().alias("a"))
             .join(lt.with_columns(s=pl.col("recv_ts_us") // 1_000_000).group_by("s").agg(pl.col("mid").last().alias("l")),
                   on="s").sort("s"))
        if j.height > 30:
            basis = ((j["a"] - j["l"]) / j["l"] * 1e4).to_numpy()
            out["basis_bps_mean"] = float(basis.mean())
            out["basis_bps_sd"] = float(basis.std())
            hl = ar1_halflife(basis)
            out["basis_halflife_s"] = hl
    return out


def diagnostics_report(root: Path, markets: list[str], reports: Path) -> Path:
    rows = [diagnose(root, m) for m in markets]
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out = reports / "diagnostics" / day
    out.mkdir(parents=True, exist_ok=True)
    flat = [{k: (str(v) if isinstance(v, dict) else v) for k, v in r.items()} for r in rows]
    pl.DataFrame(flat).write_parquet(out / "diagnostics.parquet")
    L = [f"# Microstructure diagnostics - {day}", "",
         "From the recorder's Parquet (bbo, book snapshots, trades). Short samples are NOT decision grade: the spec "
         "needs 2-4 weeks before universe selection.", "",
         "| Market | Venue | Minutes | Spread p50/p90 bp | 1m vol bp | ER60 | VR | OER 10/25/60 bp | Depth +-10bp p50/p10 $ |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        for v in ("arcus", "lighter_rh"):
            if f"{v}_minutes" not in r:
                continue
            L.append(f"| {r['market']} | {v} | {r[f'{v}_minutes']} | {r[f'{v}_spread_bps_p50']:.2f}/"
                     f"{r[f'{v}_spread_bps_p90']:.2f} | {r[f'{v}_vol_1m_bps']:.2f} | {r[f'{v}_er_60m']:.2f} | "
                     f"{r[f'{v}_variance_ratio']:.2f} | {r[f'{v}_oer_10bp']:.2f}/{r[f'{v}_oer_25bp']:.2f}/"
                     f"{r[f'{v}_oer_60bp']:.2f} | {r[f'{v}_depth10bp_p50_usd']:.0f}/{r[f'{v}_depth10bp_p10_usd']:.0f} |")
    L += ["", "## Arcus taker concentration and cross-venue basis", "",
          "| Market | Taker HHI | Top taker share | Basis mean bp | Basis sd bp | Basis half-life s |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['market']} | {r.get('arcus_taker_hhi', math.nan):.3f} | {r.get('arcus_top_taker_share', math.nan):.0%} | "
                 f"{r.get('basis_bps_mean', math.nan):+.2f} | {r.get('basis_bps_sd', math.nan):.2f} | "
                 f"{r.get('basis_halflife_s', math.nan):.0f} |")
    p = out / "DIAGNOSTICS.md"
    p.write_text("\n".join(L) + "\n")
    return p
