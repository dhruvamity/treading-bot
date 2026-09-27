"""Research sweeps behind docs/RESEARCH.md: every market x setup (or parameter variant) on the recorded days,
steady state (no daily stop, no kill: the cost over whole days), at a capital and each market's maximum leverage.

    .venv/bin/python scripts/research.py --markets BTC,SPY --setups "mid 0,touch 0" [--capital 100] [--pos-stop 3]
        [--param kappa=0,1] [--cfg maker_us=0,280000]

Prints one row per market x setup x variant: volume/day, PnL/day, cost (bp of volume), maker edge and markouts.
"""

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lbot.scout.sim import MarketRules, Sim, SimCfg, Window  # noqa: E402
from lbot.scout.tape import Tape, day_start  # noqa: E402
from lbot.trade.sizing import Stops, sizes  # noqa: E402
from lbot.trade.strategy import parse  # noqa: E402
from lbot.venue.market import Market  # noqa: E402

US = 1_000_000


def markets() -> dict[str, Market]:
    p = ROOT / "data" / "markets.json"
    return {k: Market.from_dict(v) for k, v in json.loads(p.read_text()).items()}


def _num(x: str) -> float | int:
    try:
        return int(x)
    except ValueError:
        return float(x)


def run_one(job: tuple) -> dict:
    m, day, setup_text, pvar, cvar, capital, pos_stop, lev, margin = job
    mk = markets()[m]
    tape = Tape(ROOT / "data" / "tape")
    t = tape.load_day(m, day)
    cfg = replace(SimCfg(), **cvar)   # the bot's defaults: 0.5 s loop, 54 requotes a minute
    w = Window(t, day_start(day), day_start(day) + 86_400 * US, cfg)
    s = parse(setup_text)
    p = s.params(**pvar)
    L = min(lev or mk.max_leverage, mk.max_leverage)
    sz = sizes(capital, L, Stops(pos_stop, 1e9, 1e9))
    if margin:
        sz = replace(sz, capital=margin)   # sizes from `capital`, but a large balance: no liquidation
    mr = MarketRules(mk.tick, mk.step, mk.min_base, mk.min_quote, mk.mmf_frac)
    r = Sim(p, sz, mr, cfg, s.name).run(w)
    scale = 24 / r.hours if r.hours else 0
    return {"m": m, "day": day, "setup": s.name, "p": pvar, "c": cvar, "lev": L, "hours": round(r.hours, 1),
            "vol_d": r.volume * scale, "pnl_d": r.pnl * scale, "cost": r.cost_bps, "edge": r.edge_bps,
            "mo": r.markout_bps, "fills": r.maker_fills, "taker": r.taker_fills, "ps": r.pos_stops,
            "liq": r.liquidated, "req": r.requests, "skip": r.skipped, "rej": r.rejects,
            "quote_h": r.quoting_s / 3600, "fund": r.funding, "maxpos": r.max_pos_usd,
            "hourly_usd": r.hourly_usd, "hourly_pnl": r.hourly_pnl}


def variants(spec: list[str]) -> list[dict]:
    if not spec:
        return [{}]
    keys, vals = [], []
    for s in spec:
        k, v = s.split("=", 1)
        keys.append(k)
        vals.append([_num(x) for x in v.split(",")])
    return [dict(zip(keys, c, strict=True)) for c in itertools.product(*vals)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="BTC,ETH,SOL,HYPE,SPY,QQQ,NVDA,TSLA")
    ap.add_argument("--days", default="")
    ap.add_argument("--setups", default="mid 0")
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--cfg", action="append", default=[])
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--pos-stop", type=float, default=3.0)
    ap.add_argument("--lev", type=float, default=0.0)
    ap.add_argument("--margin", type=float, default=0.0, help="a balance this large for margin (0: the capital)")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    tape = Tape(ROOT / "data" / "tape")
    jobs = []
    for m in a.markets.split(","):
        days = a.days.split(",") if a.days else tape.days(m)
        for d in days:
            for s in [x.strip() for x in a.setups.split(",") if x.strip()]:
                for pv in variants(a.param):
                    for cv in variants(a.cfg):
                        jobs.append((m, d, s, pv, cv, a.capital, a.pos_stop, a.lev, a.margin))
    with mp.Pool(a.workers) as pool:
        rows = pool.map(run_one, jobs)
    if a.json:
        Path(a.json).write_text(json.dumps(rows))
    for r in rows:
        mo = " ".join(f"{k}={v:+.2f}" for k, v in r["mo"].items())
        extra = " ".join(f"{k}={v}" for k, v in {**r["p"], **r["c"]}.items())
        print(f"{r['m']:5} {r['day']} {r['setup']:15} {extra:24} {r['lev']:>3.0f}x vol/d=${r['vol_d']:>12,.0f} "
              f"pnl/d=${r['pnl_d']:>8.2f} cost={r['cost']:+6.2f}bp edge={r['edge']:+5.2f} {mo} fills={r['fills']:>5} "
              f"tk={r['taker']:>3} ps={r['ps']:>3} liq={int(r['liq'])} req={r['req']:>5} skip={r['skip']:>4} "
              f"q={r['quote_h']:.1f}h")


if __name__ == "__main__":
    main()
