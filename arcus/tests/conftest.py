"""Test-wide guards.

2026-09-26: a Telegram test deployed a LIVE run through the real Pilot.deploy, which starts the guardian with
`ops.start`; that spawned a real `arcus guardian` from the repo, with the real .env, on mainnet. It outlived pytest and
sent cancel-alls to the real account. No test may start a real `bot` process: tests that exercise the spawning code
point it at a harmless command (tests/unit/test_light_scans.py) or replace ops.start / Control.start_run.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ["LBOT_NO_SPAWN"] = "1"     # the Lighter part and the funding arbitrage refuse to start a process too
os.environ["ARB_NO_SPAWN"] = "1"


@pytest.fixture(autouse=True)
def _no_real_bot_processes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real = subprocess.Popen
    refused: list[str] = []

    class Guarded(real):  # type: ignore[misc,valid-type]
        def __init__(self, args: Any, *a: Any, **kw: Any) -> None:
            argv = [str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)]
            if argv and Path(argv[0]).name in ("arcus", "lighter", "arbitrage"):
                refused.append(" ".join(argv))
                raise RuntimeError(f"a test tried to start a real bot process: {' '.join(argv)}")
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Guarded)
    yield
    assert not refused, f"a test tried to start a real bot process (replace ops.start / start_run): {refused}"
