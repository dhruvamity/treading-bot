"""`arbitrage`: funding arbitrage between Arcus and Lighter (Robinhood Chain).

    arbitrage scan [--top 10] [--arcus 120 --lighter 120] [--feeds]    rank the markets both venues list
    arbitrage plan SPY [--arcus 120 --lighter 120]                     one market's plan: sizes, stops, what it pays
    arbitrage paper open SPY | status | close SPY                      a paper record of holding it
    arbitrage feeds                                                    ProFunding's and arb.sh's view, as a cross-check
    arbitrage history                                                  download both venues' funding and price history
    arbitrage backtest [--capital 240] [--symbols SPY ...]             the rules replayed on all of it
    arbitrage settings | set max_hold_h 72                             see or change a setting
    arbitrage run [--arcus 120 --lighter 120] [--seconds N]            the executor on PAPER (simulated orders)
    arbitrage run --live                                               real orders: ARB_LIVE=1, then type LIVE
    arbitrage start [--arcus 120 --lighter 120] | start --live | stop  the same executor in the background
    arbitrage status [--live] | close [--now] | pause | resume         the running bot (add --live for the live one)
    arbitrage skip CASHCAT | unskip CASHCAT                            markets it must not open
    arbitrage livetest [SYMBOL] [--what all|lighter|arcus|engine]      REAL MONEY, smallest size: legs, then engine

In the one Telegram bot the same commands are `/arb_<command>`.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from zoneinfo import ZoneInfo

from arbitrage import feeds, paper
from arbitrage import scan as scanner
from arbitrage.config import ADJUSTABLE, ROOT, STATE, Config, load, set_value
from arbitrage.rank import Plan
from arbitrage.venues import Arcus, Lighter

IST = ZoneInfo("Asia/Kolkata")
NAMES = {"arcus": "Arcus", "lighter": "Lighter"}


def apr(rate_h: float) -> str:
    return f"{rate_h * 8760 * 100:+.1f}%"


def when(ts: int) -> str:
    if not ts:
        return "?"
    mins = max(0, round((ts - time.time()) / 60))
    return f"{dt.datetime.fromtimestamp(ts, IST):%H:%M} IST (in {mins} min)"


def collateral_arg(a: argparse.Namespace) -> dict[str, float] | None:
    if a.arcus is None and a.lighter is None:
        return None
    return {"arcus": a.arcus or 0.0, "lighter": a.lighter or 0.0}


def line(p: Plan) -> str:
    head = (f"{p.symbol:8} short {NAMES[p.short_venue]:7} long {NAMES[p.long_venue]:7} "
            f"next {apr(p.edge_next_h):>7}  24h {apr(p.edge_24h):>7}  7d {apr(p.edge_7d):>7}  "
            f"{p.leverage:4.1f}x ${p.notional:>7,.0f}  ")
    if p.breakeven_h > 1e6:
        return head + "no steady difference"
    return head + f"${p.income_day:5.2f}/day  pays back in {p.breakeven_h:5.1f} h"


def detail(p: Plan, cfg: Config) -> str:
    s = cfg.settings
    out = [f"{p.symbol}: short on {NAMES[p.short_venue]}, long on {NAMES[p.long_venue]}  "
           f"({'GO' if p.go else 'NOT NOW'})",
           f"  difference a year on the position: next payment {apr(p.edge_next_h)}, last 24 h "
           f"{apr(p.edge_24h)}, last 7 days {apr(p.edge_7d)}; counted on: {apr(p.edge_h)}",
           f"  position ${p.notional:,.0f} a leg ({p.size:g} {p.symbol}) at {p.leverage:.1f}x",
           f"  pays about ${p.income_day:.2f} a day; next payment about ${p.next_payment_usd:+.3f} at "
           f"{when(p.next_payment_at)}",
           f"  getting in and out costs about ${p.round_trip_usd:.2f} (4 fills"
           + (f"; spreads now Arcus {p.spreads_bp['arcus']:.1f} bp, Lighter {p.spreads_bp['lighter']:.1f} bp"
              if p.spreads_bp else f" at {s.fill_cost_bp:g} bp") + "): "
           + (f"{p.breakeven_h:.0f} h of funding" if p.breakeven_h < 1e6 else "never paid back"),
           f"  stop and take profit {p.stop_dist * 100:.2f}% from the entry on both legs "
           f"(liquidation {p.liq_dist * 100:.2f}% away):"]
    for venue, x in p.prices.items():
        side = "long " if x["side"] > 0 else "short"
        out.append(f"    {NAMES[venue]:7} {side} entry {x['entry']:,.4g}  stop {x['stop']:,.6g}  "
                   f"take profit {x['take']:,.6g}  liquidation {x['liq']:,.6g}")
    out += [f"  ! {r}" for r in p.reasons]
    return "\n".join(out)


async def cmd_scan(a: argparse.Namespace, cfg: Config) -> int:
    res = await scanner.run(cfg, top=a.top, collateral=collateral_arg(a))
    if a.json:
        print(json.dumps({"ts": res.ts, "collateral": res.collateral, "plans": [asdict(p) for p in res.plans]}))
        return 0
    c = res.collateral
    print(f"{res.common} markets on both venues · free collateral: Arcus ${c['arcus']:,.2f}, "
          f"Lighter ${c['lighter']:,.2f} "
          f"· {dt.datetime.fromtimestamp(res.ts, IST):%d %b %H:%M} IST")
    go = [p for p in res.plans if p.go]
    print(f"\nWorth holding now: {len(go)}")
    for p in go:
        print("  " + line(p))
    print("\nNot now:")
    for p in res.plans:
        if not p.go:
            print("  " + line(p))
            print(f"      {p.reasons[0]}")
    if a.feeds:
        print()
        await cmd_feeds(a, cfg, res)
    return 0


async def cmd_plan(a: argparse.Namespace, cfg: Config) -> int:
    res = await scanner.run(cfg, symbols=[a.symbol], collateral=collateral_arg(a))
    if not res.plans:
        print(f"{a.symbol.upper()} is not listed on both venues")
        return 1
    print(detail(res.plans[0], cfg))
    return 0


async def cmd_feeds(a: argparse.Namespace, cfg: Config, res: scanner.Scan | None = None) -> int:
    try:
        pf = await feeds.profunding(cfg.profunding_key, STATE / "profunding.json")
    except Exception as e:  # a feed being down must not hide the venues' own numbers
        pf = {}
        print(f"ProFunding: {e}")
    print(f"ProFunding lists {len(pf)} Arcus <-> Lighter pairs" + ("" if cfg.profunding_key else " (no key set)"))
    ours = {p.symbol: p for p in res.plans} if res else {}
    for sym, o in sorted(pf.items(), key=lambda kv: -kv[1]["net_apr"])[:12]:
        mine = ours.get(sym)
        same = "" if mine is None else ("  same side as the venues" if mine.short_venue == o["short"]
                                        else "  OPPOSITE side to the venues' own rates")
        print(f"  {sym:8} short {NAMES[o['short']]:7} long {NAMES[o['long']]:7} net {o['net_apr']:6.1f}% a year"
              + (f" · venues' next payment {apr(mine.edge_next_h)}" if mine else "") + same)
    try:
        ab = await feeds.arbsh()
        age = (time.time() - ab["updated"] / 1000) / 60 if ab["updated"] else float("nan")
        print(f"arb.sh: refreshed {age:.0f} min ago; its top {ab['listed']} spreads include "
              f"{len(ab['pairs'])} Arcus <-> Lighter pairs" + (": " + ", ".join(p[0].upper() for p in ab["pairs"])
                                                               if ab["pairs"] else ""))
    except Exception as e:
        print(f"arb.sh: {e}")
    return 0


Mids = dict[str, dict[str, float]]
Rates = dict[str, dict[str, dict[int, float]]]


async def mids_and_history(pos_syms: list[str]) -> tuple[Mids, Rates]:
    arcus, lighter = Arcus(), Lighter()
    hist = scanner.History(STATE)
    now = time.time()
    try:
        await lighter.legs()
        mids: Mids = {}
        hs: Rates = {}
        for s in pos_syms:
            (ab, aa), (lb, la) = await arcus.top(s), await lighter.top(s)
            mids[s] = {"arcus": (ab + aa) / 2, "lighter": (lb + la) / 2}
            hs[s] = {"arcus": await hist.fresh("arcus", s, arcus, now),
                     "lighter": await hist.fresh("lighter", s, lighter, now)}
        return mids, hs
    finally:
        await arcus.http.close()
        await lighter.http.close()


async def cmd_paper(a: argparse.Namespace, cfg: Config) -> int:
    book_path, log_path = STATE / "paper.json", STATE / "paper_history.jsonl"
    book = paper.load(book_path)
    now = time.time()
    if a.action == "open":
        sym = a.symbol.upper()
        if sym in book:
            print(f"{sym} is already open on paper")
            return 1
        res = await scanner.run(cfg, symbols=[sym], collateral=collateral_arg(a))
        if not res.plans or res.plans[0].size <= 0:
            print(f"{sym}: nothing to open (not on both venues, or no collateral: give --arcus and --lighter)")
            return 1
        p = res.plans[0]
        mids, _ = await mids_and_history([sym])
        book[sym] = paper.open_position(p, mids[sym], cfg.settings, now)
        paper.save(book_path, book)
        print(detail(p, cfg))
        print(f"\nOpened on paper at Arcus {mids[sym]['arcus']:,.6g}, Lighter {mids[sym]['lighter']:,.6g}"
              + ("" if p.go else "  (the scan says NOT NOW; recorded anyway)"))
        return 0
    if not book:
        print("no paper position open")
        return 0
    syms = [a.symbol.upper()] if a.action == "close" else sorted(book)
    if a.action == "close" and syms[0] not in book:
        print(f"{syms[0]} is not open on paper")
        return 1
    mids, hs = await mids_and_history(syms)
    for sym in syms:
        pos = book[sym]
        r = paper.mark(pos, mids[sym], hs[sym], now, closing=a.action == "close")
        print(f"{sym}: short {NAMES[pos.short_venue]}, long {NAMES[pos.long_venue]}, ${pos.notional:,.0f} a leg, "
              f"{r['hours']:.1f} h\n  funding ${r['funding']:+.3f} ({int(r['payments'])} payments) · price gap between "
              f"the venues ${r['basis']:+.3f} · fills ${-r['cost']:.3f} · net ${r['net']:+.3f}"
              + (f"\n  ! the price moved {r['move'] * 100:.2f}%: past the {pos.stop_dist * 100:.2f}% stop"
                 if r["stop_hit"] else ""))
        if a.action == "close":
            paper.log_close(log_path, pos, r, now)
            del book[sym]
    if a.action == "close":
        paper.save(book_path, book)
    return 0


def cmd_settings(a: argparse.Namespace, cfg: Config) -> int:
    if a.cmd == "set":
        try:
            v = set_value(a.name, a.value)
        except ValueError as e:
            print(e)
            return 1
        print(f"{a.name} = {v:g}  ({ADJUSTABLE[a.name][2]})\nA running bot uses it from its next loop.")
        return 0
    for name, (lo, hi, text) in ADJUSTABLE.items():
        print(f"{name:16} {getattr(cfg.settings, name):>10g}   {text}  [{lo:g} to {hi:g}]")
    return 0


def _mode(a: argparse.Namespace) -> str:
    return "live" if getattr(a, "live", False) else "paper"


def cmd_run(a: argparse.Namespace, cfg: Config) -> int:
    from arbitrage.config import no_trading, read_env
    from arbitrage.exec import run as runner

    mode = _mode(a)
    if no_trading():
        print(f"not started: {no_trading()}")
        return 1
    if mode == "live":
        if read_env().get("ARB_LIVE", "").strip() != "1":
            print("LIVE is off: put ARB_LIVE=1 in arcus/.env first (README.md, section 6). Nothing was sent.")
            return 1
        if not a.yes:
            print("This places REAL orders on Arcus and on Lighter with the keys in arcus/.env and lighter/.env.")
            if input("Type LIVE to go on: ").strip() != "LIVE":
                print("not started")
                return 1
    elif (a.arcus or 0) <= 0 or (a.lighter or 0) <= 0:
        print("paper needs the money to pretend with: arbitrage run --arcus 120 --lighter 120")
        return 1
    st = asyncio.run(runner.run(mode, seconds=a.seconds, collateral=collateral_arg(a)))
    print(f"stopped; phase {st.phase}" + (f", {st.symbol} {st.size:g} a leg" if st.phase != "flat" else ""))
    return 0


def cmd_livetest(a: argparse.Namespace, cfg: Config) -> int:
    """The live tests, once, on the real venues at the smallest size: the Lighter leg, the Arcus leg, then the
    executor itself on both (arbitrage/exec/legtest.py, drill.py). Real money: ARB_LIVE=1 and LIVE typed here once.
    A later part runs only if the earlier ones passed and ended flat."""
    from arbitrage import ops
    from arbitrage.config import STATE, no_trading, read_env
    from arbitrage.exec import drill, legtest
    from arbitrage.exec.arcus import ArcusTrade
    from arbitrage.exec.lighter import LighterTrade

    if no_trading():
        print(f"not started: {no_trading()}")
        return 1
    if ops.running("live"):
        print("not started: the live executor is running (arbitrage stop --live first): two programs must not trade "
              "one account")
        return 1
    if read_env().get("ARB_LIVE", "").strip() != "1":
        print("LIVE is off: put ARB_LIVE=1 in arcus/.env first (README.md, section 6). Nothing was sent.")
        return 1
    symbol = a.symbol.upper()
    parts = ("lighter", "arcus", "engine") if a.what == "all" else (a.what,)
    texts = {"lighter": legtest.plan_text(symbol, a.expiry, a.max_loss),
             "arcus": legtest.plan_text_arcus(symbol, a.max_loss), "engine": drill.plan_text(symbol, a.max_loss)}
    for part in parts:
        print(texts[part] + "\n")
    if not sys.stdin.isatty():
        print("not started: the live test needs you at the keyboard to type LIVE")
        return 1
    if input("Type LIVE to start: ").strip() != "LIVE":
        print("not started")
        return 1
    bad = 0
    for part in parts:
        print(f"\n=== {part} ===")
        if part == "lighter":
            rep = asyncio.run(legtest.LegTest(LighterTrade(STATE), symbol, max_loss=a.max_loss,
                                              expiry=a.expiry).run())
        elif part == "arcus":
            rep = asyncio.run(legtest.ArcusLegTest(ArcusTrade(ROOT.parent / "arcus"), symbol,
                                                   max_loss=a.max_loss).run())
            rep.title = "the Arcus leg"
        else:
            rep = asyncio.run(_run_drill(symbol, a.max_loss, a.hold, STATE))
        p = legtest.write_report(rep, ROOT / "reports", part)
        print("\n" + rep.text() + f"\nWritten to {p}")
        if not (rep.clean and not rep.failed and not rep.aborted):
            bad = 1
            if part != parts[-1]:
                print(f"\nStopped after {part}: fix what it shows before the next part sends anything.")
            break
    return bad


async def _run_drill(symbol: str, max_loss: float, hold: float, state: Path) -> object:
    from arbitrage.exec import drill, legtest
    from arbitrage.exec.arcus import ArcusTrade
    from arbitrage.exec.lighter import LighterTrade

    arcus, lighter = ArcusTrade(ROOT.parent / "arcus"), LighterTrade(state)
    # the reads that check the engine come from the same two adapters, through the leg tests' readers
    grounds = {"arcus": legtest.ArcusLegTest(arcus, symbol), "lighter": legtest.LegTest(lighter, symbol)}
    return await drill.Drill({"arcus": arcus, "lighter": lighter}, grounds, symbol, max_loss=max_loss,
                             hold_s=hold).run()


def cmd_service(a: argparse.Namespace, cfg: Config) -> int:
    """`arbitrage start`, `arbitrage stop`: the executor in the background (arbitrage/ops.py)."""
    from arbitrage import ops

    mode = _mode(a)
    if a.cmd == "stop":
        ok, msg = ops.stop(mode)
        print(msg)
        return 0
    if mode == "live" and not a.yes:
        print("This places REAL orders on Arcus and on Lighter with the keys in arcus/.env.")
        if input("Type LIVE to go on: ").strip() != "LIVE":
            print("not started")
            return 1
    ok, msg = ops.start(mode, collateral_arg(a))
    print(msg)
    return 0 if ok else 1


def cmd_control(a: argparse.Namespace, cfg: Config) -> int:
    from arbitrage import ops
    from arbitrage.exec.run import FileStore, skipped

    mode = _mode(a)
    store = FileStore(STATE, mode, echo=None)
    if a.cmd in ("skip", "unskip"):
        cur = skipped()
        cur = cur | {a.symbol.upper()} if a.cmd == "skip" else cur - {a.symbol.upper()}
        p = ROOT / "settings.json"
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            d = {}
        d["skip"] = sorted(cur)
        p.write_text(json.dumps(d, indent=1) + "\n")
        print("never opened: " + (", ".join(sorted(cur)) or "nothing skipped"))
        return 0
    if a.cmd == "close":
        store.send(close=True, now=bool(a.now))
        print(f"{mode}: close asked" + (" (taker orders, at once)" if a.now else " (maker orders first)")
              + "; the running bot does it on its next loop")
        return 0
    if a.cmd in ("pause", "resume"):
        store.send(pause=a.cmd == "pause")
        print(f"{mode}: " + ("no new position will be opened (an open one is kept)" if a.cmd == "pause"
                             else "looking for positions again"))
        return 0
    st = store.load()
    if st is None:
        print(f"{mode}: never run (no {store.position.name})")
        return 0
    age = time.time() - store.position.stat().st_mtime
    pid = ops.running(mode)
    print(f"{mode.upper()} · {st.phase}" + (" · paused" if st.paused else "")
          + f" · last written {age:.0f} s ago"
          + (f" · running (pid {pid})" if pid else "" if age < 120 else "  (the bot is not running)"))
    if st.phase != "flat":
        print(f"  {st.symbol}: long {NAMES[st.long_venue]}, short {NAMES[st.short_venue]}, {st.size:g} a leg at "
              f"{st.leverage:.1f}x")
        if st.entry:
            for venue, e in st.entry.items():
                side = 1.0 if venue == st.long_venue else -1.0
                placed = "placed" if st.stops_ok.get(venue) else "NOT placed"
                print(f"    {NAMES[venue]:7} entry {e:,.6g}  stop {e * (1 - side * st.stop_dist):,.6g}  take profit "
                      f"{e * (1 + side * st.stop_dist):,.6g}  venue's own stop {placed}")
        if st.opened_at:
            print(f"  held {(time.time() - st.opened_at) / 3600:.1f} h; planned "
                  f"${st.plan.get('income_day', 0):.2f} a day; min hold {cfg.settings.min_hold_h:g} h, max "
                  f"{cfg.settings.max_hold_h or 'none'}")
        if st.why:
            print(f"  closing: {st.why}")
    for ev in store.tail(8):
        print(f"  {dt.datetime.fromtimestamp(ev['ts'], IST):%d %b %H:%M} {ev['text']}")
    return 0


def cmd_backtest(a: argparse.Namespace, cfg: Config) -> int:
    from arbitrage import backtest as bt

    data = Path(a.data) if a.data else ROOT / "data" / "history"
    series = bt.load(data, [s.upper() for s in a.symbols] if a.symbols else None) if data.exists() else {}
    if not series:
        print(f"no history under {data}: `arbitrage history` downloads it (about an hour)")
        return 1
    st = cfg.settings
    first = min(s.hours[0] for s in series.values())
    last = max(s.hours[-1] for s in series.values())
    day = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%d %b %Y")   # noqa: E731
    print(f"{len(series)} markets, {day(first)} to {day(last)} ({(last - first) / 86400:.0f} days); "
          f"${a.capital:,.0f} in all, half on each venue; {st.fill_cost_bp:g} bp a fill; hold at least "
          f"{st.min_hold_h:g} h"
          + (f", at most {st.max_hold_h:g} h" if st.max_hold_h else ""))
    r = bt.run(series, a.capital, st)
    x = r.summary()
    print(f"\nOne position at a time, in the market that pays most:\n"
          f"  net ${x['net']:+.2f} over {x['days']:.0f} days = ${x['per_day']:+.3f} a day = "
          f"{x['apr_pct']:+.1f}% a year on the capital\n"
          f"  funding ${x['funding']:+.2f} · the venues' prices drifting ${x['price']:+.2f} · fills and fees "
          f"${-x['cost']:.2f}\n"
          f"  {x['trades']:.0f} positions, {x['stops']:.0f} closed by a stop, in a position "
          f"{x['in_market_pct']:.0f}% of the time, worst fall ${x['max_drawdown']:.2f}\n"
          f"  money at the end: Arcus ${x['arcus']:.2f}, Lighter ${x['lighter']:.2f}; moved back to half and half "
          f"{x['rebalances']:.0f} times (whenever one venue fell under 40% of it while flat)")
    never = bt.run(series, a.capital, st, rebalance_below=0.0).summary()
    print(f"  if the money is never moved between the venues: ${never['per_day']:+.3f} a day, ending Arcus "
          f"${never['arcus']:.2f}, Lighter ${never['lighter']:.2f}")
    months: dict[str, list[float]] = {}
    for t in r.trades:
        months.setdefault(dt.datetime.fromtimestamp(t.closed, dt.UTC).strftime("%Y-%m"), []).append(t.net)
    print("  by month closed: " + " · ".join(f"{m} ${sum(v):+.2f} ({len(v)})" for m, v in sorted(months.items())))
    print("\n  positions:")
    for t in r.trades[-a.show:]:
        print(f"    {day(t.opened)[:6]} {t.symbol:8} short {NAMES[t.short_venue]:7} {t.hours:6.0f} h  "
              f"${t.notional:>6,.0f} at {t.leverage:4.1f}x  funding {t.funding:+6.2f}  prices {t.price:+6.2f}  "
              f"costs {-t.cost:6.2f}  "
              f"net {t.net:+6.2f}  {t.why[:46]}")
    print("\nEach market on its own, the whole capital:")
    print(f"  {'market':8} {'days':>5} {'$ a day':>8} {'net':>8} {'funding':>8} {'prices':>8} {'costs':>7} "
          f"{'positions':>9} {'stops':>5} {'in market':>9} {'Arcus - Lighter':>15}")
    for row in bt.by_market(series, a.capital, st):
        print(f"  {row['market']:8} {row['days']:5.0f} {row['per_day']:+8.3f} {row['net']:+8.2f} "
              f"{row['funding']:+8.2f} {row['price']:+8.2f} {-float(row['cost']):7.2f} {row['trades']:9.0f} "
              f"{row['stops']:5.0f} "
              f"{row['in_market_pct']:8.0f}% {row['diff_apr']:+13.1f}%/y")
    print("\nWhat changes the result (one position at a time):")
    for label, alt in (("a fill costs 0.5 bp", replace(st, fill_cost_bp=0.5)),
                       ("a fill costs 2 bp", replace(st, fill_cost_bp=2.0)),
                       ("a fill costs 4 bp", replace(st, fill_cost_bp=4.0)),
                       ("hold at least 0 h", replace(st, min_hold_h=0.0)),
                       ("hold at least 72 h", replace(st, min_hold_h=72.0)),
                       ("close after 24 h at most", replace(st, max_hold_h=24.0)),
                       ("open only above 10% a year", replace(st, min_edge_apr=10.0)),
                       ("open only above 20% a year", replace(st, min_edge_apr=20.0)),
                       ("leverage 5x at most", replace(st, max_leverage=5.0)),
                       ("stop at 2 daily moves", replace(st, stop_sigmas=2.0)),
                       ("stop at 5 daily moves", replace(st, stop_sigmas=5.0))):
        y = bt.run(series, a.capital, alt).summary()
        print(f"  {label:28} ${y['per_day']:+.3f} a day · {y['trades']:3.0f} positions · {y['stops']:2.0f} stops · "
              f"funding {y['funding']:+7.2f} · costs {-y['cost']:6.2f}")
    sc = bt.scalp(series, a.capital, st)
    print(f"\nIn and out around single payments instead (whenever the last payment covered four fills):\n"
          f"  {sc['trades']:.0f} round trips, funding ${sc['funding']:+.2f}, fills ${-sc['cost']:.2f}, net "
          f"${sc['net']:+.2f} = ${sc['per_day']:+.3f} a day")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="arbitrage", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def money(p: argparse.ArgumentParser) -> None:
        p.add_argument("--arcus", type=float,
                       help="collateral on Arcus to plan with (default: the account's free collateral)")
        p.add_argument("--lighter", type=float, help="collateral on Lighter to plan with")

    p = sub.add_parser("scan", help="rank the markets both venues list")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--feeds", action="store_true", help="add ProFunding's and arb.sh's view")
    p.add_argument("--json", action="store_true")
    money(p)
    p = sub.add_parser("plan", help="one market's plan")
    p.add_argument("symbol")
    money(p)
    p = sub.add_parser("paper", help="a paper record of holding a pair")
    p.add_argument("action", choices=("open", "status", "close"))
    p.add_argument("symbol", nargs="?")
    money(p)
    sub.add_parser("feeds", help="ProFunding's and arb.sh's view")
    p = sub.add_parser("backtest", help="the rules replayed on all the history there is")
    p.add_argument("--capital", type=float, default=240.0)
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--data", help="folder with the history (default: arbitrage/data/history, from `arbitrage history`)")
    p.add_argument("--show", type=int, default=40, help="how many of the last positions to list")
    p = sub.add_parser("run", help="the executor: paper unless --live")
    p.add_argument("--live", action="store_true")
    p.add_argument("--yes", action="store_true", help="--live without the typed confirmation (a service)")
    p.add_argument("--seconds", type=float, help="stop after this long (the position, if any, is kept)")
    money(p)
    for name, text in (("status", "the running bot"), ("close", "close the position"), ("pause", "open nothing new"),
                       ("resume", "open positions again")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--live", action="store_true")
        if name == "close":
            p.add_argument("--now", action="store_true", help="with taker orders, at once")
    for name in ("skip", "unskip"):
        p = sub.add_parser(name, help="a market the bot must not open" if name == "skip" else "allow it again")
        p.add_argument("symbol")
    p = sub.add_parser("history", help="download both venues' funding and price history for the backtest")
    p.add_argument("--venue", choices=("lighter", "arcus", "both"), default="both")
    p.add_argument("--symbols", nargs="*")
    p = sub.add_parser("start", help="the executor in the background: paper unless --live")
    p.add_argument("--live", action="store_true")
    p.add_argument("--yes", action="store_true", help="--live without the typed confirmation")
    money(p)
    p = sub.add_parser("stop", help="stop the background executor (its position and stops stay)")
    p.add_argument("--live", action="store_true")
    p = sub.add_parser("livetest", help="REAL MONEY, minimum size: the Lighter leg, the Arcus leg, then the "
                                        "executor on both venues, with reports (needs ARB_LIVE=1, LIVE typed)")
    p.add_argument("symbol", nargs="?", default="SPY")
    p.add_argument("--what", choices=["all", "lighter", "arcus", "engine"], default="all",
                   help="all (default): the three parts in turn, each only if the one before passed")
    p.add_argument("--hold", type=float, default=30.0, help="engine part: seconds each position is held (default 30)")
    p.add_argument("--expiry", action="store_true",
                   help="also wait about 6 minutes to see Lighter expire a maker order")
    p.add_argument("--max-loss", type=float, default=1.0, help="stop and close everything this many dollars down")
    sub.add_parser("settings", help="the settings and what they mean")
    p = sub.add_parser("set", help="change a setting: arbitrage set max_hold_h 72")
    p.add_argument("name")
    p.add_argument("value")
    a = ap.parse_args(argv)
    if a.cmd == "paper" and a.action in ("open", "close") and not a.symbol:
        ap.error(f"paper {a.action} needs a market, e.g. arbitrage paper {a.action} SPY")
    if a.cmd == "feeds":
        a.arcus = a.lighter = None
    cfg = load()
    if a.cmd == "history":
        from arbitrage import history

        tape = ROOT.parent / "arcus" / "data" / "scout" / "tape"
        history.download(ROOT / "data" / "history", venues=("lighter", "arcus") if a.venue == "both" else (a.venue,),
                         symbols=a.symbols, tape_root=tape if tape.is_dir() else None)
        sys.exit(0)
    plain = {"settings": cmd_settings, "set": cmd_settings, "backtest": cmd_backtest, "run": cmd_run,
             "status": cmd_control, "close": cmd_control, "pause": cmd_control, "resume": cmd_control,
             "skip": cmd_control, "unskip": cmd_control, "start": cmd_service, "stop": cmd_service,
             "livetest": cmd_livetest}
    if a.cmd in plain:
        sys.exit(plain[a.cmd](a, cfg))
    fn = {"scan": cmd_scan, "plan": cmd_plan, "paper": cmd_paper, "feeds": cmd_feeds}[a.cmd]
    sys.exit(asyncio.run(fn(a, cfg)))


if __name__ == "__main__":
    main()
