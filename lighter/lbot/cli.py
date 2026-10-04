"""`lbot`: the Lighter bot's command line. `lbot --help` lists everything; the README explains each command."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from lbot import ops, settings
from lbot.config import Config, load
from lbot.log import setup as log_setup

CONFIRM_FRESH_S = 600


def _cfg() -> Config:
    cfg = load()
    cfg.ensure_dirs()
    return cfg


def _lev(text: str | None, max_lev: float, cap: float | None) -> float:
    if text in (None, "", "max"):
        lev = max_lev
    else:
        lev = float(str(text).lower().removesuffix("x"))
    if cap:
        lev = min(lev, cap)
    return min(lev, max_lev)


def _money(text: str | None) -> float | None:
    if not text:
        return None
    t = text.lower().replace(",", "").removeprefix("$")
    mult = 1_000 if t.endswith("k") else 1_000_000 if t.endswith("m") else 1
    return float(t.rstrip("km")) * mult


# ------------------------------------------------------------------------------------------------ commands
def cmd_up(a: argparse.Namespace) -> None:
    cfg = _cfg()
    print(f"scout: pid {ops.start(cfg, 'scout', ['scout', 'run'])}")
    print("telegram: the trading bot's one Telegram bot shows Lighter too (`bot up` in treading-bot/bot; /l there)")


def cmd_down(a: argparse.Namespace) -> None:
    cfg = _cfg()
    for n in ("scout",) + (("run-paper", "run-live") if a.all else ()):
        if ops.stop(cfg, n):
            print(f"stopped {n}")


def cmd_status(a: argparse.Namespace) -> None:
    cfg = _cfg()
    st = ops.status(cfg)
    if a.json:
        print(json.dumps(st, indent=1, default=str))
        return
    print("services: " + ", ".join(f"{k} {'up (pid ' + str(v) + ')' if v else 'down'}" for k, v in st["services"].items()))
    for mode in ("live", "paper"):
        s = st.get(mode)
        if not s:
            continue
        run_pnl = s.get("run_pnl")
        print(f"\n{mode.upper()} {s['market']} {s['setup']} @ {s['leverage']:g}x  [{s['state']}] {s.get('why') or 'quoting'}"
              f"  (status {s['age_s']:.0f}s old)")
        eq = s.get("equity")
        print(f"  equity ${eq:,.2f}" if eq is not None else "  equity ?", end="")
        print(f"  position {s['pos']:+g} (${s['pos_usd']:,.0f})  run: ${s['run_volume']:,.0f} traded, "
              f"{s['run_fills']} fills, PnL {run_pnl:+.2f}" if run_pnl is not None else "")
        for o in s.get("orders", []):
            print(f"  {o['side']:<4} {o['qty']:g} @ {o['px']:g} [{o['state']}]")
    sc = st.get("scan")
    if sc:
        print(f"\nlast scan {sc['age_min']:.0f} min ago at ${sc['capital']:,.0f}:")
        for k, v in sc["lists"].items():
            print(f"  {k}: " + (" · ".join(v) or "nothing passes"))
    rec = st.get("recorder")
    if rec:
        age = (time.time() - float(rec.get("t", 0))) / 60
        print(f"\nrecorder: {rec['markets']} markets, health written {age:.0f} min ago"
              + ("" if st["services"].get("scout") else " (the scout is not running)"))


def cmd_markets(a: argparse.Namespace) -> None:
    from lbot.trade.runner import fetch_markets
    cfg = _cfg()
    ms = asyncio.run(fetch_markets(cfg))
    print(f"{'market':<10} {'id':>3} {'max lev':>7} {'tick':>10} {'min order':>9} {'volume 24h':>14} {'trades':>8}")
    for m in sorted(ms.values(), key=lambda m: -m.day_volume_usd):
        print(f"{m.symbol:<10} {m.market_id:>3} {m.max_leverage:>6.0f}x {m.tick:>10g} ${m.min_order_usd(m.last_price):>8.2f} "
              f"${m.day_volume_usd:>13,.0f} {m.day_trades:>8,}" + ("" if m.active else "  (inactive)"))


def cmd_record(a: argparse.Namespace) -> None:
    from lbot.scout.record import Recorder
    from lbot.venue.rest import Rest
    cfg = _cfg()
    log_setup(cfg.logs_dir, "record")

    async def go() -> None:
        rest = Rest(cfg.endpoints.rest)
        r = Recorder(cfg.data_dir, rest, cfg.endpoints.ws, markets=a.markets.split(",") if a.markets else None,
                     depth=not a.no_depth)
        try:
            await r.run(a.seconds)
        finally:
            await rest.close()
    asyncio.run(go())


def cmd_scout(a: argparse.Namespace) -> None:
    from lbot.scout import service
    cfg = _cfg()
    if a.what == "run":
        log_setup(cfg.logs_dir, "scout", echo="warn")
        asyncio.run(service.run(cfg, record=not a.no_record, seconds=a.seconds))
        return
    cap = float(a.capital) if a.capital not in (None, "auto") else asyncio.run(service.account_capital(cfg))
    eff = settings.effective(cfg)
    from lbot.scout.scan import Scanner
    Scanner(cfg).scan(cap, markets=a.markets.split(",") if a.markets else None, stops=settings.stops(cfg),
                      volume_cost=float(eff["volume_cost"]), lev_cap=settings.lev_cap(cfg), full24=a.full)
    print((cfg.data_dir / "scout" / "report.txt").read_text())


def cmd_backtest(a: argparse.Namespace) -> None:
    """One setup on recorded days, printed per day."""
    from lbot.scout.record import load_markets
    from lbot.scout.sim import MarketRules, Sim, SimCfg, Window
    from lbot.scout.tape import Tape, day_start
    from lbot.trade.sizing import Stops, sizes
    from lbot.trade.strategy import parse
    cfg = _cfg()
    m = load_markets(cfg.data_dir).get(a.market)
    if m is None:
        sys.exit(f"{a.market}: unknown market (run lbot markets first)")
    tape = Tape(cfg.data_dir / "tape")
    s = parse(a.setup)
    lev = _lev(a.lev, m.max_leverage, settings.lev_cap(cfg))
    st = tuple(float(x) for x in a.stops.split("/")) if a.stops else settings.stops(cfg)
    sz = sizes(float(a.capital), lev, Stops(*st))
    mr = MarketRules(m.tick, m.step, m.min_base, m.min_quote, m.mmf_frac, m.maker_fee, m.taker_fee)
    simcfg = replace(SimCfg(), maker_us=cfg.latency.maker_us, taker_us=cfg.latency.taker_us)
    print(f"{a.market} {s.name} @ {lev:g}x, {sz.label()}")
    for d in a.days.split(",") if a.days else tape.days(a.market):
        w = Window(tape.load_day(a.market, d), day_start(d), day_start(d) + 86_400 * 1_000_000, simcfg)
        r = Sim(s.params(), sz, mr, simcfg, s.name).run(w)
        mo = " ".join(f"{k} {v:+.2f}" for k, v in r.markout_bps.items())
        print(f"{d}: ${r.volume:>12,.0f} traded, {r.maker_fills} maker + {r.taker_fills} taker fills, PnL ${r.pnl:+.2f} "
              f"({r.cost_bps:+.2f} bp), quoting {r.quoting_s / 3600:.1f} h of {r.hours:.1f}, stops {r.pos_stops}/"
              f"{r.day_stops}{' KILL' if r.killed else ''}; markouts {mo}")


def cmd_tape(a: argparse.Namespace) -> None:
    from lbot.scout.importer import import_all
    from lbot.scout.tape import Tape
    cfg = _cfg()
    for k, v in import_all(Path(a.path), Tape(cfg.data_dir / "tape"),
                           a.markets.split(",") if a.markets else None).items():
        print(k, v)


def _live_gate(cfg: Config, spec: Any, confirmed: bool) -> None:
    """Live needs LBOT_LIVE=1, a passing doctor and the owner's confirmation."""
    from lbot import doctor
    ok, lines = asyncio.run(doctor.check(cfg, spec.market, spec.leverage, spec.stops))
    print(doctor.render(ok, lines))
    if not ok:
        sys.exit("refused: the doctor found problems")
    if confirmed:
        return
    if not sys.stdin.isatty():
        sys.exit("refused: a live start needs you to type LIVE (or confirm from Telegram)")
    if input(f"LIVE {spec.market} {spec.setup} @ {spec.leverage:g}x with real money. Type LIVE to start: ").strip() != "LIVE":
        sys.exit("not started")


def _spec_from_args(cfg: Config, a: argparse.Namespace) -> Any:
    from lbot.trade.engine import RunSpec
    from lbot.trade.runner import fetch_markets, load_spec
    from lbot.trade.strategy import parse
    if a.spec:
        spec = load_spec(Path(a.spec))
        if a.live:
            spec = replace(spec, mode="live")
        return spec
    if not a.market or not a.setup:
        sys.exit("lbot run MARKET SETUP (e.g. lbot run ETH 'smart +1'), or --spec FILE")
    ms = asyncio.run(fetch_markets(cfg))
    m = ms.get(a.market.upper())
    if m is None:
        sys.exit(f"{a.market}: not a Lighter perp")
    s = parse(a.setup)
    return RunSpec(market=m.symbol, setup=s.name, leverage=_lev(a.lev, m.max_leverage, settings.lev_cap(cfg)),
                   mode="live" if a.live else "paper", capital=_money(a.capital), stops=settings.stops(cfg),
                   sl=_money(a.sl), tp=_money(a.tp), vol=_money(a.vol))


def cmd_run(a: argparse.Namespace) -> None:
    from lbot.trade.runner import RunRefused, build
    cfg = _cfg()
    spec = _spec_from_args(cfg, a)
    if spec.mode == "live":
        confirmed = False
        if a.confirmed and a.spec:
            raw = json.loads(Path(a.spec).read_text())
            confirmed = time.time() - float(raw.get("confirmed_at") or 0) < CONFIRM_FRESH_S
        _live_gate(cfg, spec, confirmed)
    from lbot.scout import autopilot
    if autopilot.Auto.load(cfg).on and spec.source == "you":
        autopilot.turn_off(cfg, "you started your own run")
    if a.bg:
        from lbot.scout import pilot
        pid = pilot.start(cfg, spec, confirmed=spec.mode == "live")
        print(f"started in the background: pid {pid} (lbot status; lbot stop)")
        return
    log_setup(cfg.logs_dir, f"run-{spec.mode}", echo="warn")

    async def go() -> None:
        eng = await build(cfg, spec)
        print(f"{spec.mode.upper()} {spec.market} {spec.setup} @ {spec.leverage:g}x: Ctrl-C stops it (quotes "
              f"cancelled, the position kept)")
        await eng.run_loop(a.seconds)
    try:
        asyncio.run(go())
    except RunRefused as e:
        sys.exit(f"refused: {e}")


def cmd_control(a: argparse.Namespace) -> None:
    from lbot.scout import autopilot
    from lbot.trade.engine import send_control
    cfg = _cfg()
    if a.cmd in ("stop", "close") and autopilot.Auto.load(cfg).on:
        autopilot.turn_off(cfg, f"you sent {a.cmd}")
        print("autopilot turned off")
    modes = [a.mode] if a.mode else [m for m in ("live", "paper") if ops.running(cfg, f"run-{m}")]
    if not modes:
        print("no run is going")
        return
    for m in modes:
        send_control(cfg.state_dir, m, a.cmd)
        print(f"{a.cmd} sent to the {m} run")


def cmd_pilot(a: argparse.Namespace) -> None:
    from lbot.scout import pilot
    from lbot.trade.engine import RunSpec
    cfg = _cfg()
    if a.what == "status":
        cmd_status(argparse.Namespace(json=False))
        return
    row = pilot.pick(cfg, a.list, a.n)
    spec = RunSpec(market=row["market"], setup=row["setup"], leverage=float(row["leverage"]),
                   mode="live" if a.live else "paper", capital=None if a.live else float(row["capital"]),
                   stops=settings.stops(cfg), source=a.list)
    if spec.mode == "live":
        _live_gate(cfg, spec, False)
    print(f"started: pid {pilot.start(cfg, spec, confirmed=spec.mode == 'live')}")


def cmd_auto(a: argparse.Namespace) -> None:
    from lbot.scout import autopilot
    cfg = _cfg()
    if a.action == "on":
        mode = "live" if a.live else "paper"
        if mode == "live":
            if not cfg.live_allowed:
                sys.exit("refused: LBOT_LIVE=1 is not set in lighter/.env")
            if not sys.stdin.isatty() or input("The autopilot will trade real money. Type LIVE: ").strip() != "LIVE":
                sys.exit("not turned on")
        cost = None if a.cost in (None, "auto") else float(a.cost)
        autopilot.turn_on(cfg, mode, a.budget, cost)
    elif a.action == "off":
        autopilot.turn_off(cfg, "you turned it off")
    elif a.action == "set":
        au = autopilot.Auto.load(cfg)
        if a.budget is not None:
            au.budget = a.budget
        if a.cost is not None:
            au.ceiling = None if a.cost == "auto" else float(a.cost)
        au.save(cfg)
    au = autopilot.Auto.load(cfg)
    print(f"autopilot {'ON (' + au.mode + ')' if au.on else 'off'}: budget ${au.budget:g} a day, pot ${au.pot:.2f}, "
          f"ceiling {'the list budget' if au.ceiling is None else f'${au.ceiling:.3f}/1k'}")
    if au.run:
        print(f"running: {au.run['market']} {au.run['setup']} (run stop ${au.run.get('sl', 0):.2f})")
    if au.last:
        print(f"last: {au.last}")


def cmd_doctor(a: argparse.Namespace) -> None:
    from lbot import doctor
    cfg = _cfg()
    lev = float(a.lev.lower().removesuffix("x")) if a.lev and a.lev != "max" else None
    ok, lines = asyncio.run(doctor.check(cfg, a.market.upper() if a.market else None, lev, settings.stops(cfg)))
    print(doctor.render(ok, lines))
    sys.exit(0 if ok else 1)


def cmd_set(a: argparse.Namespace) -> None:
    cfg = _cfg()
    if a.name is None:
        for k, v in settings.effective(cfg).items():
            print(f"{k:<14} {settings.show(k, v):<22} {settings.SETTINGS[k].help}")
        return
    if a.value in ("default", "reset"):
        settings.reset(cfg, a.name)
    else:
        try:
            settings.save(cfg, a.name, settings.parse(a.name, a.value))
        except ValueError as e:
            sys.exit(str(e))
    print(f"{a.name} = {settings.show(a.name, settings.effective(cfg)[a.name])}")


def cmd_account(a: argparse.Namespace) -> None:
    from lbot.account import report
    cfg = _cfg()
    print(asyncio.run(report(cfg)))


def cmd_keys(a: argparse.Namespace) -> None:
    from lbot.venue.signer import generate_key
    priv, pub = generate_key()
    print("A new API key pair (made on this machine; nothing was sent):")
    print(f"  public:  {pub}")
    print(f"  private: {priv}")
    print("Register the public key with your account in the Lighter app (API keys), or with the SDK's "
          "system_setup example, then put the private key and its slot in lighter/.env.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="lbot", description="The Lighter (Robinhood Chain) market-making bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("up", help="start the scout (recorder and scans) in the background")
    p.set_defaults(fn=cmd_up)
    p = sub.add_parser("down", help="stop them (--all: the running bot too, position kept)")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_down)
    p = sub.add_parser("status", help="what runs, the run, the last scan")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("markets", help="every Lighter perp: leverage, tick, minimum order, volume")
    p.set_defaults(fn=cmd_markets)
    p = sub.add_parser("record", help="record the market tape (the scout does this itself)")
    p.add_argument("--seconds", type=float)
    p.add_argument("--markets")
    p.add_argument("--no-depth", action="store_true")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("scout", help="run: record + scan every N minutes; scan: one scan now")
    p.add_argument("what", choices=("run", "scan"))
    p.add_argument("--capital")
    p.add_argument("--markets")
    p.add_argument("--full", action="store_true", help="re-run the last 24 h for every setup")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--seconds", type=float)
    p.set_defaults(fn=cmd_scout)
    p = sub.add_parser("backtest", help="one setup on the recorded days")
    p.add_argument("market")
    p.add_argument("setup")
    p.add_argument("--capital", default="100")
    p.add_argument("--lev")
    p.add_argument("--stops", help="position/daily/kill in %%, e.g. 2/5/15")
    p.add_argument("--days")
    p.set_defaults(fn=cmd_backtest)
    p = sub.add_parser("tape", help="import the old recorder's Parquet files")
    p.add_argument("action", choices=("import",))
    p.add_argument("path")
    p.add_argument("--markets")
    p.set_defaults(fn=cmd_tape)
    p = sub.add_parser("run", help="run a setup: lbot run ETH 'smart +1' [--lev 50] [--live] [--sl 10]")
    p.add_argument("market", nargs="?")
    p.add_argument("setup", nargs="?")
    p.add_argument("--lev")
    p.add_argument("--capital")
    p.add_argument("--sl")
    p.add_argument("--tp")
    p.add_argument("--vol")
    p.add_argument("--live", action="store_true")
    p.add_argument("--spec")
    p.add_argument("--confirmed", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--bg", action="store_true", help="in the background")
    p.add_argument("--seconds", type=float)
    p.set_defaults(fn=cmd_run)
    for name, help_ in (("stop", "stop the run (quotes cancelled, position kept)"),
                        ("close", "close the position (maker, then taker) and stop"),
                        ("pause", "no new orders (closing orders keep working)"), ("unpause", "quote again"),
                        ("resume", "trade again after the kill or a daily stop")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--mode", choices=("paper", "live"))
        p.set_defaults(fn=cmd_control, cmd=name)
    p = sub.add_parser("pilot", help="approve a list's pick: lbot pilot approve 1 [--list most] [--live]")
    p.add_argument("what", choices=("approve", "status"))
    p.add_argument("n", nargs="?", type=int, default=1)
    p.add_argument("--list", choices=("most", "cheapest", "max"), default="most")
    p.add_argument("--live", action="store_true")
    p.set_defaults(fn=cmd_pilot)
    p = sub.add_parser("auto", help="the autopilot: lbot auto on [--live] [--budget 5] [--cost 0.05] | off | status")
    p.add_argument("action", nargs="?", choices=("status", "on", "off", "set"), default="status")
    p.add_argument("--live", action="store_true")
    p.add_argument("--budget", type=float)
    p.add_argument("--cost")
    p.set_defaults(fn=cmd_auto)
    p = sub.add_parser("doctor", help="everything a live run needs (read-only)")
    p.add_argument("market", nargs="?")
    p.add_argument("--lev")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("set", help="see or change a setting: lbot set daily_stop 5")
    p.add_argument("name", nargs="?", choices=list(settings.SETTINGS))
    p.add_argument("value", nargs="?")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("account", help="the account as Lighter keeps it: volume, PnL, points")
    p.set_defaults(fn=cmd_account)
    p = sub.add_parser("keys", help="make a new API key pair locally")
    p.set_defaults(fn=cmd_keys)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
