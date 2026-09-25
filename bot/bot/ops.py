"""`bot up`, `bot down`, `bot status`: run everything a machine needs with one command.

Background services (each a normal `bot` command, detached, logging to logs/<name>.out, pid in state/<name>.pid):
- scout:    `bot scout run --depth` records every Arcus market and ranks setups (always);
- telegram: `bot telegram`, the phone control bot (when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are in .env);
- guardian: `bot guardian`, which cancels everything if the LIVE bot goes silent (only while a live bot runs:
            with no live bot it would fire at once).
The trading bot itself is started by the pilot (`bot pilot approve 1 [--live]`, or Telegram's /top3 -> Run) and is
not a service here; `bot down --all` stops it too (quotes cancelled, positions kept).
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.common.config import AppConfig


@dataclass(frozen=True)
class Service:
    name: str
    args: tuple[str, ...]
    what: str
    stop_wait_s: float


SERVICES = (
    Service("scout", ("scout", "run", "--depth"), "records every market, ranks setups every 30 min", 240.0),
    Service("telegram", ("telegram",), "phone control bot", 20.0),
    Service("guardian", ("guardian",), "cancels everything if the live bot goes silent", 20.0),
)
BY_NAME = {s.name: s for s in SERVICES}


def bot_bin() -> str:
    return str(Path(sys.executable).with_name("bot"))


def pid_path(app: AppConfig, name: str) -> Path:
    return Path(app.state_dir) / f"{name}.pid"


def pid_of(app: AppConfig, name: str) -> int | None:
    """The service's pid if that process is alive."""
    try:
        pid = int(pid_path(app, name).read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def uptime_s(app: AppConfig, name: str) -> float | None:
    try:
        return time.time() - pid_path(app, name).stat().st_mtime
    except OSError:
        return None


def wanted(app: AppConfig, env: dict[str, str], live_running: bool) -> dict[str, str]:
    """{service: why} for what should run here now; a missing key means skipped (the reason is in `skipped`)."""
    out = {"scout": "always"}
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out["telegram"] = "Telegram is set up in .env"
    if live_running:
        out["guardian"] = "a live bot is running"
    return out


def skipped(env: dict[str, str], live_running: bool) -> dict[str, str]:
    out = {}
    if not (env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")):
        out["telegram"] = "no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env"
    if not live_running:
        out["guardian"] = "no live bot running (it starts with one)"
    return out


def start(app: AppConfig, name: str) -> tuple[bool, str]:
    """Start one service in the background unless it already runs. (started?, message)."""
    s = BY_NAME[name]
    pid = pid_of(app, name)
    if pid:
        return False, f"{name}: already running (pid {pid})"
    Path(app.logs_dir).mkdir(parents=True, exist_ok=True)
    Path(app.state_dir).mkdir(parents=True, exist_ok=True)
    log = Path(app.logs_dir) / f"{name}.out"
    with log.open("ab") as fh:
        p = subprocess.Popen([bot_bin(), *s.args], stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    pid_path(app, name).write_text(str(p.pid))
    time.sleep(1.0)
    if p.poll() is not None:
        tail = log.read_text(errors="ignore").splitlines()[-5:]
        return False, f"{name}: exited at once ({p.returncode}); last lines of {log}:\n  " + "\n  ".join(tail)
    return True, f"{name}: started (pid {p.pid}), log {log}"


def stop(app: AppConfig, name: str) -> tuple[bool, str]:
    """SIGTERM, then wait for a clean exit (the scout finishes its scan and writes out its buffers)."""
    pid = pid_of(app, name)
    if not pid:
        with contextlib.suppress(OSError):
            pid_path(app, name).unlink()
        return False, f"{name}: not running"
    os.kill(pid, signal.SIGTERM)
    t0 = time.time()
    while time.time() - t0 < BY_NAME[name].stop_wait_s:
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)   # reap it if this process started it (else it lingers as a zombie)
        if pid_of(app, name) is None:
            with contextlib.suppress(OSError):
                pid_path(app, name).unlink()
            return True, f"{name}: stopped"
        time.sleep(1.0)
    return False, (f"{name}: still stopping after {BY_NAME[name].stop_wait_s:.0f} s (pid {pid}); it exits when its "
                   "current work is done")


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    return f"{s // 86400}d {s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h {s % 3600 // 60}m" \
        if s >= 3600 else f"{s // 60}m {s % 60}s"


def dashboard(app: AppConfig, env: dict[str, str], root: Path) -> str:
    """One screen: the services, the trading bot, what is deployed, the last scan, the balance."""
    from bot.core.balances import BalanceLog, pnl
    from bot.telegram.control import Control

    ctl = Control(app, root=root)
    live = ctl.is_running("live")
    lines = ["SERVICES"]
    skip = skipped(env, live)
    for s in SERVICES:
        pid = pid_of(app, s.name)
        state = f"running {ago(uptime_s(app, s.name))} (pid {pid})" if pid else \
            f"off: {skip[s.name]}" if s.name in skip else "STOPPED (bot up starts it)"
        lines.append(f"  {s.name:<9} {state:<46} {s.what}")
    lines += ["", "TRADING BOT"]
    modes = [m for m in ("live", "paper") if ctl.is_running(m)]
    if not modes:
        lines.append("  not running. Start one: bot pilot approve 1 (paper) or bot pilot approve 1 --live")
    for m in modes:
        v = ctl.view(m)
        snap = v.snapshot or {}
        for sess in snap.get("sessions") or []:
            lines.append(f"  {m.upper():<6} {sess.get('market', '?'):<6} session PnL ${float(sess.get('pnl') or 0):+.2f}"
                         f" · sizing for ${float(sess.get('size_capital') or sess.get('capital') or 0):,.2f}")
        pos = ", ".join(f"{k.split(':')[-1]} {p}" for k, p in v.positions.items()) or "flat"
        lines.append(f"         position: {pos} · open orders: {len(v.open_orders)}"
                     + (f" · paused: {', '.join(v.paused)}" if v.paused else ""))
    try:
        st = json.loads((Path(app.state_dir) / "pilot.json").read_text())
    except (OSError, ValueError):
        st = {}
    a = st.get("active")
    lines.append(f"  deployed: {a['market']} {a['config']} ({a['mode']})" + (
        f" · PAUSED by the scout: {st['paused_by_scout']}" if st.get("paused_by_scout") else "") if a else
        "  deployed: nothing")
    lines += ["", "SCOUT"]
    try:
        scan: dict[str, Any] = json.loads((root / "data" / "scout" / "latest.json").read_text())
        cap = scan.get("capital") or {}
        usd = cap.get("usd") or (scan.get("risk") or {}).get("capital_usd") or 100
        lines.append(f"  last scan {ago(time.time() - scan['ts_us'] / 1e6)} ago at ${usd:,.2f} "
                     f"({cap.get('source', 'older scan')}); {len(scan.get('top') or [])} setups pass all checks")
        for i, c in enumerate(scan.get("top") or [], 1):
            lines.append(f"   {i}. {c['market']} {c['config']}: ${c['volume_day']:,.0f}/day, PnL {c['pnl_day']:+.2f}/day")
    except (OSError, ValueError, KeyError):
        lines.append("  no scan yet")
    lines += ["", "BALANCE"]
    last = BalanceLog(Path(app.state_dir) / "balances.jsonl").latest()
    if last:
        p = pnl(last)
        lines.append(f"  ${last['equity']:,.2f} ({ago(time.time() - last['ts'])} ago, from {last['source']})"
                     + (f" · trading PnL ${p:+,.2f}" if p is not None else ""))
    else:
        lines.append("  no reading yet (the scout reads it before each scan once ARCUS_ADDRESS is in .env)")
    return "\n".join(lines)
