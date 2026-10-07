"""JSON-lines logging: one object per event with a time, a level, a component and the event's fields.

`setup(logs_dir, name)` sends a process's events to logs/<name>.jsonl (rotated by day) and warnings to stderr.
Before setup (tests, one-off commands), warnings and errors go to stderr only.
"""

from __future__ import annotations

import datetime as dt
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

import orjson

_lock = threading.Lock()
_state: dict[str, Any] = {"dir": None, "name": "lighter", "day": "", "fh": None, "echo": "warn", "quiet": False}
_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40, "crit": 50}


def setup(logs_dir: Path | str, name: str, *, echo: str = "warn") -> None:
    with _lock:
        _state.update(dir=Path(logs_dir), name=name, echo=echo, day="")
        if _state["fh"] is not None:
            _state["fh"].close()
            _state["fh"] = None


def quiet(on: bool = True) -> None:
    """Tests: nothing on stderr."""
    _state["quiet"] = on


def _fh() -> TextIO | None:
    d = _state["dir"]
    if d is None:
        return None
    day = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    if day != _state["day"]:
        if _state["fh"] is not None:
            _state["fh"].close()
        d.mkdir(parents=True, exist_ok=True)
        _state["fh"] = open(d / f"{_state['name']}-{day}.jsonl", "a", buffering=1)  # noqa: SIM115
        _state["day"] = day
    return _state["fh"]


def emit(level: str, comp: str, event: str, **fields: Any) -> None:
    rec = {"t": round(time.time(), 3), "lvl": level, "c": comp, "ev": event, **fields}
    line = orjson.dumps(rec, default=str).decode()
    with _lock:
        fh = _fh()
        if fh is not None:
            fh.write(line + "\n")
        if not _state["quiet"] and _LEVELS[level] >= _LEVELS[_state["echo"]]:
            print(line, file=sys.stderr)


class Log:
    def __init__(self, comp: str) -> None:
        self.comp = comp

    def debug(self, ev: str, **kw: Any) -> None:
        emit("debug", self.comp, ev, **kw)

    def info(self, ev: str, **kw: Any) -> None:
        emit("info", self.comp, ev, **kw)

    def warn(self, ev: str, **kw: Any) -> None:
        emit("warn", self.comp, ev, **kw)

    def error(self, ev: str, **kw: Any) -> None:
        emit("error", self.comp, ev, **kw)

    def crit(self, ev: str, **kw: Any) -> None:
        emit("crit", self.comp, ev, **kw)
