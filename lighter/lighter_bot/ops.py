"""Background processes: the scout and a run, each with a pid file in state/ and a log in logs/. (`tbot up` in
treading-bot/arcus starts this scout with everything else; the Telegram controls are in that one bot.)

    lighter up          the scout: recorder and scans
    lighter down        stop them (--all: the running bot too; its position is kept)
    lighter status      one screen: what runs, the run's state, the last scan
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
from typing import Any

from lighter_bot.config import ROOT, Config

SERVICES = ("scout", "run-paper", "run-live")


def pid_path(cfg: Config, name: str) -> Path:
    return cfg.state_dir / f"{name}.pid"


def alive(pid: int) -> bool:
    """True if `pid` is a live process. `os.kill(pid, 0)` alone says yes for a zombie: a child that exited but was
    never reaped. The scout and the Telegram bot start the runs and live on, so a finished run stayed "running"
    for as long as they did (found on the Arcus bot, 2026-10-03). Reaps the pid first when it is our own child."""
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


def running(cfg: Config, name: str) -> int | None:
    try:
        pid = int(pid_path(cfg, name).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if alive(pid) else None


def start(cfg: Config, name: str, args: list[str]) -> int:
    """Start `lighter <args>` detached, unless `name` already runs."""
    if os.environ.get("LBOT_NO_SPAWN") == "1":      # the tests set this: never start a real process
        raise RuntimeError(f"refusing to start {name} under tests")
    pid = running(cfg, name)
    if pid:
        return pid
    cfg.ensure_dirs()
    out = open(cfg.logs_dir / f"{name}.out", "a")   # noqa: SIM115 - handed to the child
    exe = Path(sys.executable).parent / "lighter"
    cmd = [str(exe), *args] if exe.exists() else [sys.executable, "-m", "lighter_bot.cli", *args]
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    pid_path(cfg, name).write_text(str(p.pid))
    return p.pid


def stop(cfg: Config, name: str, timeout_s: float = 20.0) -> bool:
    pid = running(cfg, name)
    if not pid:
        return False
    os.kill(pid, signal.SIGTERM)
    end = time.time() + timeout_s
    while time.time() < end:
        if not alive(pid):
            break
        time.sleep(0.2)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    pid_path(cfg, name).unlink(missing_ok=True)
    return True


def read_json(p: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def status(cfg: Config) -> dict[str, Any]:
    out: dict[str, Any] = {"services": {n: running(cfg, n) for n in SERVICES}}
    for mode in ("paper", "live"):
        st = read_json(cfg.state_dir / f"status-{mode}.json")
        if st:
            st["age_s"] = round(time.time() - float(st.get("t", 0)), 1)
            out[mode] = st
    scan = read_json(cfg.data_dir / "scout" / "latest.json")
    if scan:
        out["scan"] = {"age_min": round((time.time() - scan["t"]) / 60, 1), "capital": scan["capital"],
                       "lists": {k: [f"{r['market']} {r['setup']} @ {r['leverage']:g}x" for r in v]
                                 for k, v in scan["lists"].items()}}
    rec = read_json(cfg.data_dir / "recorder.json")
    if rec:
        out["recorder"] = rec
    return out
