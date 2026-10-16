"""`deltaneutral start` and `stop` (and `tbot up|down deltaneutral`): the bot as a background process, so it runs
without a terminal that stays open.

    state/run-<mode>.pid     the process, while it runs
    state/run-<mode>.out     what it printed

Stopping is not closing: an open cycle stays as it is, on both venues, until the bot runs again (`deltaneutral close`
closes). Live starts only from a terminal, after LIVE is typed (cli.py); `tbot` and Telegram start paper only.
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

from lighter_bot.ops import alive  # the one check: a zombie is not running

from . import ROOT, STATE
from . import venues as V

MODES = ("paper", "live")
STOP_WAIT_S = 30.0


def pid_path(mode: str, state: Path | None = None) -> Path:
    return (state or STATE) / f"run-{mode}.pid"


def log_path(mode: str, state: Path | None = None) -> Path:
    return (state or STATE) / f"run-{mode}.out"


def running(mode: str, state: Path | None = None) -> int | None:
    try:
        pid = int(pid_path(mode, state).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) else None


def phase(mode: str, state: Path | None = None) -> str:
    """The bot's last written phase (flat, opening, open, closing); "" when it never ran."""
    try:
        return str(json.loads(((state or STATE) / f"{mode}-state.json").read_text()).get("phase") or "")
    except (OSError, ValueError):
        return ""


def command(mode: str, collateral: dict[str, float] | None = None, now: bool = False) -> list[str]:
    exe = Path(sys.executable).parent / "deltaneutral"
    base = [str(exe)] if exe.exists() else [sys.executable, "-m", "deltaneutral.cli"]
    extra = ["--now"] if now else []
    if mode == "live":
        return [*base, "run", "--live", "--yes", *extra]
    c = collateral or {}
    return [*base, "run", "--arcus", f"{c.get('arcus', 100.0):g}", "--lighter", f"{c.get('lighter', 100.0):g}",
            *extra]


def start(mode: str = "paper", collateral: dict[str, float] | None = None, *, now: bool = False,
          state: Path | None = None) -> tuple[bool, str]:
    """Start the bot in the background unless it runs. (started?, what to tell the owner). Live is refused without
    DN_LIVE=1; the caller has already asked the owner (a typed LIVE)."""
    if mode not in MODES:
        return False, f"unknown mode {mode!r}"
    env = V.read_env()
    why = V.no_trading(env)
    if why:
        return False, f"not started: {why}"
    pid = running(mode, state)
    if pid:
        return False, f"{mode}: already running (pid {pid})"
    if mode == "live" and env.get("DN_LIVE", "").strip() != "1":
        return False, "LIVE is off: put DN_LIVE=1 in arcus/.env first. Nothing was started."
    if os.environ.get("DN_NO_SPAWN") == "1":        # the tests set this: never start a real process
        raise RuntimeError(f"refusing to start the delta-neutral {mode} bot under tests")
    st = state or STATE
    st.mkdir(parents=True, exist_ok=True)
    log = log_path(mode, st)
    with log.open("ab") as fh:
        p = subprocess.Popen(command(mode, collateral, now), cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    pid_path(mode, st).write_text(str(p.pid))
    time.sleep(1.5)
    if p.poll() is not None:
        with contextlib.suppress(OSError):
            pid_path(mode, st).unlink()
        tail = log.read_text(errors="ignore").splitlines()[-4:]
        return False, f"{mode}: exited at once ({p.returncode}):\n  " + "\n  ".join(tail)
    return True, f"{mode}: started (pid {p.pid}), log {log}"


def stop(mode: str = "paper", state: Path | None = None, timeout_s: float = STOP_WAIT_S) -> tuple[bool, str]:
    """SIGTERM, then wait for the bot to write its state and exit. An open cycle stays open."""
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
    ph = phase(mode, state)
    return True, f"{mode}: stopped" + (f"; its cycle is {ph} and stays so on both venues until it runs again "
                                       "(deltaneutral close closes it)" if ph not in ("", "flat") else "")
