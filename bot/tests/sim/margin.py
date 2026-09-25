"""Margin and liquidation in the simulator: liquidation when equity <= maintenance margin (mark-valued), modelled as a
full close at mark with a fee (Arcus does not document it; a parameter, default 1%). The off-hours IMF applies to
opening only (no liquidation for it).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bot.venues.base import Market, Venue

Z = Decimal(0)


@dataclass(frozen=True, slots=True)
class LiqAction:
    venue: Venue
    base: str
    close_size: Decimal  # signed size to close (opposite of position)
    price: Decimal
    fee: Decimal
    kind: str


def maintenance(positions: dict[str, Decimal], marks: dict[str, Decimal], markets: dict[str, Market]) -> Decimal:
    return sum((abs(p) * marks.get(b, Z) * markets[b].mmf for b, p in positions.items() if p), Z)


def check_liquidation(venue: Venue, equity: Decimal, positions: dict[str, Decimal], marks: dict[str, Decimal],
                      markets: dict[str, Market], fee_frac: Decimal = Decimal("0.01")) -> list[LiqAction]:
    mm = maintenance(positions, marks, markets)
    if mm <= 0 or equity > mm:
        return []
    return [LiqAction(venue, b, -pos, marks[b], abs(pos) * marks[b] * fee_frac, "arcus_full")
            for b, pos in positions.items() if pos != 0 and b in marks]


def distance_to_liquidation(collateral: Decimal, notional: Decimal, mmf: Decimal) -> Decimal:
    """A6.7: adverse move fraction ~ (C - N x MMF) / N."""
    return (collateral - notional * mmf) / notional if notional > 0 else Decimal("Infinity")
