"""`tbot up daytrade` and `tbot down daytrade`: the paper auto-trader (bot.py) as a background process, so it runs
without a terminal that stays open. Paper only: nothing here, nor the program it starts, can send an order.

    state/paper.pid     the process, while it runs (the bot writes the same file and removes it when it stops)
    state/paper.out     what it printed
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import ROOT, STATE

MODE = "paper"
STOP_WAIT_S = 60.0          # SIGTERM lands between two minutes' updates; one update reads three markets


def pid_path(state: Path | None = None) -> Path:
    return (state or STATE) / f"{MODE}.pid"


def log_path(state: Path | None = None) -> Path:
    return (state or STATE) / f"{MODE}.out"


def alive(pid: int) -> bool:
    """True if `pid` is a live process. `os.kill(pid, 0)` alone says yes for a zombie: a child that exited but was
    never reaped (the Telegram bot starts this one and lives on). Reaps the pid first when it is our own child."""
    if pid <= 0:
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False          # our child, exited: now reaped
        if done == 0:
            return True           # our child, still running
    except ChildProcessError:
        pass                      # someone else's child
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True               # exists, owned by another user
    try:                          # exists: a zombie (state Z) waiting for its parent is not running
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    return not state.startswith("Z")


def running(state: Path | None = None) -> int | None:
    try:
        pid = int(pid_path(state).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) else None


def command() -> list[str]:
    exe = Path(sys.executable).parent / "daytrade"
    return [str(exe), "run"] if exe.exists() else [sys.executable, "-m", "daytrade.cli", "run"]


def start(state: Path | None = None) -> tuple[bool, str]:
    """Start `daytrade run` in the background unless it runs. (started?, what to tell the owner)."""
    pid = running(state)
    if pid:
        return False, f"already running (pid {pid})"
    if os.environ.get("DAYTRADE_NO_SPAWN") == "1":       # the tests set this: never start a real process
        raise RuntimeError("refusing to start the daytrade paper bot under tests")
    st = state or STATE
    st.mkdir(parents=True, exist_ok=True)
    log = log_path(st)
    with log.open("ab") as fh:
        p = subprocess.Popen(command(), cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    pid_path(st).write_text(str(p.pid))
    time.sleep(1.5)
    if p.poll() is not None:
        with contextlib.suppress(OSError):
            pid_path(st).unlink()
        tail = log.read_text(errors="ignore").splitlines()[-4:]
        return False, f"exited at once ({p.returncode}); last lines of {log}:\n  " + "\n  ".join(tail)
    return True, f"started (pid {p.pid}), log {log}"


def stop(state: Path | None = None, timeout_s: float = STOP_WAIT_S) -> tuple[bool, str]:
    """SIGTERM, then wait: the bot finishes the minute's update, writes its stop event and removes its pid file."""
    pid = running(state)
    if not pid:
        with contextlib.suppress(OSError):
            pid_path(state).unlink()
        return False, "not running"
    os.kill(pid, signal.SIGTERM)
    end = time.time() + timeout_s
    while time.time() < end and alive(pid):
        time.sleep(0.3)
    if alive(pid):
        return False, f"still stopping after {timeout_s:.0f} s (pid {pid}); it exits when this minute's update is done"
    with contextlib.suppress(OSError):
        pid_path(state).unlink()
    return True, "stopped"
