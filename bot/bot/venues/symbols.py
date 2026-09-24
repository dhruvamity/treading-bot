"""Canonical symbol mapping (P0 task 4).

Builds canonical BASE -> {arcus: (marketId, displayName), lighter_rh: (market_id, symbol)} from the live
market lists at start-up. IDs are never hard-coded. Unmatched and ambiguous symbols are reported, not guessed
(e.g. Arcus GLD vs Lighter XAU is a ratio hedge, excluded from v1).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from bot.venues.base import Market, Venue

# Known naming differences where the same underlying trades under different tickers. Empty by design:
# only add a pair after checking both venues' contract specs describe the same instrument and unit.
ALIASES: dict[tuple[Venue, str], str] = {}

# Same underlying, different contract (unit/ratio): never map automatically.
RATIO_PAIRS = {("GLD", "XAU"), ("XAU", "GLD")}


def canonical_base(venue: Venue, venue_symbol: str) -> str:
    s = venue_symbol.upper()
    if venue is Venue.ARCUS:
        s = s.removesuffix("-USD")
    s = s.split("/")[0]  # Lighter spot "INTC/USDG" (not used for perps)
    return ALIASES.get((venue, s), s)


@dataclass(slots=True)
class SymbolMap:
    by_base: dict[str, dict[Venue, Market]] = field(default_factory=dict)
    unmatched: dict[Venue, list[str]] = field(default_factory=dict)
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
        venues = {v for (v, _) in seen}
        for base, per in sm.by_base.items():
            if len(per) == 1 and len(venues) > 1:
                (v,) = per
                sm.unmatched.setdefault(v, []).append(base)
        for v in sm.unmatched:
            sm.unmatched[v].sort()
        return sm

    def get(self, base: str, venue: Venue) -> Market:
        try:
            return self.by_base[base.upper()][venue]
        except KeyError as e:
            raise KeyError(f"{base} is not listed on {venue}") from e

    def has(self, base: str, venue: Venue) -> bool:
        return venue in self.by_base.get(base.upper(), {})

    def both(self) -> list[str]:
        return sorted(b for b, per in self.by_base.items() if len(per) >= 2)

    def ratio_pairs_present(self) -> list[tuple[str, str]]:
        out = []
        for a, b in RATIO_PAIRS:
            if self.has(a, Venue.ARCUS) and self.has(b, Venue.LIGHTER_RH):
                out.append((a, b))
        return out

    def report(self) -> dict[str, object]:
        return {
            "both_venues": self.both(),
            "unmatched": {str(k): v for k, v in self.unmatched.items()},
            "ambiguous": self.ambiguous,
            "ratio_pairs_excluded": self.ratio_pairs_present(),
        }
