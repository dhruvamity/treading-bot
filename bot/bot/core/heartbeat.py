"""Bot -> guardian heartbeat: an atomically replaced JSON file (works across processes and systemd units)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import orjson


def write_heartbeat(path: Path | str, **extra: object) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")  # per-file temp: heartbeat.live and heartbeat.paper never share one
    tmp.write_bytes(orjson.dumps({"ts_us": time.time_ns() // 1000, "pid": os.getpid(), **extra}))
    os.replace(tmp, p)


def read_heartbeat(path: Path | str) -> dict[str, object] | None:
    try:
        hb = orjson.loads(Path(path).read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None
    return hb if isinstance(hb, dict) else None


def read_heartbeat_age_s(path: Path | str, now_us: int | None = None) -> float:
    p = Path(path)
    if not p.exists():
        return float("inf")
    try:
        ts = int(orjson.loads(p.read_bytes())["ts_us"])
    except (ValueError, KeyError, orjson.JSONDecodeError):
        return float("inf")
    return ((now_us if now_us is not None else time.time_ns() // 1000) - ts) / 1e6


def heartbeat_process_alive(path: Path | str) -> bool:
    """True if the process that wrote the heartbeat still exists on this host (a crashed bot leaves a fresh-looking
    file behind for a few seconds; its pid is gone)."""
    p = Path(path)
    try:
        pid = int(orjson.loads(p.read_bytes())["pid"])
    except (OSError, ValueError, KeyError, TypeError, orjson.JSONDecodeError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True
