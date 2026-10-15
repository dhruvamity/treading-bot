"""Do the venues' own prices give the same trades as the reference history? (`daytrade check`)

The long backtests run on the ETFs as traded in New York and on gold from Binance; the bot would trade the venues'
perpetuals. Over the months both exist (since the venues opened, late June 2026) this runs each main strategy on
both and counts the trades that match (same day, side, and entry within two minutes), and how far the prices sit
apart in the strategy's hours.
"""

from __future__ import annotations

import numpy as np

from . import REPORTS
from . import bars as B
from .core import MARKETS
from .sim import run
from .strategies import EmaPullback, LondonSweep, OpeningRange

PAIRS = [("SPY", "alpaca_SPY", ["lighter_SPY", "arcus_SPY"]), ("QQQ", "alpaca_QQQ", ["lighter_QQQ", "arcus_QQQ"]),
         ("XAU", "binance_XAUUSDT_perp", ["lighter_XAU", "binance_PAXGUSDT", "duka_XAUUSD"])]


def make(sym: str, tf: int):
    return {"SPY": lambda: OpeningRange("SPY", tf), "QQQ": lambda: EmaPullback("QQQ", tf),
            "XAU": lambda: LondonSweep(tf=tf)}[sym]()


def tracking(ref: B.Bars, other: B.Bars, tz, s0: int, s1: int) -> tuple[float, float, int]:
    """Median distance between the two closes (bp) and the correlation of 5-minute returns, in the session hours."""
    common, ia, ib = np.intersect1d(ref.t, other.t, return_indices=True)
    sod = B.local(common, tz) % B.DAY
    keep = (sod >= s0) & (sod < s1)
    a, b = ref.c[ia][keep], other.c[ib][keep]
    if len(a) < 100:
        return float("nan"), float("nan"), len(a)
    gap = float(np.median(np.abs(b / a - 1)) * 1e4)
    ra, rb = np.diff(np.log(a[::5])), np.diff(np.log(b[::5]))
    ok = np.abs(ra) < 0.02
    return gap, float(np.corrcoef(ra[ok], rb[ok])[0, 1]), len(a)


def main() -> None:
    out = ["# The strategies on the venues' own prices", ""]
    for sym, ref_name, others in PAIRS:
        ref = B.load(ref_name)
        m = MARKETS[sym]
        venue = m.venues["lighter"]
        s0, s1 = ((9 * 3600 + 1800, 16 * 3600) if sym != "XAU" else (0, 20 * 3600))
        for other_name in others:
            other = B.load(other_name)
            if not len(other) or not len(ref):
                out.append(f"- {sym}: {other_name} has no data")
                continue
            start = max(int(other.t[0]), int(ref.t[0])) + 2 * B.DAY
            end = min(int(other.t[-1]), int(ref.t[-1])) - 60
            gap, corr, n = tracking(ref.between(start, end), other.between(start, end), m.tz, s0, s1)
            out.append(f"## {sym}: {ref_name} against {other_name}, {B.day_str(start // 86400)} to "
                       f"{B.day_str(end // 86400)}")
            out.append(f"- prices in the strategy's hours: {n:,} common minutes, median gap {gap:.1f} bp, "
                       f"5-minute moves correlate {corr:.3f}")
            for tf in (1, 5, 15):
                s = make(sym, tf)
                ta = [x for x in run(s, s.days(ref.between(start - 30 * B.DAY, end)), venue, m.tick)
                      if start <= x.t_entry < end]
                s = make(sym, tf)
                tb = [x for x in run(s, s.days(other.between(start - 30 * B.DAY, end)), venue, m.tick)
                      if start <= x.t_entry < end]
                keys_b = {(x.day, x.side): x for x in tb}
                match = sum(1 for x in ta if (x.day, x.side) in keys_b
                            and abs(keys_b[(x.day, x.side)].t_entry - x.t_entry) <= 120)
                out.append(f"- {tf}m: reference {len(ta)} trades ({sum(x.r for x in ta):+.1f}R), venue {len(tb)} "
                           f"({sum(x.r for x in tb):+.1f}R), {match} the same trade")
            out.append("")
    text = "\n".join(out)
    (REPORTS / "research").mkdir(parents=True, exist_ok=True)
    (REPORTS / "research" / "CHECK.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
