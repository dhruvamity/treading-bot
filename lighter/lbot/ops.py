"""Background processes: the scout, the Telegram bot and a run, each with a pid file in state/ and a log in logs/.

    lbot up          the scout and (if set up) the Telegram bot
    lbot down        stop them (--all: the running bot too; its position is kept)
    lbot status      one screen: what runs, the run's state, the last scan
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

from lbot.config import ROOT, Config

SERVICES = ("scout", "telegram", "run-paper", "run-live")


def pid_path(cfg: Config, name: str) -> Path:
    return cfg.state_dir / f"{name}.pid"


def running(cfg: Config, name: str) -> int | None:
    try:
        pid = int(pid_path(cfg, name).read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def start(cfg: Config, name: str, args: list[str]) -> int:
    """Start `lbot <args>` detached, unless `name` already runs."""
    if os.environ.get("LBOT_NO_SPAWN") == "1":      # the tests set this: never start a real process
        raise RuntimeError(f"refusing to start {name} under tests")
    pid = running(cfg, name)
    if pid:
        return pid
    cfg.ensure_dirs()
    out = open(cfg.logs_dir / f"{name}.out", "a")   # noqa: SIM115 - handed to the child
    exe = Path(sys.executable).parent / "lbot"
    cmd = [str(exe), *args] if exe.exists() else [sys.executable, "-m", "lbot.cli", *args]
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
        try:
            os.kill(pid, 0)
        except OSError:
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
