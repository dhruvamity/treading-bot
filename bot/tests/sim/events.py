"""Simulator events: one deterministic, time-ordered stream per run.

Event = (ts_us, prio, seq, venue, base, kind, payload). Order: time, then kind priority (book before trades before
prices before funding), then the venue's own sequence, then the load order.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from bot.venues.base import Venue

PRIO = {"book_snapshot": 0, "book_delta": 1, "trade": 2, "price": 3, "attrs": 4, "funding_pred": 5, "funding_paid": 6}


@dataclass(frozen=True, slots=True)
class Event:
    ts_us: int
    prio: int
    seq: int
    order: int
    venue: Venue
    base: str
    kind: str
    payload: Any

    def key(self) -> tuple[int, int, int, int]:
        return (self.ts_us, self.prio, self.seq, self.order)


_counter = itertools.count()


def ev(ts: int, venue: Venue, base: str, kind: str, payload: Any, seq: int = 0) -> Event:
    return Event(ts, PRIO[kind], seq, next(_counter), venue, base, kind, payload)


def merge(streams: Iterable[Iterable[Event]]) -> Iterator[Event]:
    return heapq.merge(*streams, key=Event.key)

