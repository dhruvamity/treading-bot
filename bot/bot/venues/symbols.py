"""Canonical symbols: an Arcus market display name ("BTC-USD") <-> its base ("BTC").

Built from the live market list at start-up; market ids are never hard-coded. Duplicate bases are reported, not
guessed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from bot.venues.base import Market, Venue


def canonical_base(venue: Venue, venue_symbol: str) -> str:
    return venue_symbol.upper().removesuffix("-USD")


@dataclass(slots=True)
class SymbolMap:
    by_base: dict[str, dict[Venue, Market]] = field(default_factory=dict)
    ambiguous: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, markets: Iterable[Market]) -> SymbolMap:
        sm = cls()
        seen: dict[tuple[Venue, str], Market] = {}
        for m in markets:
            key = (m.venue, m.base)
            if key in seen and seen[key].venue_market_id != m.venue_market_id:
                sm.ambiguous.append(f"{m.venue}:{m.base} ids {seen[key].venue_market_id},{m.venue_market_id}")
                continue
            seen[key] = m
            sm.by_base.setdefault(m.base, {})[m.venue] = m
        return sm

    def get(self, base: str, venue: Venue) -> Market:
        try:
            return self.by_base[base.upper()][venue]
        except KeyError as e:
            raise KeyError(f"{base} is not listed on {venue}") from e

    def has(self, base: str, venue: Venue) -> bool:
        return venue in self.by_base.get(base.upper(), {})
