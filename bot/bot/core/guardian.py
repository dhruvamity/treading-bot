"""Guardian (P2 task 7): an independent process with its own venue connections.

- Watches the bot heartbeat (written every 5 s). Stale for 60 s -> cancel-all + CRIT alert. (The Arcus dead man's
  switch fires on its own too; the guardian covers its 10-fires/day cap and a bot that hangs with the switch armed.)
- A bot stopped on purpose (Telegram /stop, close, `bot down --all`) writes a last heartbeat saying so after it has
  pulled its quotes: the guardian then exits quietly instead of alarming.
- Watches account equity from public reads; drawdown beyond the hard limit -> cancel-all and flatten:
  reduce-only maker orders first, IOC reduce-only after 30 s.
- NEVER places a risk-increasing order: every order it sends is reduce-only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from bot.common.ids import ClientIdFactory
from bot.common.logging import Log
from bot.common.time import now_us
from bot.core.alerts import Alerter, Level
from bot.core.heartbeat import read_heartbeat, read_heartbeat_age_s
from bot.venues.base import TIF, Market, OrderRequest, Position, Side, Venue

log = Log("guardian")


@dataclass
class GuardedVenue:
    venue: Venue
    adapter: object  # VenueAdapter with cancel_all / positions / balances / place
    markets: dict[str, Market]
    start_equity: Decimal | None = None
    capital_usd: Decimal = Decimal(0)


def flatten_orders(positions: Sequence[Position], markets: dict[str, Market], mids: dict[str, Decimal],
                   ids: ClientIdFactory, venue: Venue, *, taker: bool, slippage_bps: Decimal = Decimal(50)) -> list[OrderRequest]:
    """Reduce-only orders that close every position. Maker: at the touch-ish mid; taker: IOC with a bound."""
    out = []
    for p in positions:
        if p.size == 0 or p.base not in markets:
            continue
        m = markets[p.base]
        mid = mids.get(p.base) or p.mark_price
        side = Side.SELL if p.size > 0 else Side.BUY
        if taker:
            px = mid * (1 - slippage_bps / 10_000) if side is Side.SELL else mid * (1 + slippage_bps / 10_000)
            px = m.round_price(px, is_bid=side is Side.BUY)
            tif = TIF.IOC
        else:
            px = m.round_price(mid, is_bid=side is Side.BUY)
            tif = TIF.POST_ONLY
        size = (abs(p.size) / m.step_size).to_integral_value() * m.step_size
        out.append(OrderRequest(venue, p.base, side, px, size, tif, reduce_only=True, client_id=ids.arcus(),
                                tag="guardian_flatten",
                                reason="guardian flatten (reduce-only)"))
    assert all(o.reduce_only for o in out), "guardian may only send reduce-only orders"
    return out


@dataclass
class Guardian:
    heartbeat_file: Path
    venues: list[GuardedVenue]
    alerter: Alerter
    heartbeat_timeout_s: float = 60.0
    drawdown_hard_pct: float = 10.0
    poll_s: float = 5.0
    taker_after_s: float = 30.0
    fired_heartbeat: bool = False
    fired_drawdown: bool = False
    stood_down: bool = False
    started_us: int = field(default_factory=now_us)
    ids: ClientIdFactory = field(default_factory=lambda: ClientIdFactory("guardian", now_us() // 60_000_000 % 10**7))

    async def cancel_all_everywhere(self, why: str) -> None:
        for gv in self.venues:
            try:
                await gv.adapter.cancel_all(None)  # type: ignore[attr-defined]
                log.critical("guardian_cancel_all", venue=gv.venue.value, reason=why)
            except Exception as e:
                log.critical("guardian_cancel_all_failed", venue=gv.venue.value, reason=type(e).__name__,
                             data={"err": str(e)[:200]})
        await self.alerter.send(Level.CRIT, "guardian", f"guardian cancel-all: {why}")

    async def flatten_everywhere(self, why: str) -> None:
        await self.cancel_all_everywhere(why)
        for taker in (False, True):
            for gv in self.venues:
                try:
                    pos = await gv.adapter.positions()  # type: ignore[attr-defined]
                    mids = {p.base: p.mark_price for p in pos}
                    orders = flatten_orders(pos, gv.markets, mids, self.ids, gv.venue, taker=taker)
                    if orders:
                        await gv.adapter.place(orders)  # type: ignore[attr-defined]
                except Exception as e:
                    log.critical("guardian_flatten_failed", venue=gv.venue.value, reason=type(e).__name__)
            if not taker:
                await asyncio.sleep(self.taker_after_s)
                for gv in self.venues:
                    try:
                        await gv.adapter.cancel_all(None)  # type: ignore[attr-defined]
                    except Exception:
                        log.critical("guardian_cancel_before_taker_failed", venue=gv.venue.value)

    async def check_once(self) -> list[str]:
        actions: list[str] = []
        hb = read_heartbeat(self.heartbeat_file) or {}
        if hb.get("stopped"):
            # The bot stopped on purpose and pulled its quotes: nothing to alarm about. Stand down once that stop is
            # newer than this guardian (an older one only means its bot has not written a heartbeat yet).
            if int(str(hb.get("ts_us") or 0)) >= self.started_us:
                self.stood_down = True
                log.info("guardian_stood_down", reason=str(hb["stopped"]))
                return ["stood_down"]
        else:
            age = read_heartbeat_age_s(self.heartbeat_file)
            if age > self.heartbeat_timeout_s and not self.fired_heartbeat:
                self.fired_heartbeat = True
                actions.append("heartbeat_cancel_all")
                await self.cancel_all_everywhere(f"bot heartbeat silent for {age:.0f}s")
            elif age <= self.heartbeat_timeout_s:
                self.fired_heartbeat = False
        for gv in self.venues:
            try:
                bal = await gv.adapter.balances()  # type: ignore[attr-defined]
            except Exception as e:
                log.warning("guardian_balance_read_failed", venue=gv.venue.value, reason=type(e).__name__)
                continue
            eq = Decimal(bal.get("equity", 0))
            if gv.start_equity is None:
                gv.start_equity = eq
            cap = gv.capital_usd or gv.start_equity
            if cap and gv.start_equity - eq > cap * Decimal(self.drawdown_hard_pct) / 100 and not self.fired_drawdown:
                self.fired_drawdown = True
                actions.append("drawdown_flatten")
                await self.flatten_everywhere(f"{gv.venue.value} equity {eq} below start {gv.start_equity} by more "
                                              f"than {self.drawdown_hard_pct}% of ${cap}")
        return actions

    async def run(self) -> None:
        log.info("guardian_started", data={"venues": [g.venue.value for g in self.venues]})
        while not self.stood_down:
            try:
                await self.check_once()
            except Exception as e:
                log.critical("guardian_loop_error", reason=type(e).__name__, data={"err": str(e)[:200]})
            await asyncio.sleep(self.poll_s)
