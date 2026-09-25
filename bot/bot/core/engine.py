"""Session engine: the one place where market data, risk gates, a strategy, the order manager, venues, state and the
ledger meet. The simulator, paper mode and live all drive this class; only the clock and the adapters differ.

Per tick (default 1 s): gates (safety pause, band/OI, event window, budget) -> strategy.on_tick -> order-manager sync
per venue/market -> IOC intents (deduped while in flight) -> PnL kill switches -> liquidation distance.
Fills: state (dedupe) -> tag enrichment -> ledger (edge vs mid at fill) -> governor -> strategy.on_fill -> markouts.
Order updates: state -> order manager in-flight -> critical rejects (SELF_TRADE / GEO_RESTRICTED) stop the venue.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.common import settings, sizing
from bot.common.config import DNSession, MMSession, RiskLimitsCfg
from bot.common.ids import ClientIdFactory
from bot.common.logging import DecisionLog, Log
from bot.core.budget import BudgetGovernor, BudgetMode
from bot.core.calendar import TradingCalendar
from bot.core.ledger import Ledger
from bot.core.marketdata import MarketDataHub
from bot.core.order_manager import BBOTicks, OrderManager, PlanParams
from bot.core.risk import AccountSnapshot, RiskAction, RiskContext, RiskDecision, RiskEngine
from bot.core.scheduler import SessionClock, SessionState
from bot.core.state import StateStore
from bot.strategies.base import StrategyContext, StrategyOutput
from bot.venues.base import TIF, Fill, Market, OrderRequest, OrderState, OrderStatus, Side, Venue

log = Log("engine")
Z = Decimal(0)


@dataclass
class EngineStats:
    ticks: int = 0
    actions: int = 0
    rejects: int = 0
    errors: int = 0
    hedges: int = 0
    risk_events: list[RiskDecision] = field(default_factory=list)
    mode_time_s: dict[str, float] = field(default_factory=dict)


class SessionEngine:
    def __init__(self, *, session: MMSession | DNSession, strategy: Any, hub: MarketDataHub,
                 adapters: dict[Venue, Any], markets: dict[Venue, dict[str, Market]], state: StateStore,
                 risk: RiskEngine, governor: BudgetGovernor, ledger: Ledger, decisions: DecisionLog,
                 calendar: TradingCalendar, session_num: int = 1, alerter: Any = None,
                 risk_limits: RiskLimitsCfg | None = None) -> None:
        self.session = session
        self.strategy = strategy
        self.hub = hub
        self.adapters = adapters
        self.markets = markets
        self.state = state
        self.risk = risk
        self.governor = governor
        self.ledger = ledger
        self.decisions = decisions
        self.calendar = calendar
        self.alerter = alerter
        self.limits = risk_limits or RiskLimitsCfg()
        self.is_dn = isinstance(session, DNSession)
        self.sid = session.session_id
        if self.is_dn:
            assert isinstance(session, DNSession)
            self.venue = Venue.ARCUS
            self.other_venue: Venue | None = Venue.LIGHTER_RH
            self.account_index = {Venue.ARCUS: session.legs["arcus"].account_index or 0, Venue.LIGHTER_RH: 0}
            self.capital = Decimal(str(session.collateral_per_leg_usd * 2))
            self.clock = SessionClock(session.session)
            strat_name: str = session.strategy
        else:
            assert isinstance(session, MMSession)
            self.venue = Venue(session.venue)
            self.other_venue = Venue.LIGHTER_RH if self.venue is Venue.ARCUS else Venue.ARCUS
            self.account_index = {self.venue: session.account_index}
            self.capital = Decimal(str(session.capital_usd))
            risk.safety = session.safety_pause  # per-session thresholds (one MM session per process in v1)
            self.clock = SessionClock(session.session)
            strat_name = session.mode if session.mode != "auto" else "dgrid"
        self.base = session.market.upper()
        ids = {v: ClientIdFactory(strat_name if strat_name in ("mid", "grid", "rgrid", "dgrid", "blend", "signal",
                                                                 "dn_hedged_mm", "dn_carry", "points_overlay") else "manual",
                                  session_num) for v in (Venue.ARCUS, Venue.LIGHTER_RH)}
        self.now_us = 0
        self.om = OrderManager(adapters=adapters, state=state, risk=risk, governor=governor, decisions=decisions,
                               ids=ids, session=self.sid, account_index=self.account_index, now_fn=lambda: self.now_us)
        self.ids = ids
        self.stats = EngineStats()
        self.session_start_equity: Decimal | None = None
        self.day_start_equity: dict[str, Decimal] = {}
        self.inflight_intents: dict[tuple[Venue, str], int] = {}
        self._fill_usd_5m: deque[tuple[int, float]] = deque()
        self.other_down_since_us: int | None = None
        self.last_mode = ""
        self.last_tick_us = 0
        self.stopped = False
        # dollar stops (MMSession.pos_stop_usd / daily_stop_usd): exit with a maker order, cross after
        # exit_taker_after_s; a position stop then pauses quoting for cooldown_s
        self.exit_since_us: int | None = None
        self.pos_stop_active = False
        self.cooldown_until_us = 0
        # sizes that follow the account (MMSession.sizing, bot/common/sizing.py): the capital the sizes and the stops
        # are taken on, re-read from the account's equity at start and at 00:00 UTC
        self.size_capital = self.capital
        self.sized_day: str | None = None
        self.too_small = ""
        self.settings_dir: Path | None = None   # the runner sets it: the owner's Telegram settings (common/settings)
        self._wire_risk_context()

    # ---------------------------------------------------------------- wiring
    def market(self, venue: Venue) -> Market:
        return self.markets[venue][self.base]

    def _wire_risk_context(self) -> None:
        hub, state = self.hub, self.state

        def bbo(v: Venue, b: str) -> tuple[Decimal | None, Decimal | None]:
            view = hub.get(v, b)
            if view is None:
                return None, None
            bb, ba = view.book.best_bid(), view.book.best_ask()
            return (bb[0] if bb else None, ba[0] if ba else None)

        def oracle(v: Venue, b: str) -> Decimal | None:
            view = hub.get(v, b)
            if view is None:
                return None
            return view.oracle or view.index or view.mark or view.mid()

        def open_notional(v: Venue, b: str, s: Side, exclude: str | None = None) -> Decimal:
            return sum((o.remaining * o.req.price for o in state.open_orders(v, b)
                        if o.req.side is s and not o.req.reduce_only and o.req.client_id != exclude), Z)

        def oi(v: Venue, b: str) -> tuple[Decimal | None, Decimal | None]:
            view = hub.get(v, b)
            return (view.oi, view.oi_cap) if view else (None, None)

        self.risk.ctx = RiskContext(bbo=bbo, oracle=oracle, account=self._account, position=state.position,
                                    open_notional=open_notional, oi=oi)
        from bot.core.risk import MarketLimits

        if self.is_dn:
            assert isinstance(self.session, DNSession)
            cap = Decimal(str(self.session.collateral_per_leg_usd * self.session.leverage_per_leg * 1.05))
            for v in (Venue.ARCUS, Venue.LIGHTER_RH):
                self.risk.market_limits[(v, self.base)] = MarketLimits(position_cap_usd=cap,
                                                                        leverage_cap=Decimal(str(self.limits.max_leverage_dn)))
        else:
            assert isinstance(self.session, MMSession)
            self.risk.market_limits[(self.venue, self.base)] = MarketLimits(
                position_cap_usd=Decimal(str(self.session.inventory_cap_usd * 1.25)),
                leverage_cap=Decimal(str(min(self.session.leverage_max, self.limits.max_leverage_mm))))

    def _account(self, v: Venue) -> AccountSnapshot:
        acct = self.state.kv_get(f"acct:{v.value}")
        if not acct:
            return AccountSnapshot()
        eq, free = acct.split(",")
        return AccountSnapshot(Decimal(eq), Decimal(free))

    def set_account(self, v: Venue, equity: Decimal, free: Decimal) -> None:
        self.state.kv_set(f"acct:{v.value}", f"{equity},{free}")

    def resize(self, now_us: int) -> None:
        """Sessions with `sizing.follow_equity`: at the first account read and at each 00:00 UTC, size from the
        account's equity x capital_frac, bucketed, at most max_capital_usd and at most 1.25x the last capital a GO
        backtest covered (the session's, or a newer one the scout recorded for this market in kv "sizing_ok",
        which every new approval clears)."""
        s = self.session
        if not isinstance(s, MMSession) or s.sizing is None or not s.sizing.follow_equity:
            return
        from bot.common.time import utc_date_str

        day = utc_date_str(now_us)
        if day == self.sized_day:
            return
        eq = float(self._account(self.venue).equity)
        if eq <= 0:
            return  # no account read yet
        self.sized_day = day
        z, fixed = self._recipe(s)
        ok, _, ok_market = (self.state.kv_get("sizing_ok") or "").partition(":")
        covered = max(z.backtest_capital_usd, float(ok) if ok and ok_market == self.base else 0.0)
        cap = sizing.target_capital(min(eq, fixed) if fixed else eq, frac=z.capital_frac,
                                    max_capital=z.max_capital_usd, covered=covered)
        if cap < z.min_capital_usd:
            self.too_small = (f"equity ${eq:,.2f} is under the ${z.min_capital_usd:,.2f} {self.base} needs at this "
                              "leverage (Arcus minimum order)")
            self.decisions.record("resize", self.too_small, venue=self.venue.value, market=self.base, session=self.sid,
                                  ts_us=now_us)
            if self.alerter is not None:
                self.alerter.warn("sizing", self.too_small)
            return
        self.too_small = ""
        out = sizing.apply(s, cap, z)
        self.size_capital = Decimal(str(round(out.capital, 2)))
        lim = self.risk.market_limits.get((self.venue, self.base))
        if lim is not None:
            self.risk.market_limits[(self.venue, self.base)] = replace(
                lim, position_cap_usd=Decimal(str(s.inventory_cap_usd * 1.25)))
        msg = (f"sized for ${out.capital:,.2f} (equity ${eq:,.2f}, backtests cover ${covered:,.2f}): "
               f"order ${s.order_size_usd:,.2f}, cap ${s.inventory_cap_usd:,.2f}, stops ${s.pos_stop_usd:,.2f} "
               f"position / ${s.daily_stop_usd:,.2f} day / ${s.kill_usd:,.2f} kill")
        self.decisions.record("resize", msg, venue=self.venue.value, market=self.base, session=self.sid, ts_us=now_us)
        log.info("resize", venue=self.venue.value, market=self.base, session=self.sid,
                 data={"equity": eq, "capital": out.capital, "order": s.order_size_usd, "cap": s.inventory_cap_usd})

    def _recipe(self, s: MMSession) -> tuple[Any, float | None]:
        """The session's sizing recipe with the owner's Telegram settings applied (share of the balance, the cap, the
        stops), and a fixed capital if the owner set one (the bot then sizes for at most that)."""
        z = s.sizing
        assert z is not None
        if self.settings_dir is None:
            return z, None
        over = settings.load(self.settings_dir)
        pick: dict[str, Any] = {}
        if "trade_share" in over:
            pick["capital_frac"] = over["trade_share"] / 100
        if "max_capital" in over:
            pick["max_capital_usd"] = over["max_capital"]
        for name, fld in (("position_stop", "position_stop_pct"), ("daily_stop", "daily_stop_pct"),
                          ("kill", "kill_pct")):
            if name in over:
                pick[fld] = over[name]
        z = z.model_copy(update=pick) if pick else z
        cap = over.get("capital")
        return z, float(cap) if isinstance(cap, (int, float)) else None

    # ---------------------------------------------------------------- context
    def build_ctx(self, now_us: int) -> StrategyContext | None:
        m = self.market(self.venue)
        view = self.hub.get(self.venue, self.base)
        if view is None or view.mid() is None:
            return None
        other_m = self.markets.get(self.other_venue, {}).get(self.base) if self.other_venue else None
        other_view = self.hub.get(self.other_venue, self.base) if self.other_venue else None
        ok, why = self.risk.quoting_allowed(self.venue, self.base, now_us)
        skip = set(getattr(getattr(self.session, "session", None), "skip_events", []) or [])
        ev = self.calendar.in_event_window(now_us, self.base, skip) if skip else False
        off_hours = self.venue is Venue.ARCUS and bool(view.is_outside_rth) and m.rth is not None
        mode = self.governor.mode(self.venue, self.account_index.get(self.venue, 0))
        other_healthy = other_view is not None and not other_view.stale(now_us, self.limits.feed_stale_s * 5)
        if other_healthy:
            self.other_down_since_us = None
        elif self.other_down_since_us is None:
            self.other_down_since_us = now_us
        cutoff = now_us - 300_000_000
        while self._fill_usd_5m and self._fill_usd_5m[0][0] < cutoff:
            self._fill_usd_5m.popleft()
        ctx = StrategyContext(
            now_us=now_us, venue=self.venue, market=m, view=view, params=self.session,
            inventory=self.state.position(self.venue, self.base), entry_price=self.state.entry.get((self.venue, self.base)),
            other_market=other_m, other_view=other_view,
            other_inventory=self.state.position(self.other_venue, self.base) if self.other_venue else Z,
            session_progress=self.clock.progress(now_us), quoting_allowed=ok and mode is not BudgetMode.CANCELS_ONLY,
            quoting_block_reason=why if not ok else ("budget: cancels only" if mode is BudgetMode.CANCELS_ONLY else ""),
            event_window=ev, off_hours=off_hours,
            hysteresis_mult=self.governor.hysteresis_mult(self.venue, self.account_index.get(self.venue, 0)),
            our_fill_usd_5m=sum(x for _, x in self._fill_usd_5m), other_venue_healthy=other_healthy,
            other_venue_down_s=(now_us - self.other_down_since_us) / 1e6 if self.other_down_since_us else 0.0)
        margin_x = self._margin_x_mm(now_us)
        if margin_x is not None:
            ctx.extra["margin_x_mm"] = margin_x
        if self.is_dn and self.calendar.active_events(now_us, self.base, {"earnings", "ex_dividend"}):
            ctx.extra["avoid_window"] = True
        return ctx

    def _margin_x_mm(self, now_us: int) -> float | None:
        worst = None
        for v in (Venue.ARCUS, Venue.LIGHTER_RH):
            pos = self.state.position(v, self.base)
            if pos == 0 or self.base not in self.markets.get(v, {}):
                continue
            view = self.hub.get(v, self.base)
            if view is None or view.mid() is None:
                continue
            mid = view.mid()
            assert mid is not None
            mm = abs(pos) * mid * self.markets[v][self.base].mmf
            eq = self._account(v).equity
            if mm > 0 and eq > 0:
                x = float(eq / mm)
                worst = x if worst is None else min(worst, x)
        return worst

    # ---------------------------------------------------------------- tick
    async def tick(self, now_us: int) -> StrategyOutput | None:
        self.now_us = now_us
        self.stats.ticks += 1
        state = self.clock.update(now_us)
        if state is SessionState.DONE or self.stopped:
            return None
        self.resize(now_us)
        for v in (self.venue, self.other_venue):
            if v is None:
                continue
            view = self.hub.get(v, self.base)
            if view is None:
                continue
            d = self.risk.safety_pause(view, now_us)
            if d:
                await self.execute(d, now_us)
            m = self.markets.get(v, {}).get(self.base)
            if m is not None:
                d2 = self.risk.band_or_oi(view, m)
                if d2 and d2.action is RiskAction.STOP_MARKET_QUOTING:
                    pass  # quoting gate reads market_stopped; the strategy's exit book handles inventory
        ctx = self.build_ctx(now_us)
        if ctx is None:
            return None
        if state is SessionState.WAITING:
            out = self.strategy.on_stop(ctx) if ctx.inventory != 0 else StrategyOutput(reason="outside session window")
        elif state is SessionState.EXITING:
            out = self.strategy.on_stop(ctx)
            if ctx.inventory == 0 and (not self.is_dn or ctx.other_inventory == 0):
                self.clock.exit_done()
            elif (now_us - self.clock.exit_started_us) / 1e6 > getattr(self.session, "exit_taker_after_s", 60):
                out.hedge_intents.append(self._taker_exit(ctx))
        else:
            why = self._stop_exit(ctx, now_us)
            if why:
                ctx.quoting_allowed, ctx.quoting_block_reason = False, why
            out = self.strategy.on_tick(ctx)
            if self.exit_since_us is not None and ctx.inventory != 0 and \
                    (now_us - self.exit_since_us) / 1e6 >= getattr(self.session, "exit_taker_after_s", 60):
                out.hedge_intents.append(replace(self._taker_exit(ctx), reason=f"{why}: maker exit timed out"))
                self.exit_since_us = now_us  # re-arm: one taker attempt per interval
        mode = str(getattr(getattr(self.strategy, "switcher", None), "current", getattr(self.strategy, "name", "")))
        if self.last_tick_us:
            self.stats.mode_time_s[mode] = self.stats.mode_time_s.get(mode, 0.0) + (now_us - self.last_tick_us) / 1e6
        self.last_tick_us = now_us
        if mode != self.last_mode:
            self.decisions.record("mode", f"session {self.sid} mode {self.last_mode or '-'} -> {mode}: {out.reason}",
                                  venue=self.venue.value, market=self.base, session=self.sid, ts_us=now_us)
            self.last_mode = mode
        await self.apply(out, ctx, now_us)
        await self.pnl_checks(now_us)
        return out

    def _stop_exit(self, ctx: StrategyContext, now_us: int) -> str | None:
        """Position stop, daily-stop exit and the cool-down after a position stop. Returns why quoting is blocked."""
        if self.is_dn:
            return None
        assert isinstance(self.session, MMSession)
        s, inv = self.session, ctx.inventory
        if self.too_small:
            if inv != 0 and self.exit_since_us is None:
                self.exit_since_us = now_us   # close what is left: maker first, then a taker order
            return self.too_small
        if self.venue in self.risk.venue_stopped_day:
            if inv == 0:
                self.exit_since_us = None
                return None  # the risk gate already blocks quoting until 00:00 UTC
            if self.exit_since_us is None:
                self.exit_since_us = now_us
            return "daily stop: closing the position"
        if self.pos_stop_active:
            if inv != 0:
                return "position stop: closing the position"
            self.pos_stop_active, self.exit_since_us = False, None
            self.cooldown_until_us = now_us + int(s.cooldown_s * 1e6)
        if now_us < self.cooldown_until_us:
            return f"cooling down after a position stop ({(self.cooldown_until_us - now_us) / 1e6:.0f} s left)"
        mid = ctx.view.mid()
        if s.pos_stop_usd and inv != 0 and ctx.entry_price is not None and mid is not None:
            upnl = inv * (mid - ctx.entry_price)
            if upnl <= -Decimal(str(s.pos_stop_usd)):
                self.pos_stop_active, self.exit_since_us = True, now_us
                d = RiskDecision("position_stop", RiskAction.PAUSE_QUOTES, self.venue, self.base,
                                 f"open position {inv} down ${-upnl:.2f} (stop ${s.pos_stop_usd:.2f})",
                                 f"position closed, then {s.cooldown_s:.0f} s")
                self.risk._log(d, now_us)
                self.stats.risk_events.append(d)
                if self.alerter is not None:
                    self.alerter.warn("position_stop", d.reason)
                return "position stop: closing the position"
        return None

    def _taker_exit(self, ctx: StrategyContext) -> OrderRequest:
        m = ctx.market
        mid = ctx.view.mid()
        assert mid is not None
        side = Side.SELL if ctx.inventory > 0 else Side.BUY
        px = mid * (Decimal("0.998") if side is Side.SELL else Decimal("1.002"))
        return OrderRequest(m.venue, m.base, side, m.round_price(px, is_bid=side is Side.BUY), abs(ctx.inventory),
                            TIF.IOC, reduce_only=True, tag="exit_ioc", reason="session exit: maker unwind timed out")

    async def apply(self, out: StrategyOutput, ctx: StrategyContext, now_us: int) -> None:
        for (v, b), desired in out.desired.items():
            m = self.markets[v][b]
            view = self.hub.get(v, b)
            if view is None:
                continue
            bb, ba = view.book.best_bid(), view.book.best_ask()
            bbo = BBOTicks(int(bb[0] / m.tick_size) if bb else None, int(ba[0] / m.tick_size) if ba else None)
            ai = self.account_index.get(v, 0)
            mult = self.governor.hysteresis_mult(v, ai)
            mode = self.governor.mode(v, ai)
            req = getattr(self.session, "requote", None)
            min_ticks = req.min_ticks if req else 2
            frac = req.min_frac_of_half_spread if req else 0.25
            tol = max(min_ticks, frac * out.half_spread_ticks) * (mult if math.isfinite(mult) else 1)
            cap = 30 if v is Venue.LIGHTER_RH else 40
            params = PlanParams(min_ticks=min_ticks, tol_ticks=tol, size_frac=req.min_size_frac if req else 0.2,
                                max_open_per_market=cap, allow_places=mode is not BudgetMode.CANCELS_ONLY,
                                allow_modifies=mode is not BudgetMode.CANCELS_ONLY)
            res = await self.om.sync(v, m, desired, bbo, params, why=out.reason or "strategy")
            self.stats.actions += len(res.actions)
            self.stats.rejects += len(res.rejected)
            if res.errors:
                self.stats.errors += len(res.errors)
                log.warning("sync_errors", venue=v.value, market=b, session=self.sid, data={"errors": res.errors[:5]})
        for h in out.hedge_intents:
            key = (h.venue, h.tag)
            if key in self.inflight_intents and now_us - self.inflight_intents[key] < 3_000_000:
                continue
            if h.base not in self.markets.get(h.venue, {}):
                continue
            h = replace(h, client_id=self.om.new_client_id(h.venue))
            try:
                await self.om.hedge(h, self.markets[h.venue][h.base])
                self.inflight_intents[key] = now_us
                self.stats.hedges += 1
            except Exception as e:
                self.stats.rejects += 1
                self.decisions.record("hedge_rejected", f"{h.reason}: {e}", venue=h.venue.value, market=h.base,
                                      session=self.sid, ts_us=now_us)

    # ---------------------------------------------------------------- events
    async def on_fill(self, f: Fill, now_us: int) -> None:
        self.now_us = now_us
        o = self.state.orders.get(f.client_id)
        tag = o.req.tag if o else f.tag
        f = replace(f, tag=tag)
        if not self.state.on_fill(f, self.sid, tag):
            return
        view = self.hub.get(f.venue, f.base)
        vmid = view.mid() if view is not None else None
        mid: Decimal = vmid if vmid is not None else f.price
        kind = "hedge" if tag in ("hedge", "dn_kill") else "strategy"
        self.ledger.on_fill(f, mid, kind=kind)
        self.ledger.mark(f.venue, f.base, mid, now_us)
        if f.venue is Venue.ARCUS:
            self.governor.for_arcus(self.account_index.get(Venue.ARCUS, 0)).record_fill(float(f.notional))
        self._fill_usd_5m.append((now_us, float(f.notional)))
        self.inflight_intents.pop((f.venue, tag), None)
        ctx = self.build_ctx(now_us)
        if ctx is not None:
            self.strategy.on_fill(ctx, f)
        self.decisions.record("fill", f"{tag or 'order'} filled", venue=f.venue.value, market=f.base, session=self.sid,
                              ts_us=now_us, side=f.side.value, price=str(f.price), size=str(f.size),
                              maker=f.is_maker, fee=str(f.fee))

    async def on_order_update(self, st: OrderState, now_us: int) -> None:
        o = self.state.on_update(st)
        self.om.on_order_update(st.client_id, st.status is not OrderStatus.PENDING_NEW)
        if st.status is OrderStatus.REJECTED and st.reject_reason:
            if o is not None:
                self.inflight_intents.pop((o.req.venue, o.req.tag), None)
            base = (o.req.base if o is not None else st.base) or self.base
            d = self.risk.on_reject(st.venue or self.venue, st.reject_reason, base, now_us)
            if d:
                await self.execute(d, now_us)  # alerts, and the quoting gate pulls this market's quotes
        if st.status.is_terminal and o is not None:
            self.inflight_intents.pop((o.req.venue, o.req.tag), None)

    # ---------------------------------------------------------------- risk
    async def pnl_checks(self, now_us: int) -> None:
        marks = {}
        for v in (Venue.ARCUS, Venue.LIGHTER_RH):
            view = self.hub.get(v, self.base)
            if view is not None and view.mid() is not None:
                mid = view.mid()
                assert mid is not None
                marks[(v, self.base)] = view.mark or mid
                self.ledger.mark(v, self.base, view.mark or mid, now_us)
        venues = [self.venue] + ([self.other_venue] if self.is_dn and self.other_venue else [])
        pnl = sum((self.ledger.breakdown(v, self.base, marks.get((v, self.base))).net for v in venues), Z)
        equity = self.capital + pnl
        if self.session_start_equity is None:
            self.session_start_equity = equity
        from bot.common.time import utc_date_str

        day = utc_date_str(now_us)
        self.day_start_equity.setdefault(day, equity)
        self.risk.roll_day(now_us)
        sl = getattr(self.session, "stop_loss_pct", 10.0)
        tp = getattr(self.session, "take_profit_pct", None)
        margin = self.size_capital
        for d in self.risk.on_pnl(venue=self.venue, session_id=self.sid, session_pnl=equity - self.session_start_equity,
                                  session_margin=margin, stop_loss_pct=sl, take_profit_pct=tp,
                                  day_pnl=equity - self.day_start_equity[day], capital=self.size_capital, equity=equity,
                                  is_dn=self.is_dn, ts_us=now_us, daily_stop_usd=self._usd("daily_stop_usd"),
                                  kill_usd=self._usd("kill_usd")):
            await self.execute(d, now_us)
        # liquidation distance per leg (A6.7)
        for v in venues:
            pos = self.state.position(v, self.base)
            view = self.hub.get(v, self.base)
            if pos == 0 or view is None or view.mid() is None:
                continue
            mid = view.mid()
            assert mid is not None
            coll = self._account(v).equity or (self.capital if not self.is_dn else self.capital / 2)
            _n_sig, dl = self.risk.liquidation_distance(v, self.base, collateral=coll, notional=abs(pos) * mid,
                                                      mmf=self.markets[v][self.base].mmf, sigma_1h=view.sigma_1h())
            if dl:
                await self.execute(dl, now_us)

    def _usd(self, name: str) -> Decimal | None:
        v = getattr(self.session, name, None)
        return Decimal(str(v)) if v is not None else None

    async def execute(self, d: RiskDecision, now_us: int) -> None:
        self.stats.risk_events.append(d)
        if self.alerter is not None:
            level = "crit" if d.action in (RiskAction.STOP_ALL, RiskAction.STOP_VENUE_CRIT, RiskAction.SAFE_MODE,
                                           RiskAction.HEDGE_FLATTEN) else "warn"
            getattr(self.alerter, level)(d.trigger, f"{d.action.value}: {d.reason}")
        venues = [d.venue] if d.venue else list(self.adapters)
        if d.action in (RiskAction.PAUSE_QUOTES, RiskAction.STOP_MARKET_QUOTING, RiskAction.NO_NEW_QUOTES):
            return  # the quoting gate removes quotes on the next tick; positions are kept
        if d.action in (RiskAction.STOP_VENUE_DAY, RiskAction.STOP_VENUE_CRIT, RiskAction.SAFE_MODE):
            for v in venues:
                if v in self.adapters:
                    await self.adapters[v].cancel_all(None)
            return
        if d.action in (RiskAction.FLATTEN_SESSION, RiskAction.STOP_ALL, RiskAction.HEDGE_FLATTEN):
            for v in venues:
                if v in self.adapters:
                    await self.adapters[v].cancel_all(None)
            self.clock.begin_exit(now_us)
            if d.action is RiskAction.STOP_ALL:
                self.stopped = True
                await self.flatten_all(now_us, "drawdown stop")
            return
        if d.action is RiskAction.REDUCE_HALF and d.venue and d.market:
            pos = self.state.position(d.venue, d.market)
            view = self.hub.get(d.venue, d.market)
            if pos != 0 and view is not None and view.mid() is not None:
                m = self.markets[d.venue][d.market]
                half = (abs(pos) / 2 / m.step_size).to_integral_value() * m.step_size
                if half > 0:
                    mid = view.mid()
                    assert mid is not None
                    side = Side.SELL if pos > 0 else Side.BUY
                    px = mid * (Decimal("0.995") if side is Side.SELL else Decimal("1.005"))
                    req = OrderRequest(d.venue, d.market, side, m.round_price(px, is_bid=side is Side.BUY), half,
                                       TIF.IOC, reduce_only=True, client_id=self.om.new_client_id(d.venue),
                                       tag="liq_reduce", reason=d.reason)
                    try:
                        await self.om.hedge(req, m)
                    except Exception as e:
                        log.error("reduce_failed", venue=d.venue.value, reason=str(e)[:200])

    async def flatten_all(self, now_us: int, why: str) -> None:
        for v in self.adapters:
            pos = self.state.position(v, self.base)
            view = self.hub.get(v, self.base)
            if pos == 0 or view is None or view.mid() is None or self.base not in self.markets.get(v, {}):
                continue
            m = self.markets[v][self.base]
            mid = view.mid()
            assert mid is not None
            side = Side.SELL if pos > 0 else Side.BUY
            px = mid * (Decimal("0.99") if side is Side.SELL else Decimal("1.01"))
            req = OrderRequest(v, self.base, side, m.round_price(px, is_bid=side is Side.BUY), abs(pos), TIF.IOC,
                               reduce_only=True, client_id=self.om.new_client_id(v), tag="flatten", reason=why)
            try:
                await self.om.hedge(req, m)
            except Exception as e:
                log.error("flatten_failed", venue=v.value, reason=str(e)[:200])
