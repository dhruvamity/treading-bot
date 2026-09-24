"""Per-venue margin and liquidation in the simulator (P3A task 5). Legs on different venues never share margin.

Arcus: liquidation when equity <= maintenance margin (mark-valued); fee unknown -> parameter (default 1%); modelled
as a full close at mark with the fee. Off-hours IMF applies to opening only (no liquidation for it).
Lighter: below MMF -> partial liquidation at the zero price ZP = Mark x (1 - MMF x AccountValue / MMReq) (long) until
back above MM, fee up to 1%; below the close-out MF -> full takeover. Modelled: partial = close half at ZP, full = all.
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
    kind: str  # arcus_full | lighter_partial | lighter_full


def maintenance(positions: dict[str, Decimal], marks: dict[str, Decimal], markets: dict[str, Market]) -> Decimal:
    return sum((abs(p) * marks.get(b, Z) * markets[b].mmf for b, p in positions.items() if p), Z)


def check_liquidation(venue: Venue, equity: Decimal, positions: dict[str, Decimal], marks: dict[str, Decimal],
                      markets: dict[str, Market], fee_frac: Decimal = Decimal("0.01")) -> list[LiqAction]:
    mm = maintenance(positions, marks, markets)
    if mm <= 0 or equity > mm:
        return []
    out = []
    for b, pos in positions.items():
        if pos == 0 or b not in marks:
            continue
        mark, m = marks[b], markets[b]
        if venue is Venue.ARCUS:
            out.append(LiqAction(venue, b, -pos, mark, abs(pos) * mark * fee_frac, "arcus_full"))
            continue
        closeout_req = sum((abs(p) * marks.get(x, Z) * (markets[x].close_out_mf or markets[x].mmf) for x, p in positions.items()), Z)
        if equity <= closeout_req:
            out.append(LiqAction(venue, b, -pos, mark, abs(pos) * mark * fee_frac, "lighter_full"))
            continue
        ratio = (equity / mm) if mm > 0 else Decimal(1)
        zp = mark * (1 - m.mmf * ratio) if pos > 0 else mark * (1 + m.mmf * ratio)
        half = -pos / 2
        out.append(LiqAction(venue, b, half, zp, abs(half) * zp * fee_frac, "lighter_partial"))
    return out


def distance_to_liquidation(collateral: Decimal, notional: Decimal, mmf: Decimal) -> Decimal:
    """A6.7: adverse move fraction ~ (C - N x MMF) / N."""
    return (collateral - notional * mmf) / notional if notional > 0 else Decimal("Infinity")
