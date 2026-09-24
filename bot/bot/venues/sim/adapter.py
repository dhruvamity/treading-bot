"""SimVenueAdapter (P3A task 10): the paper venue driven by the simulator's clock and replayed books/trades."""

from __future__ import annotations

from bot.venues.paper.adapter import LatencyModel, PaperVenue

SimVenueAdapter = PaperVenue

__all__ = ["LatencyModel", "SimVenueAdapter"]
