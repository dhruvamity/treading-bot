"""Summaries of paper-farm results: the synthetic study's scenario tables, and the per-hour view of a live run."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from bot.farm.analyze import fmt_table
from bot.farm.menu import BY_NAME


def _med(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return round(float(np.median(xs)), 2) if xs else None


def by_setting(rows: list[dict[str, Any]], keys: tuple[str, ...] = ("setting", "leverage")) -> list[dict[str, Any]]:
    """One row per group: medians across the scenarios, and how often the setting was near breakeven."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in keys)].append(r)
    out = []
    for k, rs in groups.items():
        vol = sum(r["volume_usd"] for r in rs)
        pnl = sum(r["pnl"] for r in rs)
        live = [r for r in rs if r["volume_usd"] > 0]
        out.append({**dict(zip(keys, k, strict=True)), "family": BY_NAME[rs[0]["setting"]].family,
                    "runs": len(rs), "turnover_per_h": _med([r["turnover_per_h"] for r in rs]),
                    "fills_per_h": _med([r["fills_per_h"] for r in rs]),
                    "cpm": round(-pnl / vol * 1e6, 1) if vol > 0 else None,
                    "median_cpm": _med([r["cpm"] for r in live]),
                    "pnl_pct_med": _med([r["pnl_pct"] for r in rs]),
                    "near_be": f"{sum(1 for r in live if r['day_loss_pct'] <= 5 and not r['killed'])}/{len(rs)}",
                    "profitable": f"{sum(1 for r in rs if r['pnl'] > 0)}/{len(rs)}",
                    "worst_dd_pct": round(max(r["max_dd_pct"] for r in rs), 2),
                    "worst_day_loss_pct": round(max(r["day_loss_pct"] for r in rs), 2),
                    "kills": sum(1 for r in rs if r["killed"]),
                    "risk": risk_mode([r["risk"] for r in rs])})
    return out


def risk_mode(labels: list[str]) -> str:
    """The label a setting earns across scenarios: its second-worst (one bad scenario is not the rule), shown with
    the spread of labels."""
    order = ["R1", "R2", "R3", "R4", "U"]
    ls = sorted((x for x in labels if x != "U"), key=order.index)
    if not ls:
        return "U"
    pick = ls[-2] if len(ls) >= 2 else ls[-1]
    counts = " ".join(f"{k}:{ls.count(k)}" for k in order[:4] if ls.count(k))
    return f"{pick} ({counts})"


COLS = [("setting", "setting"), ("leverage", "lev"), ("family", "family"), ("turnover_per_h", "turnover/h"),
        ("fills_per_h", "fills/h"), ("cpm", "CPM"), ("median_cpm", "median CPM"), ("pnl_pct_med", "median PnL %"),
        ("near_be", "near BE"), ("profitable", "profitable"), ("worst_dd_pct", "worst DD %"),
        ("worst_day_loss_pct", "worst loss/day %"), ("kills", "kills"), ("risk", "risk")]


def synth_summary(rows: list[dict[str, Any]], lev: float = 10.0) -> str:
    rows = [r for r in rows if r["leverage"] == lev]
    lines = ["# Synthetic mechanics study", "",
             "**Synthetic data: these numbers come from a model market (`bot/bot/farm/synth.py`), not from a real "
             "one.** They show how each setting reacts to chop, trend and toxic flow under the same fill model the "
             "live runs use. They are not evidence of real edge.", "",
             f"Each paper account: $100 at {lev:g}x (capped at the model market's maximum), stops 5% / 10% / 20%. "
             "Each scenario is 12 hours (30 minutes of warm-up excluded). CPM = dollars lost per $1M traded "
             "(negative = profit). Near BE = projected loss at most 5% of the capital per day. Risk = the "
             "second-worst label across scenarios, with the full count.", ""]
    profiles = sorted({r["profile"] for r in rows})
    for pn in profiles:
        rs = [r for r in rows if r["profile"] == pn]
        agg = sorted(by_setting(rs), key=lambda r: -(r["turnover_per_h"] or 0))
        lines += [f"## Market type `{pn}`: all regimes and flow levels", "", fmt_table(agg, COLS, 100), ""]
    lines += ["## Toxic flow: cost per $1M by informed-flow level (all market types and regimes)", ""]
    tox = by_setting(rows, ("setting", "informed_p"))
    piv: dict[str, dict[str, Any]] = {}
    for r in tox:
        d = piv.setdefault(r["setting"], {"setting": r["setting"], "family": r["family"]})
        d[f"cpm_i{int(r['informed_p'] * 100)}"] = r["cpm"]
        d[f"to_i{int(r['informed_p'] * 100)}"] = r["turnover_per_h"]
    inf_levels = sorted({int(r["informed_p"] * 100) for r in rows})
    cols = [("setting", "setting"), ("family", "family")]
    for i in inf_levels:
        cols += [(f"to_i{i}", f"turnover/h @{i}%"), (f"cpm_i{i}", f"CPM @{i}%")]
    lines += [fmt_table(sorted(piv.values(), key=lambda r: r["setting"]), cols, 100), ""]
    lines += ["## Regime: cost per $1M by regime (all market types and flow levels)", ""]
    reg = by_setting(rows, ("setting", "regime"))
    piv = {}
    for r in reg:
        d = piv.setdefault(r["setting"], {"setting": r["setting"], "family": r["family"]})
        d[f"cpm_{r['regime']}"] = r["cpm"]
        d[f"dd_{r['regime']}"] = r["worst_dd_pct"]
    regs = sorted({r["regime"] for r in rows})
    cols = [("setting", "setting"), ("family", "family")]
    for g in regs:
        cols += [(f"cpm_{g}", f"CPM {g}"), (f"dd_{g}", f"worst DD % {g}")]
    lines += [fmt_table(sorted(piv.values(), key=lambda r: r["setting"]), cols, 100), ""]
    return "\n".join(lines) + "\n"


def hourly_view(run_dir: Path, settings: list[str], lev: float = 10.0) -> list[dict[str, Any]]:
    """Per market, setting and hour of a live run: volume and PnL in that hour, from the per-minute paper series."""
    out = []
    for p in sorted((run_dir / "paper").glob("*.npz")):
        z = np.load(p)
        keys = list(z["keys"])
        for s in settings:
            k = f"{s} @ {lev:g}x"
            if k not in keys:
                continue
            i = keys.index(k)
            t, m = z["minute_t"][i], z["minutes"][i]      # minute columns: eq, pos_usd, maker_usd, maker_fills, ...
            ok = t > 0
            t, m = t[ok], m[ok]
            if not len(t):
                continue
            hr = ((t - t[0]) // 3_600_000_000).astype(int)
            prev_eq = prev_vol = 0.0
            for h in range(int(hr.max()) + 1):
                sel = m[hr == h]
                if not len(sel):
                    continue
                eq, vol = float(sel[-1, 0]), float(sel[-1, 2] + sel[-1, 4])
                out.append({"market": p.stem, "setting": s, "hour": h, "volume_usd": round(vol - prev_vol, 2),
                            "pnl": round(eq - prev_eq, 4)})
                prev_eq, prev_vol = eq, vol
    return out
