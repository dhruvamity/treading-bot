"""A trader's own eyes on the market: the market list, and one-minute prices of the markets the autopilot may trade.

A trader (BOT_ROLE=trader) records nothing, so it has no recorder to tell it what Arcus lists or where each market is
now. This keeps two small things current with public data only:

- data/scout/markets.json, re-read every 10 minutes, as the recorder writes it: the pilot sizes a run from it (the
  maximum leverage, the minimum order) and refuses a market that is not online;
- the last RECENT_MIN one-minute mids of the playbook's markets, in memory, from the best bid and offer: what the
  autopilot judges the market's state from (arcus/scout/regime.py). They are read here, not fetched from the other
  machine, so a decision about a run never waits on a connection between two machines.

No trades, no book depth, nothing written but that one file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from arcus.common.logging import Log
from arcus.scout.record import MARKETS_EVERY_S, MINUTE_US, RECENT_MIN, SUBS_PER_CONN
from arcus.venues.arcus.rest import ArcusRest
from arcus.venues.arcus.ws import ArcusWS
from arcus.venues.base import Venue
from arcus.venues.symbols import canonical_base

log = Log("scout.watch")


def write_markets(root: Path, markets: list[dict[str, Any]], now: float | None = None) -> None:
    """data/scout/markets.json, replaced whole (the pilot may be reading it)."""
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "markets.json.tmp"
    tmp.write_text(json.dumps({"markets": markets, "fetched": now or time.time()}))
    tmp.replace(root / "markets.json")


class MidWatch:
    def __init__(self, root: Path, *, rest_url: str, ws_url: str,
                 wanted: Callable[[], list[str]] | None = None) -> None:
        self.root = root                                   # data/scout
        self.rest = ArcusRest(rest_url)
        self.ws_url = ws_url
        self.wanted = wanted or (lambda: [])               # the markets to watch now ("BTC-USD", ...)
        self.display: dict[str, str] = {}                  # base -> "BTC-USD"
        self.minutes: dict[str, deque[tuple[int, float]]] = {}
        self.ws: ArcusWS | None = None
        self.on: set[str] = set()
        self.markets_ts = 0.0
        self._stop = asyncio.Event()

    def recent(self, market: str) -> list[tuple[int, float]]:
        """The last RECENT_MIN minutes of mids: [(minute end µs, mid)], oldest first (as ScoutRecorder.recent)."""
        return list(self.minutes.get(market, ()))

    def _on_bbo(self, base: str, c: dict[str, Any], recv_us: int) -> None:
        d = self.display.get(base)
        b, a = (c or {}).get("bestBid") or {}, (c or {}).get("bestAsk") or {}
        if d is None or not b.get("price") or not a.get("price"):
            return
        ts, mid = int(c.get("timestamp") or recv_us), (float(b["price"]) + float(a["price"])) / 2
        end = ts - ts % MINUTE_US + MINUTE_US
        q = self.minutes.setdefault(d, deque(maxlen=RECENT_MIN))
        if q and q[-1][0] == end:
            q[-1] = (end, mid)
        elif not q or end > q[-1][0]:
            q.append((end, mid))

    async def refresh(self) -> bool:
        """Re-read the market list, write it, and start watching any wanted market not watched yet. False if the
        read failed (the old file stays; tried again at the next turn)."""
        try:
            ms = await self.rest.markets()
        except Exception as e:
            log.warning("watch_markets_failed", reason=f"{type(e).__name__}: {e}"[:200])
            return False
        write_markets(self.root, ms)
        self.markets_ts = time.time()
        online = {m["marketDisplayName"] for m in ms if m.get("status") == "ONLINE"}
        new = [d for d in self.wanted() if d in online and d not in self.on][:SUBS_PER_CONN]
        for d in new:
            self.display[canonical_base(Venue.ARCUS, d)] = d
            if self.ws is None:
                self.ws = ArcusWS(self.ws_url, n_levels=1)
                self.ws.on("bbo", self._on_bbo)
                self.ws.start()
            await self.ws.subscribe_market(d, book=False, bbo=True, trades=False, predicted_funding=False)
            self.on.add(d)
        if new:
            log.info("watch_subscribed", data={"new": new, "markets": len(self.on)})
        return True

    async def run(self) -> None:
        try:
            while not self._stop.is_set():
                ok = await self.refresh()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=MARKETS_EVERY_S if ok else 30.0)
        finally:
            if self.ws is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.ws.stop(), 5)
            await self.rest.close()

    def stop(self) -> None:
        self._stop.set()
