"""Strategy registry: session config -> strategy instance. Mid and Grid, as on Tread.fi, and Smart (Mid that leaves a
side out while its fill would likely lose; arcus/strategies/setup.py)."""

from __future__ import annotations

from typing import Any

from arcus.common.config import MMSession


def make_strategy(session: MMSession) -> Any:
    from arcus.strategies.grid import GridStrategy
    from arcus.strategies.mid import MidStrategy
    from arcus.strategies.smart import SmartStrategy

    return {"mid": MidStrategy, "grid": GridStrategy, "smart": SmartStrategy}[session.mode](session)
