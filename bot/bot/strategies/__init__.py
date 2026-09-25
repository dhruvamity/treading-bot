"""Strategy registry: session config -> strategy instance."""

from __future__ import annotations

from typing import Any

from bot.common.config import MMSession


def make_strategy(session: MMSession) -> Any:
    from bot.strategies.grid import GridStrategy
    from bot.strategies.mid import MidStrategy
    from bot.strategies.rgrid import RGridStrategy
    from bot.strategies.signal import SignalStrategy

    return {"mid": MidStrategy, "grid": GridStrategy, "rgrid": RGridStrategy, "signal": SignalStrategy}[session.mode](session)
