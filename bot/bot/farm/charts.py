"""Charts for the research report (PNG, via matplotlib): the volume-vs-cost frontier of the menu.

x = turnover (volume / capital per hour, log scale), y = cost per $1M traded (negative = profit). The dashed curves
are constant loss per day in % of the capital (loss/day % = turnover/h x 24 x CPM / 10,000): a setting below a curve
loses less than that per day. Colours follow the reference palette's first three categorical slots (validated
all-pairs for scatter plots); everything else is grey.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
GROUPS = {  # family -> (legend label, colour)
    "aggressive": ("At or inside the touch (Mid 0, join, improve, gated Mid 0)", "#2a78d6"),
    "mid": ("A fixed distance from the mid (Mid +1 to +5)", "#eb6834"),
    "grid": ("Grid, RGrid, DGrid", "#1baf7a"),
    "rgrid": ("Grid, RGrid, DGrid", "#1baf7a"),
    "dgrid": ("Grid, RGrid, DGrid", "#1baf7a"),
    "signal": ("Signal (RSI skew)", "#8f8e89"),
}
LOSS_LINES = (1.0, 5.0, 20.0)


def frontier(points: list[dict[str, Any]], path: Path, title: str, subtitle: str,
             label: tuple[str, ...] = ("mid0", "mid0 vgate", "mid+1", "mid+2", "mid+3", "grid+3 r0.5",
                                       "dgrid")) -> None:
    """points: {setting, family, turnover_per_h, cpm}."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [p for p in points if p.get("cpm") is not None and (p.get("turnover_per_h") or 0) > 0]
    fig, ax = plt.subplots(figsize=(9, 5.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    xs = np.array([p["turnover_per_h"] for p in pts])
    ys = np.array([p["cpm"] for p in pts])
    lo, hi = max(0.3, xs.min() * 0.7) if len(xs) else 0.3, xs.max() * 1.4 if len(xs) else 100
    top = max(60.0, float(np.percentile(ys, 95)) * 1.2) if len(ys) else 100.0
    bottom = min(-60.0, float(np.percentile(ys, 5)) * 1.2) if len(ys) else -100.0
    x = np.geomspace(lo, hi, 200)
    for loss in LOSS_LINES:   # CPM at which a turnover loses `loss` % of the capital per day
        ax.plot(x, loss * 1e4 / (x * 24), color=INK2, lw=1, ls=(0, (4, 3)), zorder=1)
        yl = top * 0.9                                      # label each curve where it enters the plot at the top
        xl = loss * 1e4 / (24 * yl)
        if not lo <= xl <= hi:                              # the curve is under the top all the way: its left end
            xl = lo * 1.3 if xl < lo else hi / 1.6
            yl = loss * 1e4 / (24 * xl)
        ax.annotate(f"{loss:g}% of capital a day", (xl, yl), textcoords="offset points", xytext=(5, 0),
                    fontsize=7.5, color=INK2, va="center")
    ax.axhline(0, color=INK2, lw=1, zorder=1)
    order = list(dict.fromkeys(lab for lab, _ in GROUPS.values()))
    for lab_name in order:
        grp = [p for p in pts if GROUPS.get(p["family"], ("Other", ""))[0] == lab_name]
        if not grp:
            continue
        col = next(c for n, c in GROUPS.values() if n == lab_name)
        ax.scatter([p["turnover_per_h"] for p in grp], [p["cpm"] for p in grp], s=46, color=col, edgecolor=SURFACE,
                   linewidth=1.5, zorder=3, label=lab_name)
    for p in pts:
        if p["setting"] in label:
            ax.annotate(p["setting"], (p["turnover_per_h"], p["cpm"]), textcoords="offset points", xytext=(6, -3),
                        fontsize=8, color=INK)
    ax.set_xscale("log")
    ax.set_ylim(bottom, top)
    ax.set_xlim(lo, hi)
    ax.set_xlabel("Turnover: volume ÷ capital, per hour (log scale)", color=INK2, fontsize=9)
    ax.set_ylabel("Cost per $1M traded, USD (below 0 = profit)", color=INK2, fontsize=9)
    ax.grid(True, color=GRID, lw=0.6, zorder=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK2, labelsize=8)
    fig.suptitle(title, x=0.06, ha="left", fontsize=12, color=INK, fontweight="bold")
    ax.set_title(subtitle, loc="left", fontsize=8.5, color=INK2)
    ax.legend(loc="lower left", fontsize=8, frameon=False, labelcolor=INK)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
