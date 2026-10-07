"""`arbitrage start` and `stop`: the executor as a background process, so the one Telegram bot (and `arcus up`)
can run it without a terminal that stays open.

    state/run-<mode>.pid     the process, while it runs
    state/run-<mode>.out     what it printed

Stopping it is not closing: the position and the venues' own stop orders stay (`arbitrage close` closes).
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from arbitrage.config import ROOT, STATE, read_env

MODES = ("paper", "live")


def pid_path(mode: str, state: Path | None = None) -> Path:
    return (state or STATE) / f"run-{mode}.pid"


def alive(pid: int) -> bool:
    """True if `pid` is a live process. A child that exited but was never reaped (a zombie) is not: the Telegram bot
    starts the executor and lives on. Reaps the pid first when it is our own child."""
    if pid <= 0:
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
        if done == 0:
            return True
    except ChildProcessError:
        pass
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    return not state.startswith("Z")


def running(mode: str, state: Path | None = None) -> int | None:
    try:
        pid = int(pid_path(mode, state).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) else None


def phase(mode: str, state: Path | None = None) -> str:
    """The executor's last written phase (flat, entering, open, exiting); "" when it never ran."""
    try:
        return str(json.loads(((state or STATE) / f"position-{mode}.json").read_text()).get("phase") or "")
    except (OSError, ValueError):
        return ""


def current_mode(state: Path | None = None) -> str:
    """The bot a command with no mode means: the live one if it runs or holds a position, else the paper one."""
    if running("live", state) or phase("live", state) not in ("", "flat"):
        return "live"
    return "paper"


def command(mode: str, collateral: dict[str, float] | None) -> list[str]:
    exe = Path(sys.executable).parent / "arbitrage"
    base = [str(exe)] if exe.exists() else [sys.executable, "-m", "arbitrage.cli"]
    if mode == "live":
        return [*base, "run", "--live", "--yes"]
    c = collateral or {}
    return [*base, "run", "--arcus", f"{c.get('arcus', 0):g}", "--lighter", f"{c.get('lighter', 0):g}"]


def start(mode: str, collateral: dict[str, float] | None = None, *, state: Path | None = None) -> tuple[bool, str]:
    """Start the executor in the background unless it runs. (started?, what to tell the owner).
    Live is refused without ARB_LIVE=1; the caller has already asked the owner (a typed LIVE, or a typed code)."""
    if mode not in MODES:
        return False, f"unknown mode {mode!r}"
    pid = running(mode, state)
    if pid:
        return False, f"{mode}: already running (pid {pid})"
    if mode == "live" and read_env().get("ARB_LIVE", "").strip() != "1":
        return False, "LIVE is off: put ARB_LIVE=1 in arcus/.env first. Nothing was started."
    c = collateral or {}
    if mode == "paper" and (c.get("arcus", 0) <= 0 or c.get("lighter", 0) <= 0):
        return False, "paper needs the money to pretend with on both venues, for example 120 and 120"
    if os.environ.get("ARB_NO_SPAWN") == "1":       # the tests set this: never start a real process
        raise RuntimeError(f"refusing to start the {mode} executor under tests")
    st = state or STATE
    st.mkdir(parents=True, exist_ok=True)
    log = st / f"run-{mode}.out"
    with log.open("ab") as fh:
        p = subprocess.Popen(command(mode, collateral), cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    pid_path(mode, st).write_text(str(p.pid))
    time.sleep(1.5)
    if p.poll() is not None:
        tail = log.read_text(errors="ignore").splitlines()[-4:]
        return False, f"{mode}: exited at once ({p.returncode}):\n" + "\n".join(tail)
    return True, f"{mode}: started (pid {p.pid}); it prints to {log.name} in arbitrage/state"


def stop(mode: str, *, state: Path | None = None, timeout_s: float = 25.0) -> tuple[bool, str]:
    """Stop the executor (SIGTERM, then wait). Its position, if any, stays with the venues' own stops."""
    pid = running(mode, state)
    if not pid:
        with contextlib.suppress(OSError):
            pid_path(mode, state).unlink()
        return False, f"{mode}: not running"
    os.kill(pid, signal.SIGTERM)
    end = time.time() + timeout_s
    while time.time() < end and alive(pid):
        time.sleep(0.3)
    if alive(pid):
        return False, f"{mode}: still stopping after {timeout_s:.0f} s (pid {pid})"
    with contextlib.suppress(OSError):
        pid_path(mode, state).unlink()
    held = phase(mode, state)
    return True, f"{mode}: stopped" + (f"; its position is still {held}: the stops stay on the venues, `close` "
                                       "needs the executor running" if held not in ("", "flat") else "")
