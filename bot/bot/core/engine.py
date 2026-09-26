"""Session engine: the one place where market data, risk gates, a strategy, the order manager, venues, state and the
ledger meet. Paper and live both drive this class; only the clock and the adapters differ.

Per tick (default 1 s): gates (safety pause, band/OI, event window, budget) -> strategy.on_tick -> order-manager sync
-> IOC intents (deduped while in flight) -> PnL kill switches -> liquidation distance.
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
from bot.common.config import MMSession, RiskLimitsCfg
from bot.common.ids import ClientIdFactory
from bot.common.logging import DecisionLog, Log
from bot.common.time import US_PER_S
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
    iocs: int = 0
    risk_events: list[RiskDecision] = field(default_factory=list)
    mode_time_s: dict[str, float] = field(default_factory=dict)


REFUSED_ALERT_PER_MIN = 30   # pre-trade refusals in one minute that make an alert
BLOCKS = (("safety pause", "safety pause"), ("skip window", "skip window"), ("daily", "daily stop"),
          ("position stop", "position stop"), ("cooling down", "cooldown"), ("budget", "order budget"),
          ("event window", "event window"), ("repeated rejects", "rejects"), ("safe mode", "safe mode"),
          ("critical stop", "safe mode"), ("stopped", "stopped"), ("scout:", "paused by the scout"),
          ("telegram", "paused by you"), ("band", "market stopped"), ("oi cap", "market stopped"),
          ("market is", "market stopped"), ("too small", "capital too small"), ("no book", "no book"))


def block_category(why: str) -> str:
    """A short name for why quoting is blocked (the dashboard's breakdown)."""
    w = why.lower()
    return next((name for key, name in BLOCKS if key in w), why[:40] or "blocked")


@dataclass
class QuoteStats:
    """Seconds of one UTC day: quoting or blocked (and why), with an order resting on each side, and at the best
    price. Answers "why no fills?": a bot that is paused, or rests behind the best price, is rarely filled."""

    day: str = ""
    seconds: float = 0.0
    quoting: float = 0.0
    blocked: dict[str, float] = field(default_factory=dict)
    bid: float = 0.0          # seconds with a buy resting (not reduce-only)
    ask: float = 0.0
    bid_touch: float = 0.0    # ... at or inside the best bid
    ask_touch: float = 0.0
    bid_ticks: float = 0.0    # ticks behind the best bid, summed over the seconds a buy rests
    ask_ticks: float = 0.0
    refused: int = 0          # orders the bot's own pre-trade check refused (they never reach the venue)
    refused_why: str = ""     # the last such reason

    def add(self, dt: float, why: str, bid: tuple[float, float] | None, ask: tuple[float, float] | None) -> None:
        """dt seconds; why = "" while quoting; bid/ask = (our best price, the book's best price) in ticks, when an
        order rests on that side."""
        self.seconds += dt
        if why:
            cat = block_category(why)
            self.blocked[cat] = self.blocked.get(cat, 0.0) + dt
        else:
            self.quoting += dt
        if bid is not None:
            self.bid += dt
            self.bid_touch += dt if bid[0] >= bid[1] else 0.0
            self.bid_ticks += dt * max(0.0, bid[1] - bid[0])
        if ask is not None:
            self.ask += dt
            self.ask_touch += dt if ask[0] <= ask[1] else 0.0
            self.ask_ticks += dt * max(0.0, ask[0] - ask[1])


class SessionEngine:
    def __init__(self, *, session: MMSession, strategy: Any, hub: MarketDataHub,
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
        self.sid = session.session_id
        self.venue = Venue(session.venue)
        self.account_index = {self.venue: session.account_index}
        self.capital = Decimal(str(session.capital_usd))
        risk.safety = session.safety_pause  # per-session thresholds (one session per process)
        self.clock = SessionClock(session.session)
        self.base = session.market.upper()
        ids = {self.venue: ClientIdFactory(session.mode, session_num)}
        self.now_us = 0
        self.om = OrderManager(adapters=adapters, state=state, risk=risk, governor=governor, decisions=decisions,
                               ids=ids, session=self.sid, account_index=self.account_index, now_fn=lambda: self.now_us)
        self.ids = ids
        self.stats = EngineStats()
        self.quotes = QuoteStats()
        self._refused_at: deque[int] = deque()           # pre-trade refusals in the last minute
        self._refused_alert_us: dict[str, int] = {}      # last alert per check
        self.session_start_equity: Decimal | None = None
        self.run_pnl: Decimal | None = None             # the whole run's PnL when it has a loss limit (sl=)
        self._run_carry: Decimal | None = None          # ... that earlier processes of the same run made
        self._run_saved_us = 0
        self.day_start_equity: dict[str, Decimal] = {}
        self.inflight_intents: dict[tuple[Venue, str], int] = {}
        self._fill_usd_5m: deque[tuple[int, float]] = deque()
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
        if s.sizing is None or not s.sizing.follow_equity:
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
        ok, why = self.risk.quoting_allowed(self.venue, self.base, now_us)
        window = self.calendar.in_skip_window(now_us, self.session.session.skip_et)
        if ok and window:
            ok, why = False, f"skip window {window} New York time: no new quotes"
        skip = set(self.session.session.skip_events or [])
        ev = self.calendar.in_event_window(now_us, self.base, skip) if skip else False
        off_hours = self.venue is Venue.ARCUS and bool(view.is_outside_rth) and m.rth is not None
        mode = self.governor.mode(self.venue, self.account_index.get(self.venue, 0))
        cutoff = now_us - 300_000_000
        while self._fill_usd_5m and self._fill_usd_5m[0][0] < cutoff:
            self._fill_usd_5m.popleft()
        ctx = StrategyContext(
            now_us=now_us, venue=self.venue, market=m, view=view, params=self.session,
            inventory=self.state.position(self.venue, self.base), entry_price=self.state.entry.get((self.venue, self.base)),
            session_progress=self.clock.progress(now_us), quoting_allowed=ok and mode is not BudgetMode.CANCELS_ONLY,
            quoting_block_reason=why if not ok else ("budget: cancels only" if mode is BudgetMode.CANCELS_ONLY else ""),
            event_window=ev, off_hours=off_hours, our_fill_usd_5m=sum(x for _, x in self._fill_usd_5m))
        return ctx

    # ---------------------------------------------------------------- tick
    async def tick(self, now_us: int) -> StrategyOutput | None:
        self.now_us = now_us
        self.stats.ticks += 1
        state = self.clock.update(now_us)
        if state is SessionState.DONE or self.stopped:
            return None
        self.resize(now_us)
        view = self.hub.get(self.venue, self.base)
        if view is not None:
            d = self.risk.safety_pause(view, now_us)
            if d:
                await self.execute(d, now_us)
            m = self.markets.get(self.venue, {}).get(self.base)
            if m is not None:
                self.risk.band_or_oi(view, m)   # the quoting gate reads market_stopped; the exit book handles inventory
        ctx = self.build_ctx(now_us)
        if ctx is None:
            return None
        if state is SessionState.WAITING:
            out = self.strategy.on_stop(ctx) if ctx.inventory != 0 else StrategyOutput(reason="outside session window")
        elif state is SessionState.EXITING:
            out = self.strategy.on_stop(ctx)
            if ctx.inventory == 0:
                self.clock.exit_done()
            elif (now_us - self.clock.exit_started_us) / 1e6 > self.session.exit_taker_after_s:
                out.ioc_intents.append(self._taker_exit(ctx))
        else:
            why = self._stop_exit(ctx, now_us)
            if why:
                ctx.quoting_allowed, ctx.quoting_block_reason = False, why
            out = self.strategy.on_tick(ctx)
            if self.exit_since_us is not None and ctx.inventory != 0 and \
                    (now_us - self.exit_since_us) / 1e6 >= self.session.exit_taker_after_s:
                out.ioc_intents.append(replace(self._taker_exit(ctx), reason=f"{why}: maker exit timed out"))
                self.exit_since_us = now_us  # re-arm: one taker attempt per interval
        mode = str(getattr(self.strategy, "name", ""))
        dt = min(5.0, (now_us - self.last_tick_us) / 1e6) if self.last_tick_us else 0.0
        if self.last_tick_us:
            self.stats.mode_time_s[mode] = self.stats.mode_time_s.get(mode, 0.0) + (now_us - self.last_tick_us) / 1e6
        self.last_tick_us = now_us
        if mode != self.last_mode:
            self.decisions.record("mode", f"session {self.sid} mode {self.last_mode or '-'} -> {mode}: {out.reason}",
                                  venue=self.venue.value, market=self.base, session=self.sid, ts_us=now_us)
            self.last_mode = mode
        await self.apply(out, ctx, now_us)
        if dt:
            self._count_quotes(ctx, state, now_us, dt)
        await self.pnl_checks(now_us)
        return out

    def _refused(self, why: str, n: int, now_us: int) -> None:
        """Our own pre-trade check refused orders: count them for the dashboard, and warn once (at most every 30 min
        per check) when it keeps refusing for a minute. Such orders never reach the venue, so without this a bot can
        sit for hours with nothing on the book and no alert (QQQ, 2026-09-25: ~4,900 refusals in 2 h)."""
        self.quotes.refused += n
        self.quotes.refused_why = why[:160]
        self._refused_at.extend([now_us] * n)
        while self._refused_at and now_us - self._refused_at[0] > 60 * US_PER_S:
            self._refused_at.popleft()
        check = why.split(":")[0]
        if len(self._refused_at) >= REFUSED_ALERT_PER_MIN and self.alerter is not None and \
                now_us - self._refused_alert_us.get(check, 0) > 1800 * US_PER_S:
            self._refused_alert_us[check] = now_us
            self.alerter.warn("orders_refused", f"{self.base}: the bot's own pre-trade check refused "
                              f"{len(self._refused_at)} orders in the last minute ({why[:160]}). Nothing reaches "
                              "Arcus; /status")

    def _count_quotes(self, ctx: StrategyContext, state: SessionState, now_us: int, dt: float) -> None:
        from bot.common.time import utc_date_str

        if state is not SessionState.RUNNING:
            why = "outside the session window"
        elif not ctx.quoting_allowed:
            why = ctx.quoting_block_reason or "blocked"
        else:
            why = "event window" if ctx.event_window else ""
        book, tick = ctx.view.book, float(ctx.market.tick_size)
        mine = [o for o in self.state.open_orders(self.venue, self.base) if not o.req.reduce_only]
        sides: list[tuple[float, float] | None] = []
        for is_bid, best in ((True, book.best_bid()), (False, book.best_ask())):
            px = [float(o.req.price) for o in mine if (o.req.side is Side.BUY) is is_bid]
            if px and best is not None and tick > 0:
                ours = max(px) if is_bid else min(px)
                sides.append((ours / tick, float(best[0]) / tick))
            else:
                sides.append(None)
        day = utc_date_str(now_us)
        if self.quotes.day != day:
            self.quotes = QuoteStats(day=day)
        self.quotes.add(dt, why, sides[0], sides[1])

    def _stop_exit(self, ctx: StrategyContext, now_us: int) -> str | None:
        """Position stop, daily-stop exit and the cool-down after a position stop. Returns why quoting is blocked."""
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
            req = self.session.requote
            tol = max(req.min_ticks, req.min_frac_of_half_spread * out.half_spread_ticks) * (mult if math.isfinite(mult) else 1)
            params = PlanParams(min_ticks=req.min_ticks, tol_ticks=tol, size_frac=req.min_size_frac,
                                max_open_per_market=40, allow_places=mode is not BudgetMode.CANCELS_ONLY,
                                allow_modifies=mode is not BudgetMode.CANCELS_ONLY,
                                # a venue that cannot modify reliably requotes with cancel + place (ArcusAdapter)
                                allow_modify=bool(getattr(self.om.adapters.get(v), "use_modify", True)))
            res = await self.om.sync(v, m, desired, bbo, params, why=out.reason or "strategy")
            self.stats.actions += len(res.actions)
            self.stats.rejects += len(res.rejected)
            if res.rejected:
                self._refused(res.rejected[-1][1], len(res.rejected), now_us)
            if res.errors:
                self.stats.errors += len(res.errors)
                log.warning("sync_errors", venue=v.value, market=b, session=self.sid, data={"errors": res.errors[:5]})
        for h in out.ioc_intents:
            key = (h.venue, h.tag)
            if key in self.inflight_intents and now_us - self.inflight_intents[key] < 3_000_000:
                continue
            if h.base not in self.markets.get(h.venue, {}):
                continue
            h = replace(h, client_id=self.om.new_client_id(h.venue))
            try:
                await self.om.ioc(h, self.markets[h.venue][h.base])
                self.inflight_intents[key] = now_us
                self.stats.iocs += 1
            except Exception as e:
                self.stats.rejects += 1
                self.decisions.record("ioc_rejected", f"{h.reason}: {e}", venue=h.venue.value, market=h.base,
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
        self.ledger.on_fill(f, mid)
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
        view = self.hub.get(self.venue, self.base)
        mark: Decimal | None = None
        if view is not None and view.mid() is not None:
            mark = view.mark or view.mid()
            assert mark is not None
            self.ledger.mark(self.venue, self.base, mark, now_us)
        equity = self.capital + self.ledger.breakdown(self.venue, self.base, mark).net
        if self.session_start_equity is None:
            self.session_start_equity = equity
        from bot.common.time import utc_date_str

        day = utc_date_str(now_us)
        self.day_start_equity.setdefault(day, equity)
        self.risk.roll_day(now_us)
        s = self.session
        if s.max_loss_usd:
            self.run_pnl = self._run_total(equity - self.session_start_equity, now_us)
        for d in self.risk.on_pnl(venue=self.venue, session_id=self.sid, session_pnl=equity - self.session_start_equity,
                                  session_margin=self.size_capital, stop_loss_pct=s.stop_loss_pct,
                                  take_profit_pct=s.take_profit_pct, day_pnl=equity - self.day_start_equity[day],
                                  capital=self.size_capital, equity=equity, ts_us=now_us,
                                  daily_stop_usd=_usd(s.daily_stop_usd), kill_usd=_usd(s.kill_usd),
                                  run_pnl=self.run_pnl, run_limit_usd=_usd(s.max_loss_usd)):
            await self.execute(d, now_us)
        # liquidation distance (A6.7)
        pos = self.state.position(self.venue, self.base)
        if pos != 0 and view is not None and view.mid() is not None:
            mid = view.mid()
            assert mid is not None
            coll = self._account(self.venue).equity or self.capital
            _n_sig, dl = self.risk.liquidation_distance(self.venue, self.base, collateral=coll, notional=abs(pos) * mid,
                                                      mmf=self.markets[self.venue][self.base].mmf,
                                                      sigma_1h=view.sigma_1h())
            if dl:
                await self.execute(dl, now_us)

    def _run_total(self, pnl: Decimal, now_us: int) -> Decimal:
        """The run's PnL across restarts: what earlier processes of this run (the pilot's run_id) made, kept in kv
        and saved every 5 s, plus this process's. A session without a run_id counts this process only."""
        key = f"run_pnl:{self.session.run_id}" if self.session.run_id else ""
        if self._run_carry is None:
            self._run_carry = Decimal(self.state.kv_get(key) or 0) if key else Decimal(0)
        total = self._run_carry + pnl
        if key and now_us - self._run_saved_us >= 5 * US_PER_S:
            self._run_saved_us = now_us
            self.state.kv_set(key, str(total))
        return total

    async def execute(self, d: RiskDecision, now_us: int) -> None:
        self.stats.risk_events.append(d)
        if self.alerter is not None:
            level = "crit" if d.action in (RiskAction.STOP_ALL, RiskAction.STOP_VENUE_CRIT, RiskAction.SAFE_MODE) else "warn"
            getattr(self.alerter, level)(d.trigger, f"{d.action.value}: {d.reason}")
        venues = [d.venue] if d.venue else list(self.adapters)
        if d.action in (RiskAction.PAUSE_QUOTES, RiskAction.STOP_MARKET_QUOTING, RiskAction.NO_NEW_QUOTES):
            return  # the quoting gate removes quotes on the next tick; positions are kept
        if d.action in (RiskAction.STOP_VENUE_DAY, RiskAction.STOP_VENUE_CRIT, RiskAction.SAFE_MODE):
            for v in venues:
                if v in self.adapters:
                    await self.adapters[v].cancel_all(None)
            return
        if d.action in (RiskAction.FLATTEN_SESSION, RiskAction.STOP_ALL):
            for v in venues:
                if v in self.adapters:
                    await self.adapters[v].cancel_all(None)
            self.clock.begin_exit(now_us)
            if d.action is RiskAction.STOP_ALL:
                self.stopped = True
                await self.flatten_all(now_us, "run loss limit" if d.trigger == "run_loss" else "drawdown stop")
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
                        await self.om.ioc(req, m)
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
                await self.om.ioc(req, m)
            except Exception as e:
                log.error("flatten_failed", venue=v.value, reason=str(e)[:200])


def _usd(v: float | None) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None
