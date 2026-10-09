"""`arcus scout run`: record every Arcus perp, scan every `every_min` minutes, review the running deployment.

Writes data/scout/latest.json (and a copy per scan under data/scout/scans/) and hands the result to the pilot, which
pauses a deployment whose conditions changed and offers the top 3 when nothing is running. Once a day it also fetches
the stock perps' earnings dates (arcus/core/earnings.py) and adds the finished day to the autopilot's playbook
(arcus/scout/playbook.py); the autopilot (arcus/scout/autopilot.py) looks every minute and acts when the owner turned it
on (/auto).

Those are three jobs, and a machine does the ones its role gives it (arcus/common/role.py; `arcus up` picks):

    record      the tape                                         all, recorder, scout
    rank        scans, the playbook, the markets' usual levels   all, scout
    supervise   the pilot's review, the autopilot                all, trader

A trader ranks nothing, so instead of scanning it follows (`follow`): it fetches the lists from the machine that
makes them (arcus/handoff.py, when BOT_SYNC_FROM is set), reviews its run on each new scan, keeps its own market
list and prices (arcus/scout/watch.py), and says so when the other machine goes quiet.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

from arcus.common import settings
from arcus.common.config import SizingDefaults
from arcus.common.logging import Log
from arcus.common.sizing import bucket
from arcus.core import earnings
from arcus.core.balances import BalanceLog
from arcus.core.calendar import TradingCalendar
from arcus.scout import playbook as pbk
from arcus.scout import regime as rg
from arcus.scout.autopilot import Autopilot
from arcus.scout.capital import account_snapshot, choose, settle
from arcus.scout.pilot import MAX_SCAN_AGE_S, Pilot
from arcus.scout.record import ScoutRecorder
from arcus.scout.scan import ScanStopped, scan, table
from arcus.scout.tape import TapeStore
from arcus.scout.watch import MidWatch

log = Log("scout")


def save_scan(root: Path, res: dict[str, Any]) -> Path:
    """latest.json (everything; the pilot reads it), a slim copy per scan under scans/ (no per-setting list, so weeks
    of scans stay small), and report.txt plus reports/<day>.txt (the last scan of each UTC day) to read by eye."""
    d = root / "data" / "scout"
    (d / "scans").mkdir(parents=True, exist_ok=True)
    (d / "reports").mkdir(parents=True, exist_ok=True)
    t = time.gmtime(res["ts_us"] / 1e6)
    slim = {k: v for k, v in res.items() if k != "all"}
    (d / "scans" / time.strftime("%Y%m%d-%H%M.json", t)).write_text(json.dumps(slim, default=str))
    tmp = d / "latest.json.tmp"
    tmp.write_text(json.dumps(res, default=str))
    tmp.replace(d / "latest.json")
    text = f"scan at {time.strftime('%Y-%m-%d %H:%M UTC', t)}\n{table(res, 60)}\n"
    (d / "report.txt").write_text(text)
    (d / "reports" / time.strftime("%Y-%m-%d.txt", t)).write_text(text)
    return d / "latest.json"


SCAN_NOW = "scan_now"   # a file in the state folder: Telegram's /scannow asks for a scan without waiting
FIRST_SCAN_S = 10.0     # the recorder connects first
STATUS = "scan_status.json"   # data/scout: is a scan running, and how long recent scans took (Telegram's ETAs)
HISTORY = 20
FOLLOW_S = 120.0            # a trader's turn: fetch, review a new scan, check the other machine
BALANCE_EVERY_S = 1800.0    # ... and read the account this often (the scout does it before each scan)
QUIET_S = 20 * 60.0         # the other machine unreachable, or its recorder silent, for this long: say so
STALE_LISTS_S = 3 * 3600.0  # no new scan for this long while a list's pick runs: say so


def read_status(root: Path) -> dict[str, Any]:
    """{running, started, workers, history: [{ts, took_s, workers, full}]} (root: the project root)."""
    try:
        d: dict[str, Any] = json.loads((root / "data" / "scout" / STATUS).read_text())
        return d
    except (OSError, ValueError):
        return {}


def _write_status(root: Path, st: dict[str, Any]) -> None:
    p = root / "data" / "scout" / STATUS
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(p)


def eta_s(st: dict[str, Any], workers: int, full: bool) -> float | None:
    """Expected length of a scan: the median of the last five of the same kind (the once-a-day search over a new
    day, or a light one) at this many workers; failing that, the same kind at another worker count, scaled."""
    hist = [h for h in st.get("history") or [] if bool(h.get("full")) == full and h.get("took_s")]
    same = [h["took_s"] for h in hist if h.get("workers") == workers][-5:]
    if same:
        return float(sorted(same)[len(same) // 2])
    other = [h["took_s"] * max(1, h.get("workers") or 1) / max(1, workers) for h in hist[-5:]]
    return float(sorted(other)[len(other) // 2]) if other else None


def next_is_full(scan: dict[str, Any] | None, now: float) -> bool:
    """The next scan backtests a new UTC day for every setting when the last one ran on an earlier day."""
    return not scan or time.gmtime(scan["ts_us"] / 1e6)[:3] != time.gmtime(now)[:3]


def scan_workers(requested: int | str | None, bot_running: bool) -> int:
    """How many processes a scan may use: the number asked for, or all cores but one ("auto"); while a trading bot runs
    on this machine, at most all cores but two. The workers run at the lowest CPU priority (scan.lower_priority: idle
    class on Linux), so the bot always gets the CPU first; one worker, as before, made a scan at a new capital take a
    day and kept the owner from running anything while it lasted."""
    cores = os.cpu_count() or 2
    n = requested if isinstance(requested, int) and requested > 0 else max(1, cores - 1)
    return max(1, min(n, cores - 2)) if bot_running else n


async def run_service(root: Path, pilot: Pilot, *, rest_url: str, ws_url: str, every_min: float = 30.0,
                      workers: int | str | None = None, record: bool = True, ladder: bool = False, depth: bool = False,
                      capital: str | float | None = None, sizing: SizingDefaults | None = None, rank: bool = True,
                      supervise: bool = True, pull: Any = None) -> None:
    """capital: "auto" (the subaccount's equity before each scan), a fixed amount, or None for app.yaml's sizing.
    Before every scan it re-reads the owner's settings (state/settings.json, changed from Telegram), reads and logs
    the account balance (state/balances.jsonl), settles the capital (arcus/scout/capital.py) and picks the workers.
    record / rank / supervise: the three jobs (the module's note); with rank off and supervise on it follows.
    pull: a trader's fetch from the other machine (a callable returning arcus/handoff.py's state), or None."""
    base = sizing or SizingDefaults()
    state_dir = root / pilot.control.app.state_dir
    balances = BalanceLog(state_dir / "balances.jsonl")
    scout_dir = root / "data" / "scout"
    rec = ScoutRecorder(scout_dir, rest_url=rest_url, ws_url=ws_url, depth=depth) if record else None
    rec_task = asyncio.create_task(rec.run()) if rec else None
    follow = supervise and not rank
    watch = MidWatch(scout_dir, rest_url=rest_url, ws_url=ws_url,
                     wanted=lambda: sorted(pbk.load(scout_dir).get("markets") or {})) if follow and not rec else None
    watch_task = asyncio.create_task(watch.run()) if watch else None
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    halt = threading.Event()   # tells a scan in progress to stop (it runs in another thread)
    cal = TradingCalendar.for_app(state_dir, root / "config" / "calendars")
    recent = rec.recent if rec else watch.recent if watch else None
    auto_task = asyncio.create_task(Autopilot(root, pilot, calendar=cal, recent=recent,
                                              tapeless=watch is not None).run(stop)) if supervise else None
    fst: dict[str, Any] = {}   # what a following trader remembers between turns

    def shutdown() -> None:
        stop.set()
        halt.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), FIRST_SCAN_S)
        while not stop.is_set():
            t0 = time.time()
            over = settings.load(state_dir)
            every, want_workers = settings.scout_options(over)
            every = float(every or every_min)
            if not rank:
                if follow:
                    try:
                        await follow_turn(root, pilot, fst, rest_url=rest_url, balances=balances, cal=cal, pull=pull)
                    except Exception as e:   # one bad turn must not stop the autopilot beside it
                        log.error("scout_follow_failed", reason=type(e).__name__, data={"err": str(e)[:300]},
                                  exc_info=True)
                # recording only: the recorder writes and re-reads the market list by itself
                await _wait(stop, state_dir / SCAN_NOW, FOLLOW_S if follow else 3600.0)
                continue
            if rec:
                rec.flush()   # scan on data up to now
            try:
                z = settings.effective_sizing(base, over)
                spec = over.get("capital") or (z.capital_usd if capital in (None, "") else capital)
                snap = await account_snapshot(rest_url, pilot.account_index)
                if snap and snap["equity"] > 0:
                    balances.record(source="scout", account_index=pilot.account_index, equity=snap["equity"],
                                    free=snap["free"], net_deposits=snap["net_deposits"])
                eq = snap["equity"] if snap and snap["equity"] > 0 and str(spec).lower() == "auto" else None
                cap, src = choose(spec, eq, z)
                cap, src, moved = settle(state_dir, cap, src, today=time.strftime("%Y-%m-%d", time.gmtime()))
                await _earnings(state_dir, root, cal)
                a = pilot.active() if supervise else None
                n = scan_workers(want_workers, bool(pilot.control.running_modes()))
                st = read_status(root)
                _write_status(root, {**st, "running": True, "started": time.time(), "workers": n})
                res = await loop.run_in_executor(None, functools.partial(
                    scan, scout_dir, workers=n, ladder=ladder, capital=cap, pct=z.pct(),
                    capital_source=src, always={(a["market"], a["config"])} if a else None, stop=halt,
                    volume_cost=settings.volume_cost(over), lev_caps=settings.lev_caps(over),
                    budget_s=settings.scan_budget_s(over)))
                save_scan(root, res)
                hist = (st.get("history") or []) + [{"ts": time.time(), "took_s": res["took_s"], "workers": n,
                                                    "full": bool(res.get("day_jobs"))}]
                _write_status(root, {"running": False, "workers": n, "history": hist[-HISTORY:]})
                pb = pbk.load(scout_dir)
                if pbk.age_days(pb) > 20 / 24 or float(pb.get("capital") or 0) != bucket(cap) or pb.get("left_days"):
                    book = pbk.Playbook(scout_dir, capital=cap, pct=z.pct(),
                                        lev_caps=settings.lev_caps(over), calendar=cal)
                    t = await loop.run_in_executor(None, functools.partial(
                        book.build, workers=n, stop=halt, budget_s=settings.scan_budget_s(over)))
                    log.info("scout_playbook", data={"built_days": t.get("built_days"), "left": t.get("left_days"),
                                                     "markets": {m: e["days"] for m, e in t["markets"].items()}})
                if not supervise:   # a scout machine: a trader fetches the lists and has no tape for these
                    try:
                        await loop.run_in_executor(None, write_usual, scout_dir)
                    except Exception as e:
                        log.warning("scout_usual_failed", reason=f"{type(e).__name__}: {e}"[:200])
                events = pilot.review(res) if supervise else []
                log.info("scout_scan", data={"took_s": res["took_s"], "go": len(res["top"]), "capital": cap,
                                             "left_jobs": res.get("left_jobs"), "pending": len(res.get("pending") or []),
                                             "capital_moved": moved, "workers": n,
                                             "rechecked_24h": res.get("rechecked_24h"),
                                             "events": [e["kind"] for e in events]})
            except ScanStopped:
                log.info("scout_scan_stopped", reason="shutting down; finished days stay cached")
                _write_status(root, {**read_status(root), "running": False})
                break
            except Exception as e:  # a failed scan must not stop the recorder
                log.error("scout_scan_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)
                _write_status(root, {**read_status(root), "running": False})
            await _wait(stop, state_dir / SCAN_NOW, max(60.0, every * 60 - (time.time() - t0)))
    finally:
        stop.set()
        if auto_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(auto_task, 30)
        for thing, task in ((rec, rec_task), (watch, watch_task)):
            if thing and task:
                thing.stop()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, 30)
        log.info("scout_stopped")


def write_usual(scout_dir: Path, now: float | None = None) -> bool:
    """Once a day, the playbook markets' usual volatility (arcus/scout/regime.py) into data/scout/usual.json, where a
    trader fetches it: it has no tape to work them out from. True when written."""
    now = now or time.time()
    p = scout_dir / rg.USUAL_FILE
    try:
        if now - p.stat().st_mtime < 20 * 3600:
            return False
    except OSError:
        pass
    markets = sorted(pbk.load(scout_dir).get("markets") or {})
    if not markets:
        return False
    store = TapeStore(scout_dir / "tape")
    rg.save_usual(p, {m: rg.usual(store, m, int(now * 1e6)) for m in markets}, now)
    return True


async def follow_turn(root: Path, pilot: Pilot, fst: dict[str, Any], *, rest_url: str, balances: BalanceLog,
                      cal: TradingCalendar, pull: Any = None, now: float | None = None) -> list[dict[str, Any]]:
    """One turn of a trader that follows another machine's lists: fetch them, read the account, refresh the earnings
    dates, review the run on a scan it has not reviewed, and say when the other machine has gone quiet. `fst` is its
    memory between turns. Returns the events it wrote (the Telegram bot posts them)."""
    now = now or time.time()
    state_dir = root / pilot.control.app.state_dir
    out: list[dict[str, Any]] = []
    sync: dict[str, Any] | None = None
    if pull is not None:
        sync = await asyncio.get_running_loop().run_in_executor(None, pull)
    if now - float(fst.get("balance_ts") or 0) >= BALANCE_EVERY_S:
        fst["balance_ts"] = now
        snap = await account_snapshot(rest_url, pilot.account_index)
        if snap and snap["equity"] > 0:
            balances.record(source="scout", account_index=pilot.account_index, equity=snap["equity"],
                            free=snap["free"], net_deposits=snap["net_deposits"])
    await _earnings(state_dir, root, cal)
    res = pilot.latest_scan()
    ts = (res or {}).get("ts_us")
    if res and ts and ts != fst.get("reviewed"):
        first, fst["reviewed"] = "reviewed" not in fst, ts
        # the scan on disk when the service starts may be days old: judging a run by it would pause it for nothing
        if not first or now - ts / 1e6 <= MAX_SCAN_AGE_S:
            events = pilot.review(res)
            out += events
            log.info("scout_follow_review", data={"scan_age_s": round(now - ts / 1e6), "go": len(res.get("top") or []),
                                                  "events": [e["kind"] for e in events]})
    out += _quiet(pilot, fst, sync, res, now)
    return out


def _quiet(pilot: Pilot, fst: dict[str, Any], sync: dict[str, Any] | None, res: dict[str, Any] | None,
           now: float) -> list[dict[str, Any]]:
    """One alert when something a trader depends on has gone quiet, and one when it is back: the other machine
    unreachable, its recorder silent, or no new scan for hours while a list's pick runs unchecked."""
    problems: dict[str, str] = {}
    if sync is not None:
        ok = float(sync.get("last_ok") or 0)
        fst.setdefault("sync_since", now)
        if now - max(ok, float(fst["sync_since"])) > QUIET_S:
            problems["reach"] = (f"Cannot fetch the lists from {sync.get('source') or 'the other machine'}\n"
                                 f"{str(sync.get('error') or 'no answer')[:200]}")
        ages = (sync.get("remote") or {}).get("recorder_age_s") or {}
        silent = [f"{k} {float(v) / 60:.0f} min" for k, v in sorted(ages.items()) if float(v) > QUIET_S]
        if silent and "reach" not in problems:
            problems["recorder"] = "The other machine's recorder is silent: " + ", ".join(silent)
    a = pilot.active()
    listed = bool(a and a.get("profile") not in (None, "manual") and pilot.control.is_running(a["mode"]))
    if a and listed and res and now - res["ts_us"] / 1e6 > STALE_LISTS_S:
        problems["lists"] = (f"No new scan for {(now - res['ts_us'] / 1e6) / 3600:.1f} h\n"
                             f"{a['market']} · {a['config']} is running unchecked: a list's pick is paused when it "
                             "leaves its list, and nothing is checking")
    told: dict[str, str] = fst.setdefault("told", {})
    out = []
    for k, text in problems.items():
        if k not in told:
            told[k] = text
            out.append(pilot.event("alert", f"⚠️ TWO MACHINES\n{text}"))
    for k in [k for k in told if k not in problems]:
        del told[k]
        out.append(pilot.event("alert", "✅ TWO MACHINES\n" + {"reach": "The lists are arriving again",
                                                                "recorder": "The other machine is recording again",
                                                                "lists": "No list's pick is running unchecked any "
                                                                         "more"}[k]))
    return out


async def _earnings(state_dir: Path, root: Path, cal: TradingCalendar) -> None:
    """Once a day: the stock perps' earnings dates (arcus/core/earnings.py); the calendar re-reads them."""
    p = earnings.auto_path(state_dir)
    if earnings.age_h(p) < 20:
        return
    try:
        res = await earnings.refresh(state_dir, root / "data" / "scout" / "markets.json")
        log.info("scout_earnings", data=res)
        if not res.get("ok"):
            return
        cal.reload()
    except Exception as e:   # no network: the old dates stay, the scan goes on
        log.warning("scout_earnings_failed", reason=f"{type(e).__name__}: {e}"[:200])


async def _wait(stop: asyncio.Event, trigger: Path, seconds: float) -> None:
    """Until `seconds` pass, the service stops, or the trigger file appears (it is removed)."""
    end = time.time() + seconds
    while not stop.is_set() and time.time() < end:
        if take_trigger(trigger):
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), min(15.0, max(0.1, end - time.time())))


def take_trigger(trigger: Path) -> bool:
    """True once if the trigger file exists (and remove it)."""
    if not trigger.exists():
        return False
    trigger.unlink(missing_ok=True)
    return True
