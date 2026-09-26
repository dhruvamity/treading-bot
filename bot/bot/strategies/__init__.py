"""Strategy registry: session config -> strategy instance. Two modes, as on Tread.fi: Mid and Grid
(bot/strategies/setup.py)."""

from __future__ import annotations

from typing import Any

from bot.common.config import MMSession


def make_strategy(session: MMSession) -> Any:
    from bot.strategies.grid import GridStrategy
    from bot.strategies.mid import MidStrategy

    return {"mid": MidStrategy, "grid": GridStrategy}[session.mode](session)
