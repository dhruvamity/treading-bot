"""deltaneutral: two markets held delta-neutral across Arcus and Lighter, renewed every 24 hours (README.md).

    deltaneutral fetch [SYM ...] [--days 35]      both venues' public prices and funding into data/
    deltaneutral costs [SYM ...]                  what a fill costs on each venue -> data/costs.json
    deltaneutral research [--days 30]             the markets, every pair, the 30-day replay -> reports/REPORT.md
    deltaneutral backtest [X Y] [--days 30]       one pair's replay, cycle by cycle (options: --help)
    deltaneutral run [--live] [--seconds N]       the bot in this terminal (paper unless --live)
    deltaneutral start [--live] | stop [--live]   the bot in the background (tbot up deltaneutral: paper)
    deltaneutral transfer [--live]                the last transfer asked for: how much, from which venue to which
    deltaneutral status [--live]                  what it holds, the last cycle, the transfer to make
    deltaneutral cycles [--live] [-n 10]          the last cycles
    deltaneutral settings | set NAME VALUE | pair X Y
    deltaneutral skip [YYYY-MM-DD ...] [--clear]  earnings dates: nothing is held over them
    deltaneutral close | pause | resume [--live]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import sys
import time

from . import REPORTS, STATE, TOKENS
from . import backtest as BT
from . import costs as C
from . import engine as E
from . import plan as P
from .plan import ARCUS, LIGHTER, VENUES, Pair
from .series import hhmm


def _utc(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%a %d %b %H:%M UTC")


def _usd(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


# ------------------------------------------------------------------------------------------------ data and research
def c_fetch(a: argparse.Namespace) -> None:
    from . import data

    data.fetch([s.upper() for s in a.symbols] or None, days=a.days, parts=tuple(a.parts))


def c_costs(a: argparse.Namespace) -> None:
    from . import DATA
    from .series import cash_session

    syms = [s.upper() for s in a.symbols] or list(TOKENS)
    m = json.loads((DATA / "markets.json").read_text())
    ids = {s: int(m["lighter"][s]["market_id"]) for s in syms if m["lighter"].get(s)}
    c = C.measure(syms, ids, snapshots=a.snapshots)
    sess = cash_session(time.time())
    print(f"measured now ({sess}): bp from the mid to take $500 / $1,000 at once, before fees")
    print(f"{'market':8} {'Arcus':>14} {'Lighter':>14}")
    for s in syms:
        a_ = (c.take_bp(ARCUS, s, sess, 500), c.take_bp(ARCUS, s, sess, 1000))
        l_ = (c.take_bp(LIGHTER, s, sess, 500), c.take_bp(LIGHTER, s, sess, 1000))
        print(f"{s:8} {a_[0]:6.2f} / {a_[1]:5.2f} {l_[0]:6.2f} / {l_[1]:5.2f}")


def c_research(a: argparse.Namespace) -> None:
    from . import research

    text = research.report(days=a.days)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "REPORT.md").write_text(text)
    print(text)


def backtest_settings(a: argparse.Namespace) -> BT.Settings:
    return BT.Settings(open_ny=a.hour, hold_h=a.hold, margin_use=a.margin, max_leverage=a.lev,
                       ratio="beta" if a.beta else "equal", way=a.way, offhours="cut" if a.cut else "keep",
                       guard=a.guard, arcus_maker=not a.taker, maker_bp=a.maker_bp, maker_fill=a.maker_fill,
                       transfers="never" if a.no_transfers else "each", weekends=not a.weekdays)


def c_backtest(a: argparse.Namespace) -> None:
    from . import research
    from . import series as S

    s = E.Settings.load(_settings_path())
    x, y = (a.pair[0].upper(), a.pair[1].upper()) if len(a.pair) == 2 else (s.x, s.y)
    start, end = research.window(a.days)
    mks = research.load_all([x, y], start, end)
    st = backtest_settings(a)
    r = BT.run(Pair(x, y), mks[x], mks[y], C.load(scale=a.cost_scale), st, start, end,
               {ARCUS: a.arcus, LIGHTER: a.lighter})
    parts = r.parts()
    print(f"{x}/{y}, {r.days:.0f} days from {_utc(r.start)}: ${sum(r.start_equity.values()):,.2f} became "
          f"{_usd(r.total)} ({r.pnl / sum(r.start_equity.values()) * 100:+.1f}%)")
    print(f"  the two venues' prices {_usd(parts['price'])}, funding {_usd(parts['funding'])}, fills "
          f"{_usd(parts['costs'])}")
    print(f"  {len(r.cycles)} cycles, {r.liquidations} liquidations, {r.guards} early closes; traded "
          f"${r.volume:,.0f}; open interest held ${r.avg_oi:,.0f} on average (both venues, both markets)")
    print(f"  {len(r.transfers)} transfers, ${sum(t[1] for t in r.transfers):,.2f} moved in all, the largest "
          f"${max((t[1] for t in r.transfers), default=0):,.2f}")
    if r.volume:
        print(f"  cost per $1M traded: ${-parts['costs'] / r.volume * 1e6:,.0f}")
    if a.cycles:
        print(f"\n{'opened':22} {'way':>3} {'X $':>8} {'Y $':>8} {'Arcus':>8} {'Lighter':>8} {'price':>7} "
              f"{'funding':>8} {'fills':>7} {'room':>5}  move")
        for c in r.cycles:
            made = {v: c.end_equity[v] - c.start_equity[v] + (c.transfer if c.transfer_from == v else 0)
                    - (c.transfer if c.transfer_from and c.transfer_from != v else 0) for v in VENUES}
            mv = f"${c.transfer:,.2f} {c.transfer_from[:1].upper()}->" if c.transfer else ""
            print(f"{_utc(c.opened):22} {c.way:+3d} {c.nx:8,.0f} {c.ny:8,.0f} {made[ARCUS]:+8.2f} "
                  f"{made[LIGHTER]:+8.2f} {sum(c.price.values()):+7.2f} {sum(c.funding.values()):+8.2f} "
                  f"{sum(c.costs.values()):+7.2f} {c.min_room if math.isfinite(c.min_room) else 0:5.2f}  "
                  f"{c.end}{'  ' + mv if mv else ''}")
    _ = S


# ------------------------------------------------------------------------------------------------ the bot
def _settings_path():
    from .bot import settings_path

    return settings_path()


def _mode(a: argparse.Namespace) -> str:
    return "live" if getattr(a, "live", False) else "paper"


def c_run(a: argparse.Namespace) -> None:
    from . import bot
    from . import venues as V

    env = V.read_env()
    why = V.no_trading(env)
    if why:
        sys.exit(f"deltaneutral: {why}")
    if a.live:
        if env.get("DN_LIVE", "").strip() != "1":
            sys.exit("deltaneutral: live trading is off. It needs DN_LIVE=1 in arcus/.env (the owner's switch).")
        s = bot.settings()
        print(f"LIVE: real orders on Arcus and Lighter, pair {s.x}/{s.y}, each cycle {s.hold_h:g} h from "
              f"{hhmm(s.open_ny)} New York, margin use {s.margin_use:.0%}, leverage "
              f"{'the venues highest' if not s.max_leverage else f'at most {s.max_leverage:g}x'}"
              + (f", at most ${s.max_notional_usd:,.0f} a market" if s.max_notional_usd else ""))
        if not a.yes and (not sys.stdin.isatty() or input("Type LIVE to start: ").strip() != "LIVE"):
            sys.exit("not started")
    coll = {ARCUS: a.arcus, LIGHTER: a.lighter}
    asyncio.run(bot.run(_mode(a), seconds=a.seconds, collateral=coll, now=a.now))


def c_start(a: argparse.Namespace) -> None:
    from . import bot, ops
    from . import venues as V

    if a.live:
        env = V.read_env()
        if env.get("DN_LIVE", "").strip() != "1":
            sys.exit("deltaneutral: live trading is off. It needs DN_LIVE=1 in arcus/.env (the owner's switch).")
        s = bot.settings()
        print(f"LIVE in the background: real orders on Arcus and Lighter, pair {s.x}/{s.y}")
        if not sys.stdin.isatty() or input("Type LIVE to start: ").strip() != "LIVE":
            sys.exit("not started")
    print(ops.start("live" if a.live else "paper", {ARCUS: a.arcus, LIGHTER: a.lighter}, now=a.now)[1])


def c_stop(a: argparse.Namespace) -> None:
    from . import ops

    print(ops.stop(_mode(a))[1])


def c_transfer(a: argparse.Namespace) -> None:
    from . import telegram

    print(telegram.transfer_text(_mode(a)))


def status_text(mode: str) -> str:
    from . import bot

    d = bot.status(mode)
    st, s = d["state"], d["settings"]
    lines = [f"{mode.upper()} · pair {s.x}/{s.y} · a cycle every {s.hold_h:g} h from {hhmm(s.open_ny)} New York"]
    if st is None:
        lines.append("Not started yet (deltaneutral run" + (" --live" if mode == "live" else "") + ").")
        return "\n".join(lines)
    if st.phase == "flat":
        lines.append(f"Flat. Next cycle opens {_utc(st.next_open) if st.next_open else 'at the next open time'}"
                     + (" (paused)" if st.paused else ""))
    else:
        lines.append(f"Cycle {st.cycle} {st.phase}: {st.x}/{st.y}, opened {_utc(st.opened_at)}, closes "
                     f"{_utc(st.close_at)}" + (f" ({st.why})" if st.why else ""))
        for k, p in sorted(st.last_pos.items()):
            lines.append(f"  {k}: {p:+g}")
    if st.transfer:
        tr = P.Transfer(st.transfer.get("amount", 0.0), st.transfer.get("from", ""), st.transfer.get("to", ""), 0.0)
        lines.append("Last transfer asked for: " + (f"${tr.amount:,.2f} from {tr.source.title()} to "
                                                     f"{tr.target.title()}" if tr.amount else "none"))
    cyc = d["cycles"]
    if cyc:
        made = sum(sum(c["made"].values()) for c in cyc)
        vol = sum(c.get("volume", 0) for c in cyc)
        last = cyc[-1]
        lines.append(f"Last cycle ({last['cycle']}): {_usd(sum(last['made'].values()))}; the last {len(cyc)} cycles "
                     f"{_usd(made)}, ${vol:,.0f} traded")
    ev = d["events"][-4:]
    if ev:
        lines.append("Recent:")
        lines += [f"  {time.strftime('%d %b %H:%M', time.gmtime(e['ts']))} {e['text'][:220]}" for e in ev]
    return "\n".join(lines)


def c_status(a: argparse.Namespace) -> None:
    print(status_text(_mode(a)))


def cycles_text(mode: str, n: int = 10) -> str:
    from . import bot

    rows = bot.status(mode)["cycles"][-n:]
    if not rows:
        return "No cycle has closed yet."
    out = [f"{'#':>3} {'opened':22} {'pair':10} {'Arcus':>8} {'Lighter':>8} {'together':>9} {'traded':>9}  transfer"]
    for c in rows:
        m = c["made"]
        tr = c.get("transfer") or {}
        move = f"${tr['amount']:,.2f} {tr['from']}->{tr['to']}" if tr.get("amount") else "-"
        out.append(f"{c['cycle']:>3} {_utc(c['opened']):22} {c['pair']:10} {m['arcus']:+8.2f} {m['lighter']:+8.2f} "
                   f"{m['arcus'] + m['lighter']:+9.2f} {c.get('volume', 0):9,.0f}  {move}")
    return "\n".join(out)


def c_cycles(a: argparse.Namespace) -> None:
    print(cycles_text(_mode(a), a.n))


def settings_text() -> str:
    s = E.Settings.load(_settings_path())
    return "\n".join(f"{k:18} {getattr(s, k)}" for k in s.__dataclass_fields__)


def c_settings(a: argparse.Namespace) -> None:
    print(settings_text())


def c_set(a: argparse.Namespace) -> None:
    try:
        v = E.set_value(_settings_path(), a.name, a.value)
    except ValueError as e:
        sys.exit(str(e))
    print(f"{a.name} = {v} (the running bot reads it at its next step)")


def c_pair(a: argparse.Namespace) -> None:
    try:
        x, y = E.set_pair(_settings_path(), a.x, a.y)
    except ValueError as e:
        sys.exit(str(e))
    print(f"pair = {x}/{y} (from the next cycle; an open one keeps its pair)")


def skip_text(days: list[str], clear: bool = False) -> str:
    if days or clear:
        try:
            have = E.set_skip(_settings_path(), days, clear)
        except ValueError as e:
            return str(e)
    else:
        have = E.Settings.load(_settings_path()).skip
    if not have:
        return "No skip dates. Add a market's earnings date: deltaneutral skip 2026-10-22"
    return ("Skip dates (nothing is held from 16:00 New York the day before to 16:00 the day after): "
            + ", ".join(have))


def c_skip(a: argparse.Namespace) -> None:
    print(skip_text(a.days, a.clear))


def c_control(a: argparse.Namespace) -> None:
    from . import bot

    bot.store(_mode(a), echo=None).send(**{a.cmd: True})
    print(f"{a.cmd}: sent to the {_mode(a)} bot (it acts at its next step)")


# ------------------------------------------------------------------------------------------------ parser
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deltaneutral", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download both venues' public prices and funding")
    f.add_argument("symbols", nargs="*")
    f.add_argument("--days", type=float, default=35)
    f.add_argument("--parts", nargs="+", default=["lighter", "arcus", "trades"])
    f.set_defaults(fn=c_fetch)
    c = sub.add_parser("costs", help="measure what a fill costs on each venue now (both live books)")
    c.add_argument("symbols", nargs="*")
    c.add_argument("--snapshots", type=int, default=5)
    c.set_defaults(fn=c_costs)
    r = sub.add_parser("research", help="the markets, every pair and the 30-day replay -> reports/REPORT.md")
    r.add_argument("--days", type=int, default=30)
    r.set_defaults(fn=c_research)
    b = sub.add_parser("backtest", help="one pair's replay")
    b.add_argument("pair", nargs="*", help="X Y (default: the bot's pair)")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--arcus", type=float, default=100.0, help="starting money on Arcus")
    b.add_argument("--lighter", type=float, default=100.0, help="starting money on Lighter")
    b.add_argument("--hour", type=float, default=10.0, help="the daily open, New York time (10.5 = 10:30)")
    b.add_argument("--hold", type=float, default=24.0, help="hours a cycle is held")
    b.add_argument("--margin", type=float, default=0.9, help="share of the money used as initial margin")
    b.add_argument("--lev", type=float, default=0.0, help="leverage cap (0 = the venues' highest)")
    b.add_argument("--beta", action="store_true", help="Y's dollars = beta x X's (default: the same dollars)")
    b.add_argument("--way", default="auto", choices=["auto", "+1", "-1"])
    b.add_argument("--cut", action="store_true", help="cut to Arcus's off-hours size at the stock market's close")
    b.add_argument("--guard", type=float, default=0.25, help="close early under this room (0 = never)")
    b.add_argument("--taker", action="store_true", help="Arcus's legs take (default: they rest limit orders)")
    b.add_argument("--maker-bp", type=float, default=0.5, help="what a resting Arcus fill costs (bp)")
    b.add_argument("--maker-fill", type=float, default=-1.0,
                   help="share of resting Arcus orders that fill in time (default: measured)")
    b.add_argument("--cost-scale", type=float, default=1.0, help="every fill cost times this")
    b.add_argument("--no-transfers", action="store_true", help="never move money between the venues")
    b.add_argument("--weekdays", action="store_true", help="open Monday to Friday only")
    b.add_argument("--cycles", action="store_true", help="print every cycle")
    b.set_defaults(fn=c_backtest)
    rn = sub.add_parser("run", help="the bot (paper unless --live)")
    rn.add_argument("--live", action="store_true")
    rn.add_argument("--seconds", type=float, default=None)
    rn.add_argument("--arcus", type=float, default=100.0, help="paper: pretend money on Arcus")
    rn.add_argument("--lighter", type=float, default=100.0, help="paper: pretend money on Lighter")
    rn.add_argument("--yes", action="store_true", help="--live without the typed LIVE (the background service)")
    rn.add_argument("--now", action="store_true", help="the first cycle opens at once, not at the next open time")
    rn.set_defaults(fn=c_run)
    sa = sub.add_parser("start", help="the bot in the background (paper unless --live)")
    sa.add_argument("--live", action="store_true")
    sa.add_argument("--arcus", type=float, default=100.0, help="paper: pretend money on Arcus")
    sa.add_argument("--lighter", type=float, default=100.0, help="paper: pretend money on Lighter")
    sa.add_argument("--now", action="store_true", help="the first cycle opens at once, not at the next open time")
    sa.set_defaults(fn=c_start)
    so = sub.add_parser("stop", help="stop the background bot (an open cycle stays open)")
    so.add_argument("--live", action="store_true")
    so.set_defaults(fn=c_stop)
    tr = sub.add_parser("transfer", help="the last transfer asked for")
    tr.add_argument("--live", action="store_true")
    tr.set_defaults(fn=c_transfer)
    for name, fn, h in (("status", c_status, "what it holds now"), ("cycles", c_cycles, "the last cycles")):
        x = sub.add_parser(name, help=h)
        x.add_argument("--live", action="store_true")
        if name == "cycles":
            x.add_argument("-n", type=int, default=10)
        x.set_defaults(fn=fn)
    sub.add_parser("settings", help="the settings").set_defaults(fn=c_settings)
    se = sub.add_parser("set", help="change one setting")
    se.add_argument("name")
    se.add_argument("value")
    se.set_defaults(fn=c_set)
    pa = sub.add_parser("pair", help="the two markets")
    pa.add_argument("x")
    pa.add_argument("y")
    pa.set_defaults(fn=c_pair)
    sk = sub.add_parser("skip", help="earnings dates of either market: nothing is held over them")
    sk.add_argument("days", nargs="*")
    sk.add_argument("--clear", action="store_true", help="remove every date first")
    sk.set_defaults(fn=c_skip)
    for name in ("close", "pause", "resume"):
        x = sub.add_parser(name, help=f"{name} the running bot")
        x.add_argument("--live", action="store_true")
        x.set_defaults(fn=c_control, cmd=name)
    return p


def main(argv: list[str] | None = None) -> None:
    a = parser().parse_args(argv)
    STATE.mkdir(parents=True, exist_ok=True)
    a.fn(a)


if __name__ == "__main__":
    main()
