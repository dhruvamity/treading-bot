"""Every test runs offline, in a temporary lighter/ tree, and can never start a real process or read the real .env."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from lbot import log

ROOT = Path(__file__).resolve().parent.parent
os.environ["LBOT_NO_SPAWN"] = "1"
log.quiet(True)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A lighter/ tree with the real config/app.yaml and an empty .env."""
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "app.yaml", tmp_path / "config" / "app.yaml")
    (tmp_path / ".env").write_text("")
    return tmp_path


@pytest.fixture
def cfg(root: Path):
    from lbot.config import load
    c = load(root, env={})
    c.ensure_dirs()
    return c
