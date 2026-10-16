"""Two markets held delta-neutral across Arcus and Lighter on Robinhood Chain: on Arcus one is long and the other
short, on Lighter the opposite, the same dollars of each market on both venues. Renewed every 24 hours; after each
close the bot says how much money to move between the venues and which way."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("DN_ROOT") or Path(__file__).resolve().parent.parent)
DATA = ROOT / "data"
STATE = ROOT / "state"
REPORTS = ROOT / "reports"

# The owner's low open-interest list on Lighter (16 Oct 2026: $3-4m and under), all listed on Arcus too
TOKENS = ("AMD", "INTC", "SKHY", "COIN", "PLTR", "USO", "ORCL", "CRWV")
