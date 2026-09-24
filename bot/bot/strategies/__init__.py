"""Strategy registry: session config -> strategy instance."""

from __future__ import annotations

from typing import Any

from bot.common.config import DNSession, MMSession


def make_strategy(session: MMSession | DNSession) -> Any:
    from bot.strategies.auto import AutoStrategy
    from bot.strategies.blend import BlendStrategy
    from bot.strategies.dgrid import DGridStrategy
    from bot.strategies.dn_carry import DNCarry
    from bot.strategies.dn_hedged_mm import DNHedgedMM
    from bot.strategies.grid import GridStrategy
    from bot.strategies.mid import MidStrategy
    from bot.strategies.points_overlay import PointsOverlay
    from bot.strategies.rgrid import RGridStrategy
    from bot.strategies.signal import SignalStrategy

    if isinstance(session, DNSession):
        return {"dn_carry": DNCarry, "dn_hedged_mm": DNHedgedMM, "points_overlay": PointsOverlay}[session.strategy](session)
    return {"auto": AutoStrategy, "mid": MidStrategy, "grid": GridStrategy, "rgrid": RGridStrategy,
            "dgrid": DGridStrategy, "blend": BlendStrategy, "signal": SignalStrategy}[session.mode](session)
