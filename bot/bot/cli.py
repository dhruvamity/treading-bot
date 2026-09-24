"""Bot command line (`bot <command>`). Run `bot -h` for the list.

    bot sessions                  what can run
    bot doctor arcus_btc_mm       is everything ready for live? (reads only)
    bot run arcus_btc_mm          paper: live market data, simulated orders
    bot run arcus_btc_mm --live   real money: doctor must pass, then you type LIVE
    bot status                    what is running

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

from bot.common.config import (
    load_app,
    load_arcus_config,
    load_lighter_config,
    load_session,
    load_universe,
)
from bot.common.logging import setup_logging
from bot.common.secrets import KNOWN_SECRETS, SecretStore, load_dotenv, mask


def _run(coro: Any) -> Any:
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    return asyncio.run(coro)


def _confirm(what: str) -> None:
    print(what)
    if input("Type CONFIRM to proceed: ").strip() != "CONFIRM":
        print("aborted")
        sys.exit(1)


# ---------------------------------------------------------------------------------------------- data
def cmd_record(a: argparse.Namespace) -> None:
    from bot.core.alerts import alerter_from_secrets
    from bot.research.recorder.recorder import Recorder

    app = load_app()
    setup_logging(app.logs_dir)
    uni = load_universe()
    rec = Recorder(a.markets or uni.record, load_arcus_config(), load_lighter_config(), data_dir=Path(a.data or app.data_dir),
                   alerter=alerter_from_secrets(SecretStore()), n_levels=uni.book_levels)

    async def go() -> None:
        if a.seconds:
            await rec.run_for(a.seconds)
        else:
            await rec.start()
            stop = asyncio.Event()
            import signal

            loop = asyncio.get_running_loop()
            for s in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(s, stop.set)
            await stop.wait()
            await rec.stop()
        print(json.dumps({"rows": dict(rec.writer.rows_written), "bytes": dict(rec.writer.bytes_written)}, indent=1))

    _run(go())


def cmd_load_history(a: argparse.Namespace) -> None:
    from bot.research.loaders.history import load_all_funding

    app = load_app()
    setup_logging(app.logs_dir, to_stdout=False)
    print(json.dumps(_run(load_all_funding(a.markets or load_universe().record, Path(a.data or app.data_dir))), indent=1))


def cmd_dq_report(a: argparse.Namespace) -> None:
    from bot.research.eval.dq import dq_report

    app = load_app()
    p = dq_report(Path(a.data or app.data_dir), a.date, Path(app.reports_dir))
    print(f"wrote {p}")


def cmd_compact(a: argparse.Namespace) -> None:
    from bot.research.recorder.writer import compact_closed_days

    app = load_app()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    print(compact_closed_days(Path(a.data or app.data_dir), today))


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
        print("name at least one session, e.g. `bot run arcus_btc_mm` (see `bot sessions`)")
        sys.exit(2)
    out = [load_session(resolve_session(n)) for n in names]
    if getattr(a, "mode", None):
        from bot.common.config import MMSession

        for s in out:
            if isinstance(s, MMSession):
                s.mode = a.mode
    return out


def _run_mode(a: argparse.Namespace) -> Any:
    from bot.core.livelock import RunMode

    return RunMode.LIVE if getattr(a, "live", False) else RunMode.TESTNET if getattr(a, "testnet", False) else RunMode.PAPER


def _describe(s: Any) -> str:
    from bot.common.config import MMSession

    if isinstance(s, MMSession):
        acct = f"sub {s.account_index}" if s.venue == "arcus" else "acct *"
        return (f"{s.session_id:<20} {s.venue:<10} {acct:<6} {s.market:<6} mode={s.mode:<6} "
                f"capital ${s.capital_usd:g}  lev {s.leverage_max:g}x  inv cap ${s.inventory_cap_usd:g}  "
                f"live_enabled={s.live_enabled}")
    return (f"{s.session_id:<20} DN {s.strategy:<12} {s.market:<6} ${s.collateral_per_leg_usd:g}/leg  "
            f"lev {s.leverage_per_leg:g}x  live_enabled={s.live_enabled}")


def cmd_sessions(a: argparse.Namespace) -> None:
    for p in sorted(SESSIONS_DIR.glob("*.yaml")):
        try:
            print(_describe(load_session(p)))
        except Exception as e:
            print(f"{p.stem:<20} INVALID: {e}")


async def _doctor(sessions: list[Any], mode: Any, *, adopt: bool = False, account_index: int | None = None) -> Any:
    from bot.core.calendar import TradingCalendar
    from bot.core.doctor import run_doctor
    from bot.core.livelock import RunMode
    from bot.core.liveparams import LiveParams
    from bot.venues.arcus.rest import ArcusRest
    from bot.venues.lighter_rh.rest import LighterRest

    acfg, lcfg = load_arcus_config(), load_lighter_config()
    acfg.env = lcfg.env = "testnet" if mode is RunMode.TESTNET else "mainnet"
    ar, lr = ArcusRest(acfg.rest_url()), LighterRest(lcfg.rest_url())
    try:
        lp = LiveParams(arcus=ar, lighter=lr)
        await lp.refresh()
        return await run_doctor(sessions, mode=mode, app=load_app(), secrets=SecretStore(),
                                calendar=TradingCalendar.load(), arcus_rest=ar, lighter_rest=lr, markets=lp.markets,
                                adopt_positions=adopt, account_index=account_index)
    finally:
        await ar.close()
        await lr.close()


def cmd_doctor(a: argparse.Namespace) -> None:
    from bot.core.livelock import RunMode

    sessions = [load_session(resolve_session(n)) for n in a.sessions]
    mode = RunMode.TESTNET if a.testnet else RunMode.PAPER if a.paper else RunMode.LIVE
    print(f"checking {'credentials and account' if not sessions else ', '.join(s.session_id for s in sessions)} "
          f"for a {mode.value} run\n")
    rep = _run(_doctor(sessions, mode, adopt=a.adopt_positions))
    print(rep.render())
    sys.exit(1 if rep.failed else 0)


# ---------------------------------------------------------------------------------------------- trading
def cmd_run(a: argparse.Namespace) -> None:
    from bot.common.errors import BotError
    from bot.core.calendar import TradingCalendar
    from bot.core.livelock import RunMode
    from bot.core.runner import BotRunner

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
            print(f"not starting: fix the FAIL lines above (re-check with `bot doctor {' '.join(s.session_id for s in sessions)}`)")
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
                           lighter_cfg=load_lighter_config(), secrets=SecretStore(), calendar=TradingCalendar.load(),
                           state_db=a.state_db, typed_confirmation=typed)
        _run(runner.run(a.seconds))
    except BotError as e:
        print(f"stopped: {e}")
        sys.exit(2)


def cmd_status(a: argparse.Namespace) -> None:
    from bot.core.heartbeat import heartbeat_process_alive, read_heartbeat_age_s
    from bot.core.state import StateStore

    app = load_app()
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
    from bot.core.creds import (
        arcus_address,
        arcus_private_keys,
        discover_arcus_keys,
        key_for_account,
        resolve_lighter,
    )
    from bot.core.liveparams import LiveParams
    from bot.venues.base import Venue

    s = SecretStore()
    acfg, lcfg = load_arcus_config(), load_lighter_config()
    acfg.env = lcfg.env = "mainnet" if mainnet else "testnet"
    if venue == "arcus":
        from bot.venues.arcus.adapter import ArcusAdapter
        from bot.venues.arcus.rest import ArcusRest
        from bot.venues.arcus.signing import ArcusSigner

        pub = ArcusRest(acfg.rest_url())
        address = arcus_address(s, not mainnet)
        keys = await discover_arcus_keys(pub, address, arcus_private_keys(s, not mainnet))
        if account_index is None:
            active = [k for k in keys if k.active]
            account_index = active[0].account_index if active else 0
        key = key_for_account(keys, int(account_index or 0))
        lp = LiveParams(arcus=pub)
        await lp.refresh()
        rest = ArcusRest(acfg.rest_url(), signer=ArcusSigner(key.private_key), address=address, writes_allowed=True,
                         is_mainnet=mainnet)
        await pub.close()
        return ArcusAdapter(rest, None, address=address, account_index=int(account_index or 0),
                            markets=lp.markets[Venue.ARCUS])
    from bot.venues.lighter_rh.adapter import LighterAdapter
    from bot.venues.lighter_rh.auth import AuthTokenManager
    from bot.venues.lighter_rh.nonce import NonceManager
    from bot.venues.lighter_rh.rest import LighterRest
    from bot.venues.lighter_rh.signer import LighterSigner

    lr = LighterRest(lcfg.rest_url())
    lc = await resolve_lighter(lr, s, not mainnet)
    signer = LighterSigner(url=lcfg.rest_url(), chain_id=lcfg.chain(), account_index=lc.account_index,
                           api_key_index=lc.api_key_index, private_key_hex=lc.private_key)
    lp = LiveParams(lighter=lr)
    await lp.refresh()
    return LighterAdapter(LighterRest(lcfg.rest_url(), writes_allowed=True), None, signer=signer,
                          nonces=NonceManager(Path("state") / f"lighter_nonce_manual_{lc.account_index}_{lc.api_key_index}"),
                          auth=AuthTokenManager(signer), account_index=lc.account_index,
                          markets=lp.markets[Venue.LIGHTER_RH])


async def venue_cancel_all(venue: str, account: int | None, mainnet: bool, market: str | None = None) -> str:
    """Cancel every open order on a venue account (shared by the CLI and the Telegram bot)."""
    ad = await _venue_adapter(venue, account, mainnet)
    try:
        await ad.cancel_all(market)
        return (f"cancel-all sent to {venue} {'MAINNET' if mainnet else 'testnet'} "
                f"account {getattr(ad, 'account_index', '?')}")
    finally:
        await ad.close()


async def venue_flatten(venue: str, account: int | None, mainnet: bool, taker: bool) -> str:
    """Cancel everything, then close every position with reduce-only orders (maker at the touch, or IOC)."""
    from bot.common.ids import ClientIdFactory
    from bot.core.guardian import flatten_orders
    from bot.venues.base import Venue

    ad = await _venue_adapter(venue, account, mainnet)
    try:
        await ad.cancel_all(None)
        pos = await ad.positions()
        mk = {m.base: m for m in await ad.markets()}
        orders = flatten_orders(pos, mk, {p.base: p.mark_price for p in pos}, ClientIdFactory("manual", 1), Venue(venue),
                                taker=taker)
        if orders:
            await ad.place(orders)
        return f"sent {len(orders)} reduce-only {'IOC' if taker else 'maker'} orders on {venue}"
    finally:
        await ad.close()


def cmd_cancel_all(a: argparse.Namespace) -> None:
    net = "testnet" if a.testnet else "MAINNET"
    if not a.testnet and not a.yes:
        _confirm(f"cancel ALL open orders on {a.venue} ({net}, account {a.account if a.account is not None else 'of your key'})")
    print(_run(venue_cancel_all(a.venue, a.account, not a.testnet, a.market)))


def cmd_flatten(a: argparse.Namespace) -> None:
    if not a.testnet:
        _confirm(f"FLATTEN every position on {a.venue} MAINNET with reduce-only orders ({'IOC' if a.taker else 'maker'})")
    print(_run(venue_flatten(a.venue, a.account, not a.testnet, a.taker)))


def cmd_selftest(a: argparse.Namespace) -> None:
    from bot.common.errors import BotError
    from bot.core.selftest import run_selftest
    from bot.venues.arcus.rest import ArcusRest
    from bot.venues.arcus.ws import ArcusWS

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
    from bot.core.state import StateStore

    app = load_app()
    st = StateStore(a.state_db or app.state_db_for(a.mode))
    st.kv_set("resume", json.dumps({"venue": a.venue, "all": a.all, "ts": time.time()}))
    print(f"resume flag written for the {a.mode} bot; it clears safe mode / stops on its next tick")


def cmd_report(a: argparse.Namespace) -> None:
    app = load_app()
    p = app.reports_for(a.mode) / "daily" / f"{a.date}.md"
    print(p.read_text() if p.exists() else f"no {a.mode} report for {a.date} at {p}")


def cmd_keys(a: argparse.Namespace) -> None:
    from bot.core.creds import arcus_address, arcus_private_keys, discover_arcus_keys
    from bot.venues.arcus.rest import ArcusRest

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
    from bot.core.alerts import alerter_from_secrets
    from bot.core.guardian import GuardedVenue, Guardian
    from bot.venues.base import Venue

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
    from bot.telegram.api import TelegramAPI
    from bot.telegram.bot import TelegramBot
    from bot.telegram.control import Control

    app = load_app()
    setup_logging(app.logs_dir)
    s = SecretStore()
    tok, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first (OWNER_ACTIONS.md, step 9)")
        sys.exit(2)
    allowed = {int(x) for x in (s.get("TELEGRAM_ALLOWED_USER_IDS") or "").replace(" ", "").split(",") if x}
    api = TelegramAPI(tok)
    from bot.scout.pilot import Pilot

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


def cmd_scout(a: argparse.Namespace) -> None:
    """Record every Arcus perp, backtest the strategy menu on each, rank what to run now."""
    from bot.scout.scan import scan, table
    from bot.scout.service import run_service, save_scan

    app = load_app()
    setup_logging(app.logs_dir)
    root = Path.cwd()
    if a.action == "run":
        from bot.scout.pilot import Pilot
        from bot.telegram.control import Control

        cfg = load_arcus_config()
        pid = Path(app.state_dir) / "scout.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text(str(os.getpid()))
        print(f"scout: recording all Arcus perps, scanning every {a.every_min:g} min; Ctrl-C stops it")
        try:
            _run(run_service(root, Pilot(root, Control(app)), rest_url=cfg.rest.mainnet, ws_url=cfg.ws.mainnet,
                             every_min=a.every_min, workers=a.workers, ladder=not a.max_only, depth=a.depth))
        finally:
            with contextlib.suppress(OSError):
                if pid.read_text() == str(os.getpid()):
                    pid.unlink()
    elif a.action == "scan":
        res = scan(root / "data" / "scout", workers=a.workers, markets=a.markets or None, ladder=not a.max_only)
        save_scan(root, res)
        print(table(res, a.limit))
    elif a.action == "import":
        from bot.scout.bootstrap import import_arcusmm
        from bot.scout.tape import TapeStore

        rows = import_arcusmm(Path(a.src), TapeStore(root / "data" / "scout" / "tape"), workers=a.workers)
        print(f"imported {len(rows)} market-day files from {a.src}")


def cmd_pilot(a: argparse.Namespace) -> None:
    """One deployment at a time: status, approve one of the scout's top 3, close."""
    from bot.scout.pilot import Pilot, describe
    from bot.telegram.control import Control

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
        scan = pilot.latest_scan()
        print("\ntop 3 now:" if scan and scan.get("top") else "\nnothing passes all checks right now")
        for i, c in enumerate((scan or {}).get("top") or [], 1):
            print(f"  {i}. {describe(c)}")
    elif a.action == "approve":
        c = pilot.pick(a.n)
        print(describe(c))
        if a.live:
            if os.environ.get("BOT_PILOT_LIVE") != "1":
                print("live is off: set BOT_PILOT_LIVE=1 in .env first")
                sys.exit(2)
            if input("REAL MONEY. Type LIVE to deploy: ").strip() != "LIVE":
                print("aborted")
                sys.exit(1)
        print(_run(pilot.approve(a.n, live=a.live, by="cli")))
    elif a.action == "close":
        print(_run(pilot.close(by="cli")))


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


def cmd_points(a: argparse.Namespace) -> None:
    from bot.core.state import StateStore

    app = load_app()
    st = StateStore(a.state_db or app.state_db_for("live"))
    if a.action == "add":
        st.add_points(a.venue, a.week, a.points)
    for v, w, p in st.points():
        print(v, w, p)


def cmd_probe(a: argparse.Namespace) -> None:
    from bot.core.liveparams import LiveParams
    from bot.venues.arcus.rest import ArcusRest
    from bot.venues.base import Venue
    from bot.venues.lighter_rh.rest import LighterRest

    async def go() -> None:
        acfg, lcfg = load_arcus_config(), load_lighter_config()
        acfg.env = lcfg.env = "testnet" if a.testnet else "mainnet"
        lp = LiveParams(arcus=ArcusRest(acfg.rest_url()), lighter=LighterRest(lcfg.rest_url()))
        await lp.refresh()
        v = Venue(a.venue)
        for b in a.markets or sorted(lp.markets[v])[:10]:
            m = lp.markets[v].get(b)
            if m:
                print(f"{v.value} {b:8s} id={m.venue_market_id:<4} tick={m.tick_size} step={m.step_size} "
                      f"min=${m.min_notional}/{m.min_size} imf={m.imf} mmf={m.mmf} maxlev={m.max_leverage} "
                      f"fees={m.maker_fee}/{m.taker_fee} status={m.status}")
        if a.venue == "arcus" and a.testnet is False:
            print("compliance:", await ArcusRest(acfg.rest_url()).compliance())
        s = SecretStore()
        addr = s.get("ARCUS_ADDRESS" if not a.testnet else "ARCUS_TESTNET_ADDRESS")
        if a.venue == "arcus" and addr:
            r = ArcusRest(acfg.rest_url())
            for idx in (0, 1, 2):
                try:
                    print(f"sub{idx}", await r.rate_limit(addr, idx))
                except Exception as e:
                    print(f"sub{idx} rateLimit: {e}")

    _run(go())


def cmd_backtest(a: argparse.Namespace) -> None:
    from bot.research.eval.backtest import run_backtest

    app = load_app()
    setup_logging(app.logs_dir, to_stdout=False)
    res = run_backtest([load_session(p) for p in a.session], Path(a.data or app.data_dir), start=a.start, end=a.end,
                       fill_mode=a.fill_mode, lighter_maker_ms=a.lighter_maker_ms)
    print(json.dumps(res, indent=1, default=str))


def cmd_carry_study(a: argparse.Namespace) -> None:
    from bot.research.diagnostics.carry import carry_study

    app = load_app()
    p = carry_study(Path(a.data or app.data_dir), a.markets or load_universe().record, Path(app.reports_dir))
    print(f"wrote {p}")


def cmd_diagnostics(a: argparse.Namespace) -> None:
    from bot.research.diagnostics.microstructure import diagnostics_report

    app = load_app()
    p = diagnostics_report(Path(a.data or app.data_dir), a.markets or load_universe().record, Path(app.reports_dir))
    print(f"wrote {p}")


def cmd_gonogo(a: argparse.Namespace) -> None:
    from bot.research.eval.gonogo import gonogo_report

    app = load_app()
    p = gonogo_report(Path(a.data or app.data_dir), Path(app.reports_dir), sessions=[load_session(s) for s in a.session],
                      seed=a.seed)
    print(f"wrote {p}")


def cmd_region_check(a: argparse.Namespace) -> None:
    import runpy

    runpy.run_path(str(Path(__file__).parents[1] / "deploy" / "scripts" / "region_check.py"), run_name="__main__")


# ---------------------------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bot", description="Arcus trading bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn: Any, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("record", cmd_record, "run the market-data recorder (public data only)")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--data")
    sp.add_argument("--seconds", type=float)
    sp = add("load-history", cmd_load_history, "load historical funding for both venues")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--data")
    sp = add("dq-report", cmd_dq_report, "data-quality report for a UTC date")
    sp.add_argument("--date", default=time.strftime("%Y-%m-%d", time.gmtime()))
    sp.add_argument("--data")
    sp = add("compact", cmd_compact, "merge closed days' Parquet part files")
    sp.add_argument("--data")
    modes = ("mid", "grid", "rgrid", "dgrid", "blend", "signal", "auto")
    add("sessions", cmd_sessions, "list the session files: venue, subaccount, market, mode, capital, live_enabled")
    sp = add("doctor", cmd_doctor, "check everything a live run needs (credentials, account, sizing, clock); no orders")
    sp.add_argument("sessions", nargs="*", help="session names (default: credentials and account only)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="check for a paper run instead of live")
    g.add_argument("--testnet", action="store_true")
    sp.add_argument("--adopt-positions", action="store_true", help="an existing position is the bot's to manage")
    sp = add("run", cmd_run, "run sessions: paper by default, --live for real money")
    sp.add_argument("sessions", nargs="*", help="session names, e.g. arcus_btc_mm (see `bot sessions`)")
    sp.add_argument("--session", action="append", help=argparse.SUPPRESS)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="simulated orders on live market data (default)")
    g.add_argument("--testnet", action="store_true", help="real orders on the venue testnet")
    g.add_argument("--live", action="store_true", help="REAL orders: runs doctor, then asks you to type LIVE")
    sp.add_argument("--mode", choices=modes, help="override the strategy mode in the session file for this run")
    sp.add_argument("--yes", action="store_true",
                    help="unattended live start (systemd): skips the prompt, needs live_enabled: true in every session")
    sp.add_argument("--adopt-positions", action="store_true", help="an existing position is the bot's to manage")
    sp.add_argument("--seconds", type=float, help="stop after this long")
    sp.add_argument("--state-db", help=argparse.SUPPRESS)
    sp = add("status", cmd_status, "what is running per mode: heartbeat, open orders, positions")
    sp.add_argument("--mode", choices=["live", "testnet", "paper"])
    sp = add("selftest", cmd_selftest, "prove Arcus accepts every signed request the bot sends (no trading)")
    sp.add_argument("--account", type=int, help="subaccount (default: the one your key is bound to)")
    sp.add_argument("--market", default="BTC")
    sp.add_argument("--offset-pct", type=float, default=3.0, help="test orders sit this far below the bid")
    sp.add_argument("--allow-funded", action="store_true", help="also run the order steps on a funded account")
    sp.add_argument("--testnet", action="store_true")
    for name, fn, h in (("cancel-all", cmd_cancel_all, "cancel all open orders (mainnet unless --testnet)"),
                        ("flatten", cmd_flatten, "close all positions with reduce-only orders (mainnet unless --testnet)")):
        sp = add(name, fn, h)
        sp.add_argument("--venue", choices=["arcus", "lighter_rh"], required=True)
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
    sp.add_argument("action", choices=["run", "scan", "import"],
                    help="run: record + scan every N min (the daemon); scan: one scan now; import: old recordings")
    sp.add_argument("src", nargs="?", default="../../arcus-mm", help="import: the arcus-mm folder")
    sp.add_argument("--every-min", type=float, default=30)
    sp.add_argument("--workers", type=int, default=4)
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--max-only", action="store_true",
                    help="test each market at its maximum leverage only (default: the max, then 20x, 10x, 5x, 2x)")
    sp.add_argument("--depth", action="store_true",
                    help="run: also record the top 10 book levels (queue-position data for larger orders; ~2x the disk)")
    sp = add("pilot", cmd_pilot, "one deployment at a time: status, approve N [--live], close")
    sp.add_argument("action", choices=["status", "approve", "close"])
    sp.add_argument("n", nargs="?", type=int, default=1, help="approve: which of the top 3")
    sp.add_argument("--live", action="store_true", help="real money (needs BOT_PILOT_LIVE=1 and typing LIVE)")
    sp = add("resume", cmd_resume, "clear safe mode / stops (after investigation)")
    sp.add_argument("--venue")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--mode", choices=["live", "testnet", "paper"], default="live")
    sp.add_argument("--state-db", help=argparse.SUPPRESS)
    sp = add("report", cmd_report, "print a daily report")
    sp.add_argument("--date", default=time.strftime("%Y-%m-%d", time.gmtime()))
    sp.add_argument("--mode", choices=["live", "testnet", "paper"], default="live")
    sp = add("keys", cmd_keys, "your API keys as the venue sees them: subaccount, status, expiry")
    sp.add_argument("--testnet", action="store_true")
    sp = add("guardian", cmd_guardian, "run the independent guardian process")
    sp.add_argument("--venue", nargs="+", default=["arcus"])
    sp.add_argument("--account", type=int, help="subaccount (default: the one your key is bound to)")
    sp.add_argument("--capital", type=float, default=100)
    sp.add_argument("--testnet", action="store_true")
    sp = add("secrets", cmd_secrets, "encrypted secrets store")
    sp.add_argument("action", choices=["init", "set", "list-redacted", "status", "import-env"])
    sp.add_argument("name", nargs="?")
    sp = add("points", cmd_points, "record weekly Lighter points for the S4 study")
    sp.add_argument("action", choices=["add", "list"])
    sp.add_argument("--venue", default="lighter_rh")
    sp.add_argument("--week")
    sp.add_argument("--points", type=float)
    sp.add_argument("--state-db")
    sp = add("probe", cmd_probe, "print live market params, compliance and rate budgets")
    sp.add_argument("--venue", choices=["arcus", "lighter_rh"], default="arcus")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--testnet", action="store_true")
    sp = add("backtest", cmd_backtest, "replay recorded data through the simulator")
    sp.add_argument("--session", action="append", required=True)
    sp.add_argument("--start")
    sp.add_argument("--end")
    sp.add_argument("--data")
    sp.add_argument("--fill-mode", choices=["pessimistic", "optimistic"], default="pessimistic")
    sp.add_argument("--lighter-maker-ms", type=float, default=200)
    sp = add("carry-study", cmd_carry_study, "DN funding/basis carry study on funding history")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--data")
    sp = add("diagnostics", cmd_diagnostics, "microstructure diagnostics on recorded data")
    sp.add_argument("--markets", nargs="*")
    sp.add_argument("--data")
    sp = add("gonogo", cmd_gonogo, "write reports/GO_NO_GO.md")
    sp.add_argument("--session", action="append", default=[])
    sp.add_argument("--data")
    sp.add_argument("--seed", type=int, default=7)
    add("region-check", cmd_region_check, "print this server's IP, country and venue access")
    return p


def main(argv: list[str] | None = None) -> None:
    os.chdir(os.environ.get("BOT_HOME", os.getcwd()))
    load_dotenv(".env")
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
