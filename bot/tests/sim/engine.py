"""Test harness: an event-driven simulator that replays synthetic events through the SAME SessionEngine, strategies,
order manager, risk engine and ledger used live, with PaperVenue (queue-aware fills, latency) as the venue. The
scout ranks setups with its own faster backtest (bot/scout/sim.py); this one tests the live code end to end.

Deterministic: events are totally ordered, all randomness is seeded, the clock is simulated.
Outages (P3A task 9): during an outage the venue keeps matching resting orders (book truth still advances) but our
feed is stale (hub timestamps frozen) and order entry fails.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bot.common.config import MMSession, RiskLimitsCfg, SafetyPauseCfg
from bot.common.errors import VenueError
from bot.common.logging import DecisionLog
from bot.core.book import L2Book
from bot.core.budget import BudgetGovernor
from bot.core.calendar import TradingCalendar
from bot.core.engine import SessionEngine
from bot.core.ledger import Ledger, PnLBreakdown
from bot.core.marketdata import MarketDataHub
from bot.core.risk import RiskEngine
from bot.core.state import StateStore
from bot.strategies import make_strategy
from bot.venues.base import Fill, Market, Venue
from bot.venues.paper.adapter import LatencyModel, PaperVenue
from bot.venues.paper.fills import FillMode
from tests.sim.events import Event
from tests.sim.margin import check_liquidation

Z = Decimal(0)


@dataclass(frozen=True, slots=True)
class Outage:
    venue: Venue
    start_us: int
    end_us: int


@dataclass
class SimConfig:
    tick_ms: int = 1000
    fill_mode: FillMode = FillMode.PESSIMISTIC
    latency: dict[Venue, LatencyModel] = field(default_factory=lambda: {Venue.ARCUS: LatencyModel.arcus()})
    outages: list[Outage] = field(default_factory=list)
    liq_fee: Decimal = Decimal("0.01")
    starting_equity: dict[Venue, Decimal] = field(default_factory=lambda: {Venue.ARCUS: Decimal(100)})
    risk: RiskLimitsCfg = field(default_factory=RiskLimitsCfg)
    safety: SafetyPauseCfg = field(default_factory=SafetyPauseCfg)
    equity_every_s: int = 60
    touch_only: dict[Venue, set[str]] = field(default_factory=dict)


@dataclass
class SimResult:
    breakdown: dict[str, PnLBreakdown]
    total: PnLBreakdown
    equity_curve: list[tuple[int, float]]
    fills: list[Fill]
    liquidations: list[dict[str, Any]]
    risk_events: list[Any]
    decisions: list[dict[str, Any]]
    stats: dict[str, Any]
    post_only_rejects: int
    orders_placed: int
    min_liq_distance_sigma: float
    queue_at_fill: list[float]
    mode_time_s: dict[str, float]


class _OutageVenue(PaperVenue):
    """PaperVenue whose order entry can be down."""

    entry_down: bool = False

    async def place(self, orders: Any) -> Any:
        if self.entry_down:
            raise VenueError(self.venue.value, "simulated outage: order entry down", retryable=True)
        return await super().place(orders)

    async def cancel(self, client_ids: Any) -> None:
        if self.entry_down:
            raise VenueError(self.venue.value, "simulated outage: order entry down", retryable=True)
        await super().cancel(client_ids)

    async def modify(self, client_id: str, price: Decimal, size: Decimal) -> Any:
        if self.entry_down:
            raise VenueError(self.venue.value, "simulated outage: order entry down", retryable=True)
        return await super().modify(client_id, price, size)


class Simulator:
    def __init__(self, sessions: list[MMSession], markets: dict[Venue, dict[str, Market]],
                 cfg: SimConfig | None = None, calendar: TradingCalendar | None = None,
                 strategies: list[Any] | None = None) -> None:
        self.cfg = cfg or SimConfig()
        self.now = 0
        self.hub = MarketDataHub()
        self.markets = markets
        self.state = StateStore(":memory:")
        self.ledger = Ledger()
        self.decisions = DecisionLog(keep_last=200_000)
        self.governor = BudgetGovernor()
        self.calendar = calendar or TradingCalendar()
        self.risk = RiskEngine(limits=self.cfg.risk, safety=self.cfg.safety, calendar=self.calendar,
                               decisions=self.decisions)
        bases = {s.market.upper() for s in sessions}
        self.venues: dict[Venue, _OutageVenue] = {}
        for v in Venue:
            mk = {b: m for b, m in markets.get(v, {}).items() if b in bases}
            if not mk:
                continue
            books = {b: self.hub.view(v, b).book for b in mk}
            self.venues[v] = _OutageVenue(v, mk, books, now_us=lambda: self.now, latency=self.cfg.latency[v],
                                          fill_mode=self.cfg.fill_mode,
                                          starting_equity=self.cfg.starting_equity.get(v, Decimal(100)),
                                          touch_only=self.cfg.touch_only.get(v), marks=self._mark_fn(v))
        self.engines: list[SessionEngine] = []
        for i, s in enumerate(sessions):
            strat = strategies[i] if strategies else make_strategy(s)
            self.engines.append(SessionEngine(session=s, strategy=strat, hub=self.hub,
                                              adapters=dict(self.venues), markets=markets, state=self.state,
                                              risk=self.risk, governor=self.governor, ledger=self.ledger,
                                              decisions=self.decisions, calendar=self.calendar, session_num=i + 1,
                                              risk_limits=self.cfg.risk))
        self.by_session = {e.sid: e for e in self.engines}
        self.equity_curve: list[tuple[int, float]] = []
        self.liquidations: list[dict[str, Any]] = []
        self.fills: list[Fill] = []
        self.min_liq_sigma = math.inf
        self._last_second = 0
        self._last_equity_us = 0
        self._last_margin_us = 0

    def _mark_fn(self, v: Venue) -> Any:
        def f(b: str) -> Decimal | None:
            view = self.hub.get(v, b)
            if view is None:
                return None
            return view.mark or view.mid()
        return f

    # ---------------------------------------------------------------- event application
    def _apply_snapshot(self, book: L2Book, bids: list[tuple[Decimal, Decimal]], asks: list[tuple[Decimal, Decimal]]) -> None:
        """Diff-apply so the fill model sees level changes (keeps queue semantics)."""
        nb, na = dict(bids), dict(asks)
        for p in [p for p in book.bids if p not in nb]:
            book.set_level(True, p, Z)
        for p in [p for p in book.asks if p not in na]:
            book.set_level(False, p, Z)
        book.apply(bids, asks)

    def _in_outage(self, v: Venue, ts: int) -> bool:
        return any(o.venue is v and o.start_us <= ts < o.end_us for o in self.cfg.outages)

    def apply(self, e: Event) -> None:
        view = self.hub.view(e.venue, e.base)
        down = self._in_outage(e.venue, e.ts_us)
        pv = self.venues.get(e.venue)
        if pv is not None:
            pv.entry_down = down
        if e.kind in ("book_snapshot", "book_delta"):
            bids, asks = e.payload
            if e.kind == "book_snapshot":
                self._apply_snapshot(view.book, bids, asks)
            else:
                view.book.apply(bids, asks)
            if not down:
                view.book_ts_us = e.ts_us
        elif e.kind == "trade":
            if not down:
                view.on_trade(e.payload)
            if pv is not None:
                pv.on_public_trade(e.payload)
        elif e.kind == "price" and not down:
            p = e.payload
            view.mark = p.get("mark") or view.mark
            view.oracle = p.get("oracle") or view.oracle
            view.index = p.get("index") or view.index
            view.price_ts_us = e.ts_us
        elif e.kind == "funding_pred" and not down:
            view.predicted_funding_h = e.payload.get("rate_h")
        elif e.kind == "attrs" and not down:
            a = e.payload
            if a.get("is_outside_rth") is not None:
                view.is_outside_rth = bool(a["is_outside_rth"])
            for k_src, k_dst in (("upper_bound", "upper_bound"), ("lower_bound", "lower_bound")):
                if a.get(k_src):
                    setattr(view, k_dst, Decimal(a[k_src]))
            view.upper_in_zone = bool(a.get("upper_in_zone"))
            view.lower_in_zone = bool(a.get("lower_in_zone"))
            if a.get("oi"):
                view.oi = Decimal(a["oi"])
            if a.get("oi_cap"):
                view.oi_cap = Decimal(a["oi_cap"])
        elif e.kind == "funding_paid":
            rate = Decimal(str(e.payload["rate_h"]))
            view.last_funding_h = float(rate)
            if pv is not None:
                pay_px = view.oracle or view.mid() or \
                    (Decimal(str(e.payload["pay_price"])) if e.payload.get("pay_price") else None)
                if pay_px is not None:
                    pos = pv.acct.positions.get(e.base, Z)
                    if pos != 0:
                        pay = pv.apply_funding(e.base, rate, pay_px)
                        self.ledger.on_funding(e.venue, e.base, pay)
                        self.state.on_funding(e.venue, e.base, e.ts_us, rate, pos, pay)

    # ---------------------------------------------------------------- drain
    async def drain(self) -> None:
        for pv in self.venues.values():
            pv.process(self.now)
            updates, fills = pv.drain()
            for st in updates:
                o = self.state.orders.get(st.client_id)
                eng = self.by_session.get(o.session) if o else None
                if eng is not None:
                    await eng.on_order_update(st, self.now)
                else:
                    self.state.on_update(st)
            for f in fills:
                self.fills.append(f)
                o = self.state.orders.get(f.client_id)
                eng = self.by_session.get(o.session) if o else (self.engines[0] if self.engines else None)
                if eng is not None:
                    await eng.on_fill(f, self.now)

    async def _margin_check(self) -> None:
        for v, pv in self.venues.items():
            marks = {b: m for b in pv.acct.positions if (m := self._mark_fn(v)(b)) is not None}
            eq = pv.equity()
            acts = check_liquidation(v, eq, dict(pv.acct.positions), marks, pv._markets, self.cfg.liq_fee)
            for a in acts:
                f = pv.liquidate(a.base, a.close_size, a.price, a.fee, self.now, a.kind)
                pv._fills.pop()  # booked directly below, not re-dispatched through drain()
                self.fills.append(f)
                self.ledger.on_fill(f, a.price)
                self.state.on_fill(f)
                self.liquidations.append({"ts_us": self.now, "venue": v.value, "base": a.base, "kind": a.kind,
                                          "size": str(a.close_size), "price": str(a.price)})
            # distance to liquidation in sigma (for the report)
            for b, pos in pv.acct.positions.items():
                if pos == 0 or b not in marks:
                    continue
                view = self.hub.get(v, b)
                sig = view.sigma_1h() if view else 0.0
                notional = abs(pos) * marks[b]
                dist = float((eq - notional * pv._markets[b].mmf) / notional) if notional > 0 else math.inf
                if sig > 0:
                    self.min_liq_sigma = min(self.min_liq_sigma, dist / sig)

    async def _clock_to(self, ts: int) -> None:
        tick = self.cfg.tick_ms * 1000
        if self._last_second == 0:
            self._last_second = ts - ts % tick
        while self._last_second + tick <= ts:
            self._last_second += tick
            self.now = self._last_second
            await self.drain()
            if self._last_second % 1_000_000 == 0:
                self.hub.tick_1s(self.now)
            for v, pv in self.venues.items():
                bal = await pv.balances()
                for eng in self.engines:
                    eng.set_account(v, bal["equity"], bal["free_collateral"])
            for eng in self.engines:
                await eng.tick(self.now)
            await self.drain()
            if self.now - self._last_margin_us >= 60_000_000:
                self._last_margin_us = self.now
                await self._margin_check()
            if self.now - self._last_equity_us >= self.cfg.equity_every_s * 1_000_000:
                self._last_equity_us = self.now
                self.equity_curve.append((self.now, float(sum((pv.equity() for pv in self.venues.values()), Z))))

    async def run(self, events: Iterable[Event]) -> SimResult:
        for e in events:
            await self._clock_to(e.ts_us)
            self.now = e.ts_us
            self.apply(e)
            await self.drain()
        await self._clock_to(self.now + self.cfg.tick_ms * 1000)
        return self.result()

    def run_sync(self, events: Iterable[Event]) -> SimResult:
        return asyncio.run(self.run(events))

    def result(self) -> SimResult:
        marks = {}
        for (v, b), view in self.hub.views.items():
            mk = view.mark or view.mid()
            if mk is not None:
                marks[(v, b)] = mk
        bd = {f"{v.value}:{b}": self.ledger.breakdown(v, b, marks.get((v, b))) for (v, b) in list(self.ledger.books)}
        queue: list[float] = []
        stats: dict[str, Any] = {}
        placed = 0
        rejects = 0
        mode_time: dict[str, float] = {}
        for pv in self.venues.values():
            rejects += sum(m.post_only_rejects for m in pv.models.values())
            placed += pv.actions
        for e in self.engines:
            stats[e.sid] = {"ticks": e.stats.ticks, "actions": e.stats.actions, "rejects": e.stats.rejects,
                            "errors": e.stats.errors, "iocs": e.stats.iocs, "risk_events": len(e.stats.risk_events)}
            for k, secs in e.stats.mode_time_s.items():
                mode_time[k] = mode_time.get(k, 0.0) + secs
        return SimResult(breakdown=bd, total=self.ledger.total(marks), equity_curve=self.equity_curve, fills=self.fills,
                         liquidations=self.liquidations,
                         risk_events=[d for e in self.engines for d in e.stats.risk_events],
                         decisions=list(self.decisions.records), stats=stats, post_only_rejects=rejects,
                         orders_placed=placed, min_liq_distance_sigma=self.min_liq_sigma, queue_at_fill=queue,
                         mode_time_s=mode_time)
