"""daytrade: the SPY, QQQ and gold day-trading strategies. Paper only.

    daytrade fetch [alpaca] [gold] [lighter] [arcus] [duka]   download one-minute history (public data)
    daytrade backtest spy|qqq|xau [--tf 5] [--venue lighter] [--data NAME] [--since 2025-10-15]
    daytrade research [spy] [qqq] [xau]                       every version, every timeframe, both venues
    daytrade check                                            the strategies on the venues' own prices vs the reference
    daytrade run [--tf 5] [--seconds N] [--no-swing]          the paper auto-trader (a server job), day and daily rules
    daytrade status                                           what it holds now and its paper results
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import bars as B
from .core import MARKETS


def strategy(name: str, tf: int):
    from .strategies import EmaPullback, LondonSweep, OpeningRange

    return {"spy": lambda: OpeningRange("SPY", tf), "qqq": lambda: EmaPullback("QQQ", tf),
            "xau": lambda: LondonSweep(tf=tf)}[name]()


def cmd_backtest(a: argparse.Namespace) -> None:
    from .research import months_between, stats
    from .sim import run

    s = strategy(a.market, a.tf)
    m = MARKETS[s.market]
    data = a.data or m.data
    b = B.load(data)
    if not len(b):
        raise SystemExit(f"no data in data/bars/{data}.npz: run `daytrade fetch` first")
    start = int(dt.datetime.fromisoformat(a.since).replace(tzinfo=dt.UTC).timestamp()) if a.since else int(b.t[0])
    v = m.venues[a.venue]
    trades = [x for x in run(s, s.days(b), v, m.tick) if x.t_entry >= start]
    st = stats(trades, v.max_lev, months_between(start, int(b.t[-1])))
    print(f"{s.name} on {data}, {a.venue} costs, from {B.day_str(start // 86400)}")
    print(f"  trades {st.n}, won {st.win:.0%}, average {st.avg_r:+.3f}R after costs ({st.gross_r:+.3f}R before), "
          f"total {st.total_r:+.1f}R, t {st.t:+.2f}")
    print(f"  $1,000 risking 1% a trade -> ${st.usd:,.0f} (worst fall {st.usd_dd:.0f}%)")
    if a.trades:
        for x in trades[-a.trades:]:
            print(f"  {B.day_str(x.day)} {x.tag:<11} in {x.entry:.2f} stop {x.stop0:.2f} out {x.exit:.2f} "
                  f"{x.reason:<7} {x.r:+.2f}R")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="daytrade", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("what", nargs="*")
    p = sub.add_parser("backtest")
    p.add_argument("market", choices=["spy", "qqq", "xau"])
    p.add_argument("--tf", type=int, default=5, choices=[1, 5, 15])
    p.add_argument("--venue", default="lighter", choices=["lighter", "arcus"])
    p.add_argument("--data", default="")
    p.add_argument("--since", default="")
    p.add_argument("--trades", type=int, default=0, help="print the last N trades")
    p = sub.add_parser("research")
    p.add_argument("what", nargs="*")
    sub.add_parser("check")
    p = sub.add_parser("run")
    p.add_argument("--tf", type=int, default=5, choices=[1, 5, 15])
    p.add_argument("--seconds", type=float, default=None)
    p.add_argument("--no-swing", action="store_true",
                   help="leave out the daily SPY/QQQ rules and the hourly gold rule (swing_bot.py)")
    sub.add_parser("status")
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        from . import fetch

        fetch.main(a.what)
    elif a.cmd == "backtest":
        cmd_backtest(a)
    elif a.cmd == "research":
        from . import research

        research.main(a.what)
    elif a.cmd == "check":
        from . import check

        check.main()
    elif a.cmd == "run":
        from .bot import Runner, default_slots
        from .swing_bot import default_markets

        Runner(default_slots(a.tf), swing=[] if a.no_swing else default_markets()).run(a.seconds)
    elif a.cmd == "status":
        from .bot import summary

        print(summary())


if __name__ == "__main__":
    main(sys.argv[1:])
