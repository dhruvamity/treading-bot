"""The Arcus command line (`arcus <command>`; `arcus -h` lists them). What belongs to the whole machine (all three
bots, the Telegram bot, export, import) is `tbot` (main_tbot below).

    arcus up                        start the Arcus part: its scout, and the guardian while a live run exists
    arcus scout [stop]              start (or stop) the Arcus scout alone; `scout run|scan|limits|playbook` as before
    arcus status                    the Arcus part on one screen: services, trading bot, what is deployed, last scan
    arcus dashboard                 live screen, every 10 s: today's volume and PnL, the capital's profit or loss
    arcus pilot approve 1 [--live]  trade the scout's #1 setup (paper, or real money)
        [--list volume|cheapest|max] [--max-lev]   from another top 3, or at the market's maximum leverage
    arcus pilot close               close the position and stop trading
    arcus down [--all]              stop the Arcus part (--all: its runs too, positions kept)
    arcus doctor pilot              is everything ready for live? (reads only)
    tbot export                    one file with everything recorded and traded since the last export (no keys)
    tbot import [FILE]             take such a file in on another machine
    tbot sync status               two machines: a trader takes the lists from the machine that makes them

Commands that touch a real account (cancel-all, flatten) ask for CONFIRM.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from arcus.common import role as roles
from arcus.common import sizing
from arcus.common.config import load_app, load_arcus_config, load_session
from arcus.common.logging import setup_logging
from arcus.common.secrets import KNOWN_SECRETS, SecretStore, load_dotenv, mask


def _run(coro: Any) -> Any:
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    return asyncio.run(coro)


def _role() -> str:
    """This machine's role (BOT_ROLE in .env); a word that is not a role stops the command with what to fix."""
    try:
        return roles.role()
    except ValueError as e:
        print(e)
        sys.exit(2)


def _must_trade() -> None:
    """Stop here on a machine whose role does not trade (a recorder or a scout machine)."""
    why = roles.no_trading()
    if why:
        print(f"not started: {why}")
        sys.exit(2)


def _confirm(what: str) -> None:
    print(what)
    if input("Type CONFIRM to proceed: ").strip() != "CONFIRM":
        print("aborted")
        sys.exit(1)


# ---------------------------------------------------------------------------------------------- sessions
SESSIONS_DIR = Path("config/sessions")


def resolve_session(name: str) -> Path:
    """`arcus_btc_mm`, `arcus_btc_mm.yaml` or a path."""
    for p in (Path(name), SESSIONS_DIR / name, SESSIONS_DIR / f"{name}.yaml"):
        if p.is_file():
            return p
    have = ", ".join(sorted(p.stem for p in SESSIONS_DIR.glob("*.yaml"))) or "none"
    print(f"no session called {name!r} (available: {have})")
    sys.exit(2)


def _load_sessions(a: argparse.Namespace) -> list[Any]:
    names = list(getattr(a, "sessions", None) or []) + list(getattr(a, "session", None) or [])
    if not names:
        print("name at least one session, e.g. `arcus run arcus_btc_mm` (see `arcus sessions`)")
        sys.exit(2)
    out = [load_session(resolve_session(n)) for n in names]
    if getattr(a, "mode", None):
        for s in out:
            s.mode = a.mode
    return out


def _run_mode(a: argparse.Namespace) -> Any:
    from arcus.core.livelock import RunMode

    return RunMode.LIVE if getattr(a, "live", False) else RunMode.TESTNET if getattr(a, "testnet", False) else RunMode.PAPER


def _describe(s: Any) -> str:
    return (f"{s.session_id:<20} {s.venue:<6} sub {s.account_index:<2} {s.market:<6} mode={s.mode:<6} "
            f"capital ${s.capital_usd:g}  lev {s.leverage_max:g}x  inv cap ${s.inventory_cap_usd:g}  "
            f"live_enabled={s.live_enabled}")


def cmd_sessions(a: argparse.Namespace) -> None:
    for p in sorted(SESSIONS_DIR.glob("*.yaml")):
        try:
            print(_describe(load_session(p)))
        except Exception as e:
            print(f"{p.stem:<20} INVALID: {e}")


async def _doctor(sessions: list[Any], mode: Any, *, adopt: bool = False, account_index: int | None = None,
                  replacing: bool = False) -> Any:
    from arcus.core.calendar import TradingCalendar
    from arcus.core.doctor import run_doctor
    from arcus.core.livelock import RunMode
    from arcus.core.liveparams import LiveParams
    from arcus.venues.arcus.rest import ArcusRest

    acfg = load_arcus_config()
    acfg.env = "testnet" if mode is RunMode.TESTNET else "mainnet"
    ar = ArcusRest(acfg.rest_url())
    try:
        lp = LiveParams(arcus=ar)
        await lp.refresh()
        return await run_doctor(sessions, mode=mode, app=load_app(), secrets=SecretStore(),
                                calendar=TradingCalendar.for_app(load_app().state_dir), arcus_rest=ar, markets=lp.markets,
                                adopt_positions=adopt, account_index=account_index, replacing=replacing)
    finally:
        await ar.close()


def cmd_doctor(a: argparse.Namespace) -> None:
    from arcus.core.livelock import RunMode

    sessions = [load_session(resolve_session(n)) for n in a.sessions]
    mode = RunMode.TESTNET if a.testnet else RunMode.PAPER if a.paper else RunMode.LIVE
    print(f"checking {'credentials and account' if not sessions else ', '.join(s.session_id for s in sessions)} "
          f"for a {mode.value} run\n")
    rep = _run(_doctor(sessions, mode, adopt=a.adopt_positions))
    print(rep.render())
    sys.exit(1 if rep.failed else 0)


# ---------------------------------------------------------------------------------------------- trading
def cmd_run(a: argparse.Namespace) -> None:
    from arcus.common.errors import BotError
    from arcus.core.calendar import TradingCalendar
    from arcus.core.livelock import RunMode
    from arcus.core.runner import BotRunner

    _must_trade()
    sessions = _load_sessions(a)
    mode = _run_mode(a)
    app = load_app()
    print(f"{mode.value.upper()} run")
    for s in sessions:
        print("  " + _describe(s))
    if mode is not RunMode.PAPER:
        rep = _run(_doctor(sessions, mode, adopt=a.adopt_positions))
        print("\n" + rep.render() + "\n")
        if rep.failed:
            print(f"not starting: fix the FAIL lines above (re-check with `arcus doctor {' '.join(s.session_id for s in sessions)}`)")
            sys.exit(1)
    typed = False
    if mode is RunMode.LIVE and not a.yes:
        print("This places REAL orders with REAL money on the accounts above.")
        if not sys.stdin.isatty():
            print("no terminal to confirm on: for unattended live runs set live_enabled: true in the session file(s) "
                  "and pass --yes")
            sys.exit(2)
        typed = input("Type LIVE to start: ").strip() == "LIVE"
        if not typed:
            print("not started")
            sys.exit(1)
    setup_logging(app.logs_dir)
    try:
        runner = BotRunner(sessions, mode=mode, cli_live=mode is RunMode.LIVE, app=app, arcus_cfg=load_arcus_config(),
                           secrets=SecretStore(), calendar=TradingCalendar.for_app(app.state_dir),
                           state_db=a.state_db, typed_confirmation=typed)
        _run(runner.run(a.seconds))
    except BotError as e:
        print(f"stopped: {e}")
        sys.exit(2)


def _stop_runs(app: Any, label: str) -> None:
    """Ask every Arcus run to stop (quotes cancelled, positions kept) and wait up to 30 s."""
    from arcus.telegram.control import Control

    ctl = Control(app)
    for mode in ctl.running_modes():
        ctl.request_stop(mode, f"{label} --all")
        print(f"{mode} trading bot: stop requested (quotes cancelled, positions kept)")
    t0 = time.time()
    while ctl.running_modes() and time.time() - t0 < 30:
        time.sleep(1)
    for mode in ctl.running_modes():
        print(f"{mode} trading bot: still running after 30 s; check `arcus status`")


def _up(parts: tuple[str, ...], show: bool = True) -> None:
    from arcus import ops
    from arcus.telegram.control import Control

    app = load_app()
    for line in roles.note(_role(), roles.memory_mb()):
        print(line)
    live = Control(app).is_running("live")
    for line in ops.up_parts(app, dict(os.environ), live, parts):
        print(line)
    if show:
        print("\n" + ops.dashboard(app, dict(os.environ), Path.cwd(), "all" if len(parts) > 1 or "arcus" not in parts
                                   else "arcus"))


def _down(parts: tuple[str, ...], everything: bool, label: str) -> None:
    from arcus import ops
    from arcus.telegram.control import Control

    app = load_app()
    if everything and "arcus" in parts:
        _stop_runs(app, label)
    for line in ops.down_parts(app, parts, everything=everything, live_running=Control(app).is_running("live")):
        print(line)


def cmd_up(a: argparse.Namespace) -> None:
    """`arcus up`: the Arcus part only (its scout, and the guardian while a live run exists). `tbot up` starts all."""
    _up(("arcus",))
    print("\nOnly Arcus was started. The Lighter scout is `lighter up`, the Telegram bot `tbot up telegram`, "
          "everything `tbot up`.")


def cmd_down(a: argparse.Namespace) -> None:
    """`arcus down [--all]`: stop the Arcus part; --all also stops its runs (quotes cancelled, positions kept)."""
    _down(("arcus",), bool(a.all), "arcus down")


def _parts(names: list[str]) -> tuple[str, ...]:
    from arcus import ops

    if not names:
        return ops.PARTS
    bad = [n for n in names if n not in (*ops.PARTS, "scouts", "both", "all")]
    if bad:
        sys.exit(f"tbot: unknown part {bad[0]!r}: arcus, lighter, telegram, scouts (= arcus and lighter), or nothing "
                 "for all of them")
    out: list[str] = []
    for n in names:
        out += ["arcus", "lighter"] if n in ("scouts", "both") else list(ops.PARTS) if n == "all" else [n]
    return tuple(dict.fromkeys(out))


def cmd_tbot_up(a: argparse.Namespace) -> None:
    """`tbot up [arcus] [lighter] [telegram] [scouts]`: start those parts (none named: all of them)."""
    _up(_parts(a.parts))


def cmd_tbot_down(a: argparse.Namespace) -> None:
    """`tbot down [parts] [--all]`: stop those parts; --all also stops the runs of those parts (positions kept)."""
    _down(_parts(a.parts), bool(a.all), "tbot down")


def cmd_tbot_status(a: argparse.Namespace) -> None:
    """`tbot status`: one screen for the whole machine."""
    from arcus import ops

    _role()
    print(ops.dashboard(load_app(), dict(os.environ), Path.cwd(), "all"))


def cmd_tbot_role(a: argparse.Namespace) -> None:
    """`tbot role [trader|recorder|scout|all]`: say what this machine is for, or set it (BOT_ROLE in .env)."""
    if a.role:
        roles.write(a.role)
        os.environ[roles.ENV] = a.role
        print(f"BOT_ROLE={a.role} is now in .env.")
    for line in roles.note(_role(), roles.memory_mb()):
        print(line)
    if a.role:
        print("It applies to what is started from now on: `tbot down`, then `tbot up` (a running trade is left alone).")


def cmd_tbot_scout(a: argparse.Namespace) -> None:
    """`tbot scout [arcus|lighter|both] [--stop]`: start (or stop) the scouts."""
    which = {"arcus": ("arcus",), "lighter": ("lighter",), "both": ("arcus", "lighter")}[a.which]
    if a.stop:
        _down(which, False, "tbot scout")
    else:
        _up(which, show=False)


def cmd_dashboard(a: argparse.Namespace) -> None:
    """Redraw the live dashboard every 10 s until Ctrl-C (--once: print it once). Same numbers as Telegram's
    /dashboard; with no fresh reading from a running bot it reads the account itself at most once a minute."""
    from arcus.scout.capital import account_snapshot
    from arcus.telegram import dashboard
    from arcus.telegram.control import Control

    app = load_app()
    ctl = Control(app)
    url = load_arcus_config().rest.mainnet
    own: dict[str, Any] | None = None
    try:
        while True:
            now = time.time()
            d = dashboard.collect(ctl, now=now, extra=own)
            stale = d.account_age_s is None or d.account_age_s > dashboard.FRESH_S
            if d.mode != "paper" and stale and (own is None or now - own["ts"] > dashboard.FRESH_S):
                with contextlib.suppress(Exception):   # show the last reading instead
                    snap = asyncio.run(account_snapshot(url))
                    if snap and snap["equity"] > 0:
                        own = {**snap, "ts": now, "from": "this screen"}
                        d = dashboard.collect(ctl, now=now, extra=own)
            text = dashboard.render(d, html=False, frame="once" if a.once else "live")
            if a.once:
                print(text)
                return
            print("\033[H\033[2J" + text + "\n\n(Ctrl-C to leave)", flush=True)
            time.sleep(max(0.5, dashboard.REFRESH_S - time.time() % dashboard.REFRESH_S))
    except KeyboardInterrupt:
        print()


def cmd_status(a: argparse.Namespace) -> None:
    from arcus.core.heartbeat import heartbeat_process_alive, read_heartbeat_age_s
    from arcus.core.state import StateStore

    app = load_app()
    if not a.json:
        from arcus import ops

        _role()      # a word in BOT_ROLE that is not a role: say so instead of a traceback
        print(ops.dashboard(app, dict(os.environ), Path.cwd(), "arcus"))
        print("\n(the Arcus part only: `tbot status` shows the whole machine)")
        return
    modes = [a.mode] if a.mode else ["live", "testnet", "paper"]
    out: dict[str, Any] = {}
    for m in modes:
        db = Path(app.state_db_for(m))
        age = read_heartbeat_age_s(app.heartbeat_for(m))
        if not db.exists() and age == float("inf"):
            continue
        st = StateStore(db)
        out[m] = {
            "running": age < 15 and heartbeat_process_alive(app.heartbeat_for(m)),
            "heartbeat_age_s": None if age == float("inf") else round(age, 1),
            "open_orders": [{"cid": o.req.client_id, "venue": o.req.venue.value, "market": o.req.base,
                             "side": o.req.side.value, "price": str(o.req.price), "size": str(o.req.size),
                             "status": o.status.value, "tag": o.req.tag} for o in st.open_orders()],
            "positions": {f"{v.value}:{b}": str(p) for (v, b), p in st.positions.items() if p},
            "resume_requested": st.kv_get("resume") or None,
        }
        st.close()
    print(json.dumps(out or {"note": "no runs yet"}, indent=1))


async def _venue_adapter(venue: str, account_index: int | None, mainnet: bool) -> Any:
    """A signing Arcus adapter for one-off commands (cancel-all, flatten, selftest, guardian). `venue` is kept for the
    command-line and Telegram interfaces; Arcus is the only venue."""
    from arcus.core.creds import arcus_address, arcus_private_keys, discover_arcus_keys, key_for_account
    from arcus.core.liveparams import LiveParams
    from arcus.venues.arcus.adapter import ArcusAdapter
    from arcus.venues.arcus.rest import ArcusRest
    from arcus.venues.arcus.signing import ArcusSigner
    from arcus.venues.base import Venue

    if venue != "arcus":
        raise ValueError(f"unknown venue {venue!r} (Arcus is the only one)")
    s = SecretStore()
    acfg = load_arcus_config()
    acfg.env = "mainnet" if mainnet else "testnet"
    pub = ArcusRest(acfg.rest_url())
    try:
        address = arcus_address(s, not mainnet)
        keys = await discover_arcus_keys(pub, address, arcus_private_keys(s, not mainnet))
        if account_index is None:
            active = [k for k in keys if k.active]
            account_index = active[0].account_index if active else 0
        key = key_for_account(keys, int(account_index or 0))
        lp = LiveParams(arcus=pub)
        await lp.refresh()
    finally:
        await pub.close()
    rest = ArcusRest(acfg.rest_url(), signer=ArcusSigner(key.private_key), address=address, writes_allowed=True,
                     is_mainnet=mainnet)
    return ArcusAdapter(rest, None, address=address, account_index=int(account_index or 0),
                        markets=lp.markets[Venue.ARCUS], use_modify=acfg.use_modify)


async def venue_cancel_all(venue: str, account: int | None, mainnet: bool, market: str | None = None
                           ) -> dict[str, Any]:
    """Cancel every open order on a venue account (shared by the CLI and the Telegram bot): the account, and the
    orders the venue still lists afterwards."""
    ad = await _venue_adapter(venue, account, mainnet)
    try:
        await ad.cancel_all(market)
        try:
            left: int | None = len(await ad.open_orders())
        except Exception:
            left = None
        return {"venue": venue, "net": "MAINNET" if mainnet else "testnet",
                "account": getattr(ad, "account_index", "?"), "open": left}
    finally:
        await ad.close()


async def venue_flatten(venue: str, account: int | None, mainnet: bool, taker: bool) -> dict[str, Any]:
    """Cancel everything, then close every position with reduce-only orders (maker at the touch, or IOC): how many
    positions there were, the orders sent, and the positions still open a moment later."""
    from arcus.common.ids import ClientIdFactory
    from arcus.core.guardian import flatten_orders
    from arcus.venues.base import Venue

    ad = await _venue_adapter(venue, account, mainnet)
    try:
        await ad.cancel_all(None)
        pos = [p for p in await ad.positions() if p.size]
        mk = {m.base: m for m in await ad.markets()}
        orders = flatten_orders(pos, mk, {p.base: p.mark_price for p in pos}, ClientIdFactory("manual", 1), Venue(venue),
                                taker=taker)
        left = len(pos)
        if orders:
            await ad.place(orders)
            await asyncio.sleep(2)
            with contextlib.suppress(Exception):
                left = len([p for p in await ad.positions() if p.size])
        return {"venue": venue, "positions": len(pos), "orders": len(orders), "open": left, "taker": taker}
    finally:
        await ad.close()


def _cancel_line(r: dict[str, Any]) -> str:
    return (f"cancel-all sent to {r['venue']} {r['net']} account {r['account']}"
            + (f"; {r['open']} orders still open" if r.get("open") else ""))


def _flatten_line(r: dict[str, Any]) -> str:
    return (f"sent {r['orders']} reduce-only {'IOC' if r['taker'] else 'maker'} orders on {r['venue']} for "
            f"{r['positions']} position(s); {r['open']} still open")


def cmd_cancel_all(a: argparse.Namespace) -> None:
    net = "testnet" if a.testnet else "MAINNET"
    if not a.testnet and not a.yes:
        _confirm(f"cancel ALL open orders on {a.venue} ({net}, account {a.account if a.account is not None else 'of your key'})")
    print(_cancel_line(_run(venue_cancel_all(a.venue, a.account, not a.testnet, a.market))))


def cmd_flatten(a: argparse.Namespace) -> None:
    from arcus.core.state import note_closeall

    if not a.testnet:
        _confirm(f"FLATTEN every position on {a.venue} MAINNET with reduce-only orders ({'IOC' if a.taker else 'maker'})")
    r = _run(venue_flatten(a.venue, a.account, not a.testnet, a.taker))
    if r.get("orders"):   # the next start then knows who closed the bot's position
        note_closeall(load_app().state_db_for("testnet" if a.testnet else "live"), a.venue)
    print(_flatten_line(r))


def cmd_selftest(a: argparse.Namespace) -> None:
    from arcus.common.errors import BotError
    from arcus.core.selftest import run_selftest
    from arcus.venues.arcus.rest import ArcusRest
    from arcus.venues.arcus.ws import ArcusWS

    mainnet = not a.testnet
    if a.allow_funded:
        _confirm("--allow-funded: test orders will rest (post-only, below the market) on a FUNDED account for a few "
                 "seconds and then be cancelled.")

    async def go() -> int:
        ad = await _venue_adapter("arcus", a.account, mainnet)
        cfg = load_arcus_config()
        cfg.env = "mainnet" if mainnet else "testnet"
        pub = ArcusRest(cfg.rest_url())
        ad.ws = ArcusWS(cfg.ws_url(), n_levels=5)
        try:
            try:
                acct = await pub.account(ad.address, ad.account_index)
                funded = float(acct.get("equity") or 0) > 0
            except BotError:
                funded = False
            await ad.connect()
            print(f"selftest on Arcus {'mainnet' if mainnet else 'testnet'} subaccount {ad.account_index} "
                  f"({'FUNDED' if funded else 'unfunded'})\n")
            m = ad._markets[a.market.upper()]
            res = await run_selftest(ad, pub, market=m, funded=funded, allow_funded=a.allow_funded,
                                     offset_pct=a.offset_pct)
            print(res.render())
            return 0 if res.ok else 1
        finally:
            await pub.close()
            await ad.close()

    code = _run(go())
    sys.exit(code)


def cmd_resume(a: argparse.Namespace) -> None:
    from arcus.core.state import StateStore

    app = load_app()
    st = StateStore(a.state_db or app.state_db_for(a.mode))
    st.kv_set("resume", json.dumps({"venue": a.venue, "all": a.all, "ts": time.time()}))
    print(f"resume flag written for the {a.mode} bot; it clears safe mode / stops on its next tick")


def cmd_report(a: argparse.Namespace) -> None:
    app = load_app()
    p = app.reports_for(a.mode) / "daily" / f"{a.date}.md"
    print(p.read_text() if p.exists() else f"no {a.mode} report for {a.date} at {p}")


def cmd_diagnose(a: argparse.Namespace) -> None:
    """Why a run filled what it filled (arcus/core/diagnose.py). Read-only."""
    import datetime as dt

    from arcus.core.diagnose import diagnose

    app = load_app()

    def when(x: str) -> int:
        t = dt.datetime.fromisoformat(x.replace("T", " "))
        return int((t if t.tzinfo else t.replace(tzinfo=dt.UTC)).timestamp() * 1_000_000)

    end = when(a.until) if a.until else time.time_ns() // 1000
    start = when(a.since) if a.since else end - int(a.hours * 3600 * 1_000_000)
    base = a.market.upper().removesuffix("-USD") if a.market else None
    setup = None
    if a.replay:
        from arcus.core.diagnose import find_setup

        market = f"{base}-USD" if base else None
        setup = find_setup(Path(app.state_dir) / "pilot_events.jsonl", market, end) if market else None
        if a.setting or setup is None:   # by hand: the setting and the capital / leverage it ran at
            from dataclasses import asdict

            from arcus.scout.sim import Risk

            if not (a.setting and a.capital and a.leverage and market):
                raise SystemExit("--replay: no deployed setup found; give --market, --setting, --capital and --leverage")
            setup = {"market": market, "setting": a.setting, "leverage": a.leverage,
                     "risk": asdict(Risk.for_capital(a.capital, a.leverage))}
        if a.sl is not None:
            setup = {**setup, "max_loss_usd": a.sl or None}
    print(diagnose(db=Path(app.state_db_for(a.mode)), logs=Path(app.logs_dir), tape_root=Path("data/scout/tape"),
                   markets_json=Path("data/scout/markets.json"), start_us=start, end_us=end, base=base, mode=a.mode,
                   setup=setup))


def cmd_keys(a: argparse.Namespace) -> None:
    from arcus.core.creds import arcus_address, arcus_private_keys, discover_arcus_keys
    from arcus.venues.arcus.rest import ArcusRest

    cfg = load_arcus_config()
    cfg.env = "testnet" if a.testnet else "mainnet"

    async def go() -> None:
        r = ArcusRest(cfg.rest_url())
        try:
            s = SecretStore()
            keys = await discover_arcus_keys(r, arcus_address(s, a.testnet), arcus_private_keys(s, a.testnet))
        finally:
            await r.close()
        for k in keys:
            h = k.hours_left()
            left = "no expiry" if h is None else ("EXPIRED" if h <= 0 else f"{h / 24:.1f} days left")
            where = f"subaccount {k.account_index}" if k.account_index is not None else "NOT registered for this address"
            print(f"{k.var:<28} {k.public_key[:8]}…  {where:<32} {k.name or '-':<16} {k.status or '-':<8} {left}")

    _run(go())


def cmd_guardian(a: argparse.Namespace) -> None:
    from arcus.core.alerts import alerter_from_secrets
    from arcus.core.guardian import GuardedVenue, Guardian
    from arcus.venues.base import Venue

    app = load_app()
    setup_logging(app.logs_dir)

    async def go() -> None:
        venues = []
        for v in a.venue:
            ad = await _venue_adapter(v, a.account, not a.testnet)
            venues.append(GuardedVenue(Venue(v), ad, {m.base: m for m in await ad.markets()},
                                       capital_usd=Decimal(str(a.capital))))
        g = Guardian(Path(app.heartbeat_for("testnet" if a.testnet else "live")), venues,
                     alerter_from_secrets(SecretStore()), heartbeat_timeout_s=app.risk.heartbeat_timeout_s,
                     drawdown_hard_pct=app.risk.drawdown_pct)
        await g.run()

    _run(go())


def cmd_telegram(a: argparse.Namespace) -> None:
    from arcus.telegram.api import TelegramAPI
    from arcus.telegram.bot import TelegramBot
    from arcus.telegram.control import Control

    app = load_app()
    setup_logging(app.logs_dir)
    s = SecretStore()
    tok, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first (README.md, section 2)")
        sys.exit(2)
    allowed = {int(x) for x in (s.get("TELEGRAM_ALLOWED_USER_IDS") or "").replace(" ", "").split(",") if x}
    api = TelegramAPI(tok)
    from arcus.scout.pilot import Pilot

    control = Control(app)
    bot = TelegramBot(api, control, owner_chat_id=int(chat), allowed_user_ids=allowed,
                      prefs_path=Path(app.state_dir) / "telegram_prefs.json", read_only=a.read_only,
                      daily_loss_pct=app.risk.daily_loss_pct, pilot=Pilot(Path.cwd(), control))
    print(f"Telegram control bot running for chat {mask(str(chat))}{' (read-only)' if a.read_only else ''}; Ctrl-C stops it")

    async def go() -> None:
        try:
            await bot.run()
        finally:
            await api.close()

    _run(go())


def _workers_arg(v: str) -> int | str:
    if v.lower() == "auto":
        return "auto"
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("--workers must be auto or at least 1")
    return n


def cmd_scout(a: argparse.Namespace) -> None:
    """Record every Arcus perp, backtest the strategy menu on each, rank what to run now."""
    from arcus.scout.scan import scan, table
    from arcus.scout.service import run_service, save_scan

    if a.action in ("start", "stop"):
        (_up(("arcus",), show=False) if a.action == "start" else _down(("arcus",), False, "arcus scout"))
        return
    app = load_app()
    setup_logging(app.logs_dir)
    root = Path.cwd()
    if a.action == "run":
        from arcus.scout.pilot import Pilot
        from arcus.telegram.control import Control

        cfg = load_arcus_config()
        r = _role()
        # the three jobs (arcus/scout/service.py): a flag decides, else the machine's role
        record, rank, supervise = roles.jobs(r, follow=a.follow, record_only=a.record_only)
        pull = None
        if supervise and not rank and os.environ.get("BOT_SYNC_FROM"):
            from arcus import handoff

            pull = lambda: handoff.pull(handoff.roots(root), root / app.state_dir)   # noqa: E731
        pid = Path(app.state_dir) / "scout.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text(str(os.getpid()))
        spec = a.capital or app.sizing.capital_usd
        print("scout: " + (f"recording all Arcus perps, scanning every {a.every_min:g} min at capital {spec}" if rank
                           else "recording all Arcus perps (no scans on this machine)" if record else
                           "following: no recording and no scans here; the lists come from "
                           + (os.environ.get("BOT_SYNC_FROM") or "nowhere yet (BOT_SYNC_FROM is not set)"))
              + "; Ctrl-C stops it")
        try:
            _run(run_service(root, Pilot(root, Control(app)), rest_url=cfg.rest.mainnet, ws_url=cfg.ws.mainnet,
                             every_min=a.every_min, workers=a.workers, ladder=a.ladder, depth=a.depth,
                             capital=a.capital, sizing=app.sizing, record=record, rank=rank, supervise=supervise,
                             pull=pull))
        finally:
            with contextlib.suppress(OSError):
                if pid.read_text() == str(os.getpid()):
                    pid.unlink()
    elif a.action == "scan":
        from arcus.common import settings
        from arcus.scout.capital import account_equity, choose
        from arcus.scout.service import scan_workers
        from arcus.telegram.control import Control

        over = settings.load(app.state_dir)
        z = settings.effective_sizing(app.sizing, over)
        spec = a.capital or over.get("capital") or z.capital_usd
        eq = _run(account_equity(load_arcus_config().rest.mainnet)) if str(spec).lower() == "auto" else None
        cap, src = choose(spec, eq, z)
        n = scan_workers(a.workers if a.workers != "auto" else over.get("scan_workers"),
                         bool(Control(app).running_modes()))
        as_of = None
        if a.as_of == "tape":
            import datetime as dt

            from arcus.scout.scan import tape_end_us

            as_of = tape_end_us(root / "data" / "scout")
            if as_of is None:
                sys.exit("scan: no tape under data/scout/tape (tbot import first)")
            print(f"scanning as of the end of the tape: {dt.datetime.fromtimestamp(as_of / 1e6, dt.UTC):%Y-%m-%d %H:%M} UTC")
        res = scan(root / "data" / "scout", now_us=as_of, workers=n, markets=a.markets or None, ladder=a.ladder,
                   capital=cap, pct=z.pct(), capital_source=src, shortlist=not a.full,
                   volume_cost=settings.volume_cost(over))
        save_scan(root, res)
        print(table(res, a.limit))
    elif a.action == "limits":
        from arcus.scout.scan import limits_table

        print(limits_table(root / "data" / "scout", a.markets or None))
    elif a.action == "playbook":
        from arcus.common import settings
        from arcus.core.calendar import TradingCalendar
        from arcus.scout import playbook as pbk
        from arcus.scout.service import scan_workers
        from arcus.telegram.control import Control

        over = settings.load(app.state_dir)
        z = settings.effective_sizing(app.sizing, over)
        cap = float(a.capital) if a.capital and a.capital != "auto" else \
            float(((pbk.load(root / "data" / "scout") or {}).get("capital")) or 100.0)
        n = scan_workers(a.workers if a.workers != "auto" else over.get("scan_workers"),
                         bool(Control(app).running_modes()))
        book = pbk.Playbook(root / "data" / "scout", capital=cap, pct=z.pct(), lev_caps=settings.lev_caps(over),
                            calendar=TradingCalendar.for_app(app.state_dir),
                            markets=tuple(a.markets) if a.markets else pbk.MARKETS)
        t = book.build(workers=n, budget_s=0)
        print(pbk.table_text(t))


def cmd_recommend(a: argparse.Namespace) -> None:
    """What to run, as Telegram lines to paste (arcus/recommend.py). --scan scans both bots first, as of the tape's end."""
    import subprocess

    from arcus import recommend
    from arcus.common import settings

    app = load_app()
    root = Path.cwd()
    if a.scan:
        here = Path(sys.executable).parent
        for exe in ("arcus", "lighter"):
            cmd = [str(here / exe), "scout", "scan", "--as-of", "tape", *(["--capital", a.capital] if a.capital else [])]
            print("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)), flush=True)
            r = subprocess.run(cmd, cwd=root, stdout=subprocess.DEVNULL)
            if r.returncode:
                print(f"{exe}: the scan failed ({r.returncode}); the lists below are from the one before", file=sys.stderr)
    lists = ["volume", "cheapest", "max"] if a.list == "all" else [a.list]
    budget = settings.volume_cost(settings.load(app.state_dir))
    print(recommend.report(root, a.markets, lists, a.n, budget))


def cmd_pilot(a: argparse.Namespace) -> None:
    """One deployment at a time: status, approve one of the scout's top 3, close."""
    from arcus.scout.pilot import Pilot, describe
    from arcus.telegram.control import Control

    app = load_app()
    setup_logging(app.logs_dir)
    pilot = Pilot(Path.cwd(), Control(app))
    if a.action == "status":
        st = pilot.state()
        act = st.get("active")
        print("deployed:", f"{act['market']} {act['config']} ({act['mode']})" if act else "nothing")
        if act:
            print("running:", pilot.control.is_running(act["mode"]), "| paused by scout:",
                  st.get("paused_by_scout") or "no", "| last check:", st.get("last_review"))
        from arcus.scout.profiles import PROFILES

        for p in PROFILES.values():
            top = pilot.top(p.key)
            budget = f" (at most ${pilot.budget():.2f} per $1,000)" if p.budget else ""
            print(f"\n{p.title} top 3{budget}:" if top else f"\n{p.title}: nothing right now{budget}")
            for i, c in enumerate(top, 1):
                print(f"  {i}. {describe(c)}")
    elif a.action == "approve":
        _must_trade()
        lev = "max" if a.max_lev else "rec"
        c = pilot.pick(a.n, a.list, lev)
        print(describe(c))
        cap = (pilot.latest_scan() or {}).get("capital") or {}
        if cap:
            print(f"Backtested at ${cap['usd']:,.2f} of capital ({cap.get('source')}). Sizes and stops follow the "
                  f"account's equity (re-read at start and at 00:00 UTC), at most "
                  f"{sizing.COVER_X:g}x the last capital a GO scan covered.")
        if a.live:
            if os.environ.get("BOT_PILOT_LIVE") != "1":
                print("live is off: set BOT_PILOT_LIVE=1 in .env first")
                sys.exit(2)
            if input("REAL MONEY. Type LIVE to deploy: ").strip() != "LIVE":
                print("aborted")
                sys.exit(1)
        print(_run(pilot.approve(a.n, live=a.live, by="cli", profile=a.list, lev=lev)))
    elif a.action == "close":
        print(_run(pilot.close(by="cli")))


def cmd_auto(a: argparse.Namespace) -> None:
    """The autopilot: status and plan, on (paper or live), off, budget, cost ceiling (arcus/scout/autopilot.py)."""
    from arcus.scout import autopilot as ap
    from arcus.scout import playbook as pbk

    app = load_app()
    state = Path(app.state_dir)
    if a.action == "on":
        _must_trade()
        if a.live:
            if os.environ.get("BOT_PILOT_LIVE") != "1":
                print("live is off: set BOT_PILOT_LIVE=1 in .env first")
                sys.exit(2)
            if input("REAL MONEY: the autopilot starts and stops LIVE runs by itself. Type LIVE: ").strip() != "LIVE":
                print("aborted")
                sys.exit(1)
        kw: dict[str, Any] = {"on": True, "mode": "live" if a.live else "paper", "by": "cli"}
        if a.budget is not None:
            kw["budget_day"] = a.budget
        if a.cost is not None:
            kw.update(max_cost_bp=a.cost, auto_cost=False)
        ap.configure(state, **kw)
    elif a.action == "off":
        print("was on" if ap.turn_off(state, "arcus auto off") else "was already off")
        print("the running run keeps going until `arcus pilot close` (or Telegram /auto off, which closes it)")
    elif a.action == "set":
        kw = {}
        if a.budget is not None:
            kw["budget_day"] = a.budget
        if a.cost is not None:
            kw.update(max_cost_bp=a.cost, auto_cost=False)
        ap.configure(state, **kw)
    st = ap.load(state)
    pb = pbk.load(Path.cwd() / "data" / "scout")
    print("\n".join(ap.status_lines(st, pb)))
    if pb.get("markets"):
        print("\nNext 24 h at a usual market:")
        print("\n".join("  " + x for x in ap.plan_lines(pb, ap.ceiling(ap.settings_of(st), pb))))
    else:
        print("\nNo playbook on this machine: a trader builds none; it arrives with the lists from the machine that "
              "scans (BOT_SYNC_FROM in .env). Until then the autopilot starts nothing." if roles.is_trader() else
              "\nNo playbook yet: the scout builds it after its next scan (or `arcus scout playbook`)")


def cmd_account(a: argparse.Namespace) -> None:
    """All-time perps volume, fees paid and earned, the fee tier and the realized result, as Arcus keeps them."""
    from arcus.core import account_stats

    raw = asyncio.run(account_stats.read(load_arcus_config().rest.mainnet))
    head, blocks = account_stats.lines(raw)
    print(head)
    for label, lines in blocks:
        print(f"\n{label}")
        print("\n".join(f"  {x}" for x in lines))


def cmd_export(a: argparse.Namespace) -> None:
    """One file with this machine's recordings, trades, state and logs (arcus/export.py). Reads only; no keys in it."""
    import getpass

    from arcus.export import ExportError, Roots, run_export

    try:
        res = run_export(Roots.find(Path.cwd()), Path(a.out).expanduser() if a.out else None, full=a.full,
                         days=a.days, since=a.since, tape=not a.no_tape, keep=a.keep, tag=a.tag or "",
                         probe=not a.no_probe, force=a.force)
    except ExportError as e:
        print(f"not exported: {e}")
        sys.exit(2)
    print(f"\n{res.path}\n{res.bytes / 1e6:,.1f} MB: {res.tape_files:,} tape files, {res.record_files:,} record files"
          + (f"; {len(res.warnings)} files not copied whole (listed in its SUMMARY.md)" if res.warnings else ""))
    for name in res.removed:
        print(f"removed the older {name} (--keep {a.keep}; its data is still in the live folders)")
    print("the next `tbot export` sends what is new since this one" if res.cut.moves_mark else
          "the next plain `tbot export` still sends everything since the last full or plain one")
    print("\nCopy it to the other machine, for example from there:\n"
          f"  scp {getpass.getuser()}@THIS-SERVER:{res.path} ~/Downloads/\n"
          "then, in treading-bot/arcus there:\n  .venv/bin/tbot import")


def cmd_import(a: argparse.Namespace) -> None:
    """Take in a file made by `tbot export` on another machine: check it, merge the tape, unpack the rest."""
    from arcus.export import ExportError, Roots, find_export, run_import

    roots = Roots.find(Path.cwd())
    try:
        path = find_export(roots, a.path)
        print(path)
        res = run_import(roots, path)
    except ExportError as e:
        print(f"not imported: {e}")
        sys.exit(2)
    sys.exit(0 if res["ok"] else 1)


def cmd_sync(a: argparse.Namespace) -> None:
    """Two machines: a trader takes the lists from the machine that makes them (arcus/handoff.py)."""
    from arcus import handoff

    app = load_app()
    where = handoff.roots(Path.cwd())
    state = Path(app.state_dir)
    try:
        if a.action == "serve":       # the other end of a pull: only ever run by sshd, through `tbot sync allow`
            sys.stdout.buffer.write(handoff.pack(where, handoff.since_of(os.environ.get("SSH_ORIGINAL_COMMAND"))))
            sys.stdout.buffer.flush()
        elif a.action == "receive":   # the other end of a push
            why = handoff.may_receive()
            if why:
                raise handoff.SyncError(why)
            st = handoff.take(where, state, sys.stdin.buffer.read(handoff.MAX_TOTAL + 1), "a push")
            print(f"took {len(st['took'])} file(s)" + "".join(f"\n  not taken: {k}: {v}"
                                                             for k, v in st["refused"].items()))
        elif a.action == "key":
            pub = handoff.make_key(handoff.key_path())
            print(f"{pub}\n\nOn the OTHER machine (the one that records), in treading-bot/arcus, paste that line "
                  f"in quotes:\n  .venv/bin/tbot sync allow '{pub}'\n\nThen here, put its address in .env "
                  f"({handoff.FROM}=user@its-address) and try:\n  .venv/bin/tbot sync pull")
        elif a.action == "allow":
            if not a.arg:
                raise handoff.SyncError("give the public key `tbot sync key` printed on the trader, in quotes")
            line, added = handoff.allow(a.arg, Path.cwd())
            print(("Added to ~/.ssh/authorized_keys:" if added else "Already in ~/.ssh/authorized_keys:") + f"\n  {line}"
                  "\nThat key can now run `tbot sync serve` here and nothing else: no shell, no other file.")
        elif a.action == "pull":
            why = handoff.may_receive()
            if why:
                raise handoff.SyncError(why)
            st = handoff.pull(where, state)
            if st.get("error"):
                raise handoff.SyncError(st["error"])
            print(f"took {len(st.get('took') or [])} file(s) from {st.get('source')}")
            print("\n".join(handoff.status_lines(state)))
        elif a.action == "push":
            dest = handoff.Dest.parse(a.arg)
            print(handoff.ssh_push(dest, a.path, handoff.pack(where)))
        else:
            r = _role()
            print(f"this machine: {r} ({roles.WHAT[r]})")
            print("\n".join(handoff.status_lines(state)) if r == "trader" else
                  "it makes its own lists: nothing to fetch. A trader fetches from it once `tbot sync allow` is done "
                  "here." if roles.ranks(r) else
                  "it records only: it has no lists to hand over (scan where the tape is brought to, then "
                  "`tbot sync push user@trader` from there).")
    except (handoff.SyncError, ValueError) as e:
        print(f"sync: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_secrets(a: argparse.Namespace) -> None:
    s = SecretStore()
    if a.action == "init":
        s.init()
        print(f"created {s.path} (0600)")
    elif a.action == "set":
        import getpass

        v = getpass.getpass(f"value for {a.name} (hidden): ")
        s.set(a.name, v)
        print(f"{a.name} stored")
    elif a.action == "import-env":
        print("imported:", s.import_env())
    elif a.action in ("list-redacted", "status"):
        for n, src in s.status().items():
            print(f"{n:34s} {src:8s} {mask(s.get(n)) if src != 'missing' else ''}  # {KNOWN_SECRETS[n]}")


def cmd_probe(a: argparse.Namespace) -> None:
    from arcus.core.liveparams import LiveParams
    from arcus.venues.arcus.rest import ArcusRest
    from arcus.venues.base import Venue

    async def go() -> None:
        acfg = load_arcus_config()
        acfg.env = "testnet" if a.testnet else "mainnet"
        r = ArcusRest(acfg.rest_url())
        try:
            lp = LiveParams(arcus=r)
            await lp.refresh()
            mk = lp.markets[Venue.ARCUS]
            for b in a.markets or sorted(mk)[:10]:
                m = mk.get(b.upper())
                if m:
                    print(f"arcus {b:8s} id={m.venue_market_id:<4} tick={m.tick_size} step={m.step_size} "
                          f"min=${m.min_notional}/{m.min_size} imf={m.imf} mmf={m.mmf} maxlev={m.max_leverage} "
                          f"fees={m.maker_fee}/{m.taker_fee} status={m.status}")
            if not a.testnet:
                print("compliance:", await r.compliance())
            addr = SecretStore().get("ARCUS_ADDRESS" if not a.testnet else "ARCUS_TESTNET_ADDRESS")
            if addr:
                for idx in (0, 1, 2):
                    try:
                        print(f"sub{idx}", await r.rate_limit(addr, idx))
                    except Exception as e:
                        print(f"sub{idx} rateLimit: {e}")
        finally:
            await r.close()

    _run(go())


def cmd_region_check(a: argparse.Namespace) -> None:
    import runpy

    runpy.run_path(str(Path(__file__).parents[1] / "deploy" / "scripts" / "region_check.py"), run_name="__main__")


# ---------------------------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arcus", description="Arcus trading bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn: Any, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    modes = ("mid", "grid", "smart")
    add("sessions", cmd_sessions, "list the session files: venue, subaccount, market, mode, capital, live_enabled")
    sp = add("doctor", cmd_doctor, "check everything a live run needs (credentials, account, sizing, clock); no orders")
    sp.add_argument("sessions", nargs="*", help="session names (default: credentials and account only)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="check for a paper run instead of live")
    g.add_argument("--testnet", action="store_true")
    sp.add_argument("--adopt-positions", action="store_true", help="an existing position is the bot's to manage")
    sp = add("run", cmd_run, "run sessions: paper by default, --live for real money")
    sp.add_argument("sessions", nargs="*", help="session names, e.g. arcus_btc_mm (see `arcus sessions`)")
    sp.add_argument("--session", action="append", help=argparse.SUPPRESS)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="simulated orders on live market data (default)")
    g.add_argument("--testnet", action="store_true", help="real orders on the venue testnet")
    g.add_argument("--live", action="store_true", help="REAL orders: runs doctor, then asks you to type LIVE")
    sp.add_argument("--mode", choices=modes, help="override the strategy mode in the session file for this run")
    sp.add_argument("--yes", action="store_true",
                    help="unattended live start: skips the prompt, needs live_enabled: true in every session")
    sp.add_argument("--adopt-positions", action="store_true", help="an existing position is the bot's to manage")
    sp.add_argument("--seconds", type=float, help="stop after this long")
    sp.add_argument("--state-db", help=argparse.SUPPRESS)
    sp = add("up", cmd_up, "start the Arcus part (its scout, and the guardian while a live run exists); `tbot up` "
             "starts everything")
    sp = add("down", cmd_down, "stop the Arcus part; --all also stops its runs (positions kept); `tbot down` stops "
             "everything")
    sp.add_argument("--all", action="store_true")
    sp = add("status", cmd_status, "the Arcus part on one screen: services, run, what is deployed, last scan, balance "
             "(`tbot status`: the whole machine)")
    sp.add_argument("--mode", choices=["live", "testnet", "paper"])
    sp.add_argument("--json", action="store_true", help="the per-mode details as JSON (heartbeat, orders, positions)")
    sp = add("dashboard", cmd_dashboard, "live screen, every 10 s: today's volume and PnL, the capital's profit or loss")
    sp.add_argument("--once", action="store_true", help="print it once and exit")
    sp = add("selftest", cmd_selftest, "prove Arcus accepts every signed request the bot sends (no trading)")
    sp.add_argument("--account", type=int, help="subaccount (default: the one your key is bound to)")
    sp.add_argument("--market", default="BTC")
    sp.add_argument("--offset-pct", type=float, default=3.0, help="test orders sit this far below the bid")
    sp.add_argument("--allow-funded", action="store_true", help="also run the order steps on a funded account")
    sp.add_argument("--testnet", action="store_true")
    for name, fn, h in (("cancel-all", cmd_cancel_all, "cancel all open orders (mainnet unless --testnet)"),
                        ("flatten", cmd_flatten, "close all positions with reduce-only orders (mainnet unless --testnet)")):
        sp = add(name, fn, h)
        sp.add_argument("--venue", choices=["arcus"], default="arcus")
        sp.add_argument("--account", type=int, help="subaccount (default: the one your key is bound to)")
        sp.add_argument("--testnet", action="store_true")
        if name == "cancel-all":
            sp.add_argument("--market")
            sp.add_argument("--yes", action="store_true", help="skip the confirmation (scripts)")
        else:
            sp.add_argument("--taker", action="store_true")
    sp = add("telegram", cmd_telegram, "Telegram control bot: status, pause/stop/run, cancel/flatten, live alerts")
    sp.add_argument("--read-only", action="store_true", help="status and alerts only; every control is refused")
    sp = add("scout", cmd_scout, "record all Arcus perps, backtest every strategy on each, rank what to run now")
    sp.add_argument("action", nargs="?", default="start",
                    choices=["start", "stop", "run", "scan", "limits", "playbook"],
                    help="start (the default): the scout in the background, like `arcus up` but for it alone; stop: "
                         "stop it; run: record + scan every N min in this terminal (the daemon); scan: one scan now; limits: the least and the "
                         "most capital each market can use; playbook: build and print the autopilot's table (cost "
                         "per session and market state)")
    sp.add_argument("--every-min", type=float, default=30)
    sp.add_argument("--workers", type=_workers_arg, default="auto",
                    help="processes a scan may use: auto (all cores but one; one while a bot runs here) or a number")
    sp.add_argument("--full", action="store_true",
                    help="scan: re-run the last 24 h for every setting, not only those passing on their full days")
    sp.add_argument("--as-of", choices=["now", "tape"], default="now",
                    help="scan: judge the markets as of now (default) or as of the end of the tape, for a tape recorded "
                         "elsewhere and brought here with `tbot import`")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--ladder", action="store_true",
                    help="also backtest 20x, 10x, 5x and 2x below each market's maximum (5x the work; default: the "
                         "maximum only, and Telegram's /run runs any leverage)")
    sp.add_argument("--depth", action="store_true",
                    help="run: also record the top 10 book levels (queue-position data for larger orders; more disk)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--record-only", action="store_true",
                   help="run: record the tape and nothing else (no scans; a recorder machine, BOT_ROLE=recorder)")
    g.add_argument("--follow", action="store_true",
                   help="run: no recording and no scans; take the lists from the other machine and watch the run "
                        "(a trader machine, BOT_ROLE=trader)")
    sp.add_argument("--capital",
                    help="the capital to backtest at: auto (the subaccount's equity; paper capital if unfunded) or "
                         "a dollar amount (default: config/app.yaml sizing.capital_usd)")
    sp = add("recommend", cmd_recommend, "what to run, as Telegram lines to paste: the best setups of each list, per market")
    sp.add_argument("markets", nargs="*", help="only these markets (SPY QQQ); none = the top of each list")
    sp.add_argument("--list", choices=["all", "volume", "cheapest", "max"], default="all")
    sp.add_argument("-n", type=int, default=3, help="how many per list (default 3)")
    sp.add_argument("--scan", action="store_true",
                    help="scan both bots first, as of the end of the tape (the tape brought home with `tbot import`)")
    sp.add_argument("--capital", help="with --scan: the capital to rank for (default: the settings' capital, "
                                      "$100 without a funded account)")
    sp = add("pilot", cmd_pilot, "one deployment at a time: status, approve N [--live], close")
    sp.add_argument("action", choices=["status", "approve", "close"])
    sp.add_argument("n", nargs="?", type=int, default=1, help="approve: which of the top 3")
    sp.add_argument("--live", action="store_true", help="real money (needs BOT_PILOT_LIVE=1 and typing LIVE)")
    sp.add_argument("--list", default="volume", choices=["volume", "cheapest", "max"],
                    help="which top 3: volume (most volume within /set volume_cost, default), cheapest or max "
                         "(arcus/scout/profiles.py)")
    sp.add_argument("--max-lev", action="store_true", help="the same setting at the market's maximum leverage")
    sp = add("auto", cmd_auto, "the autopilot: runs setups by itself within a daily budget (status, on, off, set)")
    sp.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "set"])
    sp.add_argument("--live", action="store_true", help="on: real money (needs BOT_PILOT_LIVE=1 and typing LIVE)")
    sp.add_argument("--budget", type=float, help="dollars added to the pot each day at 00:00 UTC")
    sp.add_argument("--cost", type=float,
                    help="cost ceiling in bp (backtest); fixes it (otherwise the playbook tunes it daily for the budget)")
    add("account", cmd_account, "all-time volume, fees paid and earned, fee tier and result, as Arcus reports them")
    sp = add("resume", cmd_resume, "clear safe mode / stops (after investigation)")
    sp.add_argument("--venue")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--mode", choices=["live", "testnet", "paper"], default="live")
    sp.add_argument("--state-db", help=argparse.SUPPRESS)
    sp = add("report", cmd_report, "print a daily report")
    sp.add_argument("--date", default=time.strftime("%Y-%m-%d", time.gmtime()))
    sp.add_argument("--mode", choices=["live", "testnet", "paper"], default="live")
    sp = add("diagnose", cmd_diagnose, "why a run filled what it filled: orders, acks, rejects, blocks, where orders "
             "rested against the best price, and the takers that traded (read-only)")
    sp.add_argument("--mode", choices=["live", "testnet", "paper"], default="live")
    sp.add_argument("--hours", type=float, default=6.0, help="the last N hours (default 6)")
    sp.add_argument("--since", help="window start, UTC: 2026-09-25 20:00")
    sp.add_argument("--until", help="window end, UTC (default now)")
    sp.add_argument("--market", help="e.g. QQQ (default: the market with the most orders)")
    sp.add_argument("--replay", action="store_true",
                    help="also backtest the run's own setup on the same window (from the pilot's deployed event and the "
                         "engine's sizes) and show it beside the run under each fill model")
    sp.add_argument("--setting", help="--replay by hand: the menu setting the run used")
    sp.add_argument("--capital", type=float, help="--replay by hand: the capital it was sized for")
    sp.add_argument("--leverage", type=float, help="--replay by hand: its leverage")
    sp.add_argument("--sl", type=float, help="--replay: the run's loss limit in dollars (/run ... sl=), which lifts "
                                             "the daily stop and the kill; 0 for none (default: the deployed one)")
    sp = add("keys", cmd_keys, "your API keys as the venue sees them: subaccount, status, expiry")
    sp.add_argument("--testnet", action="store_true")
    sp = add("guardian", cmd_guardian, "run the independent guardian process")
    sp.add_argument("--venue", nargs="+", default=["arcus"])
    sp.add_argument("--account", type=int, help="subaccount (default: the one your key is bound to)")
    sp.add_argument("--capital", type=float, default=0,
                    help="the drawdown limit is a %% of this (default 0: the account's equity when the guardian starts)")
    sp.add_argument("--testnet", action="store_true")
    sp = add("export", cmd_export, "one file with everything new since the last export: tape, trades, state, logs "
             "(no keys); for `tbot import` on another machine")
    sp.add_argument("--full", action="store_true", help="everything, not only what is new since the last export")
    sp.add_argument("--days", type=int, help="a small one: all state and trades, logs and tape of the last N UTC days")
    sp.add_argument("--since", help="the same from a UTC day on: 2026-10-01")
    sp.add_argument("--no-tape", action="store_true", help="leave the market tape out (state, trades and logs only)")
    sp.add_argument("--out", help="folder to write it to (default: treading-bot/exports)")
    sp.add_argument("--keep", type=int, default=3, help="export files kept in that folder (default 3; 0 = all)")
    sp.add_argument("--tag", help="a word for the file name, e.g. tokyo -> tb-tokyo-20261006-1612Z.tar")
    sp.add_argument("--no-probe", action="store_true", help="skip timing the connection to the venues")
    sp.add_argument("--force", action="store_true", help="export even if it leaves under 6 GB of free disk")
    sp = add("import", cmd_import, "take in a file made by `tbot export`: check it, merge the tape, unpack the rest")
    sp.add_argument("path", nargs="?", help="the tb-*.tar file or its folder (default: the newest in "
                                            "treading-bot/exports or ~/Downloads)")
    sp = add("sync", cmd_sync, "two machines: a trader takes the lists from the machine that makes them")
    sp.add_argument("action", nargs="?", default="status",
                    choices=["status", "key", "allow", "pull", "push", "serve", "receive"],
                    help="status; key: make the trader's key; allow KEY: let it fetch from this machine; pull: fetch "
                         "now; push user@trader: hand over lists you scanned here by hand")
    sp.add_argument("arg", nargs="?", help="allow: the public key, in quotes; push: user@host[:port]")
    sp.add_argument("--path", default="treading-bot/arcus",
                    help="push: the arcus folder on the trader (default treading-bot/arcus, from its home folder)")
    sp = add("secrets", cmd_secrets, "encrypted secrets store")
    sp.add_argument("action", choices=["init", "set", "list-redacted", "status", "import-env"])
    sp.add_argument("name", nargs="?")
    sp = add("probe", cmd_probe, "print live market params, compliance and rate budgets")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--testnet", action="store_true")
    add("region-check", cmd_region_check, "may this server's IP trade Arcus perps? (reads only)")
    return p


TBOT_DELEGATED = ("telegram", "export", "import", "sync", "recommend")
TBOT_HELP = """tbot: the commands that belong to no single bot (one install, three bots, one Telegram bot)

  tbot up [arcus] [lighter] [telegram]   start those parts; with none named, all of them (BOT_ROLE decides what a part is)
  tbot down [parts] [--all]              stop them; --all also stops their runs (quotes cancelled, positions kept)
  tbot status                            one screen for the whole machine
  tbot role [trader|recorder|scout|all]  what this machine is for (BOT_ROLE in .env): say it, or set it
  tbot scout [arcus|lighter|both]        start the scouts (--stop stops them)
  tbot telegram [--read-only]            the one Telegram bot for Arcus, Lighter and the arbitrage, in this terminal
  tbot recommend [MARKET ...] [--scan]   the best setups of each list, as the Telegram lines to paste
  tbot export / import / sync            one file with everything recorded and traded / take it in / two machines

parts: arcus = its scout (and the guardian while a live run exists), lighter = its scout, telegram = the bot, scouts =
arcus + lighter. Each bot also has its own: `arcus up|down|status|scout`, `lighter up|down|status|scout`, `arbitrage start|stop|status`.
"""


def main_tbot(argv: list[str] | None = None) -> None:
    """`tbot`: what belongs to the whole machine, not to one bot. The commands that already exist (telegram, export,
    import, sync, recommend, doctor, region-check) are the same ones `arcus` has, under a name that does not claim they
    are Arcus's."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in TBOT_DELEGATED:
        main(args)
        return
    p = argparse.ArgumentParser(prog="tbot", description=TBOT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="up|down|status|role|scout|telegram|recommend|export|import|sync")
    sp = sub.add_parser("up", help="start parts")
    sp.add_argument("parts", nargs="*", help="arcus, lighter, telegram, scouts; none = all")
    sp.set_defaults(fn=cmd_tbot_up)
    sp = sub.add_parser("down", help="stop parts")
    sp.add_argument("parts", nargs="*")
    sp.add_argument("--all", action="store_true", help="also stop the runs of those parts (positions kept)")
    sp.set_defaults(fn=cmd_tbot_down)
    sub.add_parser("status", help="the whole machine on one screen").set_defaults(fn=cmd_tbot_status)
    sp = sub.add_parser("role", help="what this machine is for: trader, recorder, scout or all")
    sp.add_argument("role", nargs="?", choices=list(roles.ROLES))
    sp.set_defaults(fn=cmd_tbot_role)
    sp = sub.add_parser("scout", help="start or stop the scouts")
    sp.add_argument("which", nargs="?", choices=["arcus", "lighter", "both"], default="both")
    sp.add_argument("--stop", action="store_true")
    sp.set_defaults(fn=cmd_tbot_scout)
    os.chdir(os.environ.get("BOT_HOME", os.getcwd()))
    load_dotenv(".env")
    a = p.parse_args(args)
    a.fn(a)


def main(argv: list[str] | None = None) -> None:
    os.chdir(os.environ.get("BOT_HOME", os.getcwd()))
    load_dotenv(".env")
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
