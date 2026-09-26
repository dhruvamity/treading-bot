"""`bot scout run`: record every Arcus perp, scan every `every_min` minutes, review the running deployment.

Writes data/scout/latest.json (and a copy per scan under data/scout/scans/) and hands the result to the pilot, which
pauses a deployment whose conditions changed and offers the top 3 when nothing is running.
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

from bot.common import settings
from bot.common.config import SizingDefaults
from bot.common.logging import Log
from bot.core.balances import BalanceLog
from bot.scout.capital import account_snapshot, choose, settle
from bot.scout.pilot import Pilot
from bot.scout.record import ScoutRecorder
from bot.scout.scan import ScanStopped, scan, table

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
STATUS = "scan_status.json"   # data/scout: is a scan running, and how long recent scans took (Telegram's ETAs)
HISTORY = 20


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
                      capital: str | float | None = None, sizing: SizingDefaults | None = None) -> None:
    """capital: "auto" (the subaccount's equity before each scan), a fixed amount, or None for app.yaml's sizing.
    Before every scan it re-reads the owner's settings (state/settings.json, changed from Telegram), reads and logs
    the account balance (state/balances.jsonl), settles the capital (bot/scout/capital.py) and picks the workers."""
    base = sizing or SizingDefaults()
    state_dir = root / pilot.control.app.state_dir
    balances = BalanceLog(state_dir / "balances.jsonl")
    rec = ScoutRecorder(root / "data" / "scout", rest_url=rest_url, ws_url=ws_url, depth=depth) if record else None
    rec_task = asyncio.create_task(rec.run()) if rec else None
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    halt = threading.Event()   # tells a scan in progress to stop (it runs in another thread)

    def shutdown() -> None:
        stop.set()
        halt.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), 10)
        while not stop.is_set():
            t0 = time.time()
            over = settings.load(state_dir)
            every, want_workers = settings.scout_options(over)
            every = float(every or every_min)
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
                a = pilot.active()
                n = scan_workers(want_workers, bool(pilot.control.running_modes()))
                st = read_status(root)
                _write_status(root, {**st, "running": True, "started": time.time(), "workers": n})
                res = await loop.run_in_executor(None, functools.partial(
                    scan, root / "data" / "scout", workers=n, ladder=ladder, capital=cap, pct=z.pct(),
                    capital_source=src, always={(a["market"], a["config"])} if a else None, stop=halt,
                    volume_cost=settings.volume_cost(over), lev_caps=settings.lev_caps(over),
                    budget_s=settings.scan_budget_s(over)))
                save_scan(root, res)
                hist = (st.get("history") or []) + [{"ts": time.time(), "took_s": res["took_s"], "workers": n,
                                                    "full": bool(res.get("day_jobs"))}]
                _write_status(root, {"running": False, "workers": n, "history": hist[-HISTORY:]})
                events = pilot.review(res)
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
        if rec and rec_task:
            rec.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(rec_task, 30)
        log.info("scout_stopped")


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
