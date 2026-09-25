"""Risk engine (P2 task 5): pre-trade checks and kill switches (Appendix E7).

Pre-trade (reject BEFORE sending): tick/band alignment and step alignment, venue minimums, max size, post-only
won't cross, free collateral after the order (Arcus off-hours IMF when isOutsideRth), Arcus OI-cap headroom,
oracle-deviation band, per-market position/inventory caps, leverage caps (the session's leverage_max within
risk.max_leverage_mm).

Kill switches: each trigger produces a RiskDecision with an action and a resume rule. Decisions are pure outputs;
the bot runner executes them (cancel quotes, flatten, stop) and logs them. State that must survive a restart
(stops needing manual resume) is persisted by the caller through the StateStore kv.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from bot.common.config import RiskLimitsCfg, SafetyPauseCfg
from bot.common.decimal import InexactConversion, to_units_exact
from bot.common.errors import CRITICAL_REJECTS, PreTradeReject
from bot.common.logging import DecisionLog
from bot.common.time import US_PER_S, utc_date_str
from bot.core.calendar import TradingCalendar
from bot.core.marketdata import MarketView
from bot.venues.base import TIF, Market, OrderRequest, Side, Venue

# Rejections that are part of normal quoting (a fast market crossing a post-only quote, an IOC remainder, a cancel or
# modify racing a fill) never trip the reject breaker.
ROUTINE_REJECTS = frozenset({"POST_ONLY_WOULD_CROSS", "IOC_CANCELED", "COULD_NOT_FILL", "FOK_FAILED",
                             "ORDER_NOT_FOUND", "ORDER_NOT_FOUND_FOR_MODIFY", "MODIFY_SUPERSEDED_BY_CANCEL",
                             "MODIFY_SIZE_ALREADY_FILLED", "POST_ONLY_WOULD_CROSS_MODIFY"})
REJECT_LIMIT = 5
REJECT_WINDOW_S = 60
REJECT_BACKOFF_S = 60.0
REJECT_BACKOFF_MAX_S = 600.0


class RiskAction(StrEnum):
    PAUSE_QUOTES = "pause_quotes"  # cancel quotes, keep position
    NO_NEW_QUOTES = "no_new_quotes"  # event window: no new quotes, tighten exits
    STOP_MARKET_QUOTING = "stop_market_quoting"  # band zone / OI cap
    REDUCE_HALF = "reduce_half"  # liquidation distance
    FLATTEN_SESSION = "flatten_session"  # session SL/TP
    STOP_VENUE_DAY = "stop_venue_day"  # daily loss
    STOP_ALL = "stop_all"  # drawdown: flatten everything, manual resume
    FREEZE_REQUOTES = "freeze_requotes"  # budget
    SAFE_MODE = "safe_mode"  # unexpected error / DMS failures: cancel quotes, keep positions, manual resume
    STOP_VENUE_CRIT = "stop_venue_crit"  # SELF_TRADE / GEO_RESTRICTED


@dataclass(frozen=True, slots=True)
class RiskDecision:
    trigger: str
    action: RiskAction
    venue: Venue | None
    market: str | None
    reason: str
    resume: str


@dataclass
class AccountSnapshot:
    equity: Decimal = Decimal(0)
    free_collateral: Decimal = Decimal(0)
    leverage_used: Decimal = Decimal(0)


@dataclass
class RiskContext:
    """Providers wired by the runner (live, paper or sim)."""

    bbo: Callable[[Venue, str], tuple[Decimal | None, Decimal | None]] = lambda v, b: (None, None)
    oracle: Callable[[Venue, str], Decimal | None] = lambda v, b: None
    account: Callable[[Venue], AccountSnapshot] = lambda v: AccountSnapshot()
    position: Callable[[Venue, str], Decimal] = lambda v, b: Decimal(0)
    # resting notional on one side, excluding `exclude_client_id` (the order being modified)
    open_notional: Callable[[Venue, str, Side, str | None], Decimal] = lambda v, b, s, x: Decimal(0)
    oi: Callable[[Venue, str], tuple[Decimal | None, Decimal | None]] = lambda v, b: (None, None)


@dataclass
class MarketLimits:
    position_cap_usd: Decimal = Decimal(30)
    leverage_cap: Decimal = Decimal(5)
    oracle_dev_frac: Decimal = Decimal("0.05")


@dataclass
class RiskEngine:
    limits: RiskLimitsCfg = field(default_factory=RiskLimitsCfg)
    safety: SafetyPauseCfg = field(default_factory=SafetyPauseCfg)
    calendar: TradingCalendar = field(default_factory=TradingCalendar)
    ctx: RiskContext = field(default_factory=RiskContext)
    decisions: DecisionLog | None = None
    market_limits: dict[tuple[Venue, str], MarketLimits] = field(default_factory=dict)
    # state
    paused_until_us: dict[tuple[Venue, str], int] = field(default_factory=dict)
    reject_pause_until_us: dict[tuple[Venue, str], tuple[int, str]] = field(default_factory=dict)
    _reject_hist: dict[tuple[Venue, str, str], list[int]] = field(default_factory=dict)
    _reject_backoff_s: dict[tuple[Venue, str], float] = field(default_factory=dict)
    _pause_normal_since: dict[tuple[Venue, str], int] = field(default_factory=dict)
    venue_stopped_day: dict[Venue, str] = field(default_factory=dict)
    venue_crit_stop: dict[Venue, str] = field(default_factory=dict)
    safe_mode: dict[Venue, str] = field(default_factory=dict)
    all_stopped: str | None = None
    reduce_active: set[tuple[Venue, str]] = field(default_factory=set)
    market_stopped: dict[tuple[Venue, str], str] = field(default_factory=dict)
    # Operator pause (Telegram / `bot pause`): only the operator clears it, unlike market_stopped, which the
    # band/OI check lifts on its own. Quoting stops; the strategy's reduce-only exit book keeps working.
    operator_paused: dict[tuple[Venue, str], str] = field(default_factory=dict)
    equity_peak: dict[str, Decimal] = field(default_factory=dict)

    def _log(self, d: RiskDecision, ts_us: int | None = None) -> RiskDecision:
        if self.decisions is not None:
            self.decisions.record(f"risk:{d.action.value}", d.reason, venue=d.venue.value if d.venue else None,
                                  market=d.market, ts_us=ts_us, trigger=d.trigger, resume=d.resume)
        return d

    def limits_for(self, venue: Venue, base: str) -> MarketLimits:
        return self.market_limits.setdefault((venue, base), MarketLimits())

    # ================================================================ pre-trade
    def check(self, req: OrderRequest, m: Market, *, modify: bool = False, ts_us: int | None = None) -> None:
        v, b = req.venue, req.base
        if (self.all_stopped or v in self.venue_crit_stop or v in self.safe_mode) and not req.reduce_only:
            raise PreTradeReject("halted", self.all_stopped or self.venue_crit_stop.get(v) or self.safe_mode[v])
        if v in self.venue_stopped_day and not req.reduce_only:
            raise PreTradeReject("venue_stopped", f"daily loss stop until next UTC day ({self.venue_stopped_day[v]})")
        # tick / step alignment (band tick for the price; signing divisor is the top-level tick)
        band_tick = m.tick_at(req.price)
        try:
            to_units_exact(req.price, band_tick)
            to_units_exact(req.price, m.tick_size)
            to_units_exact(req.size, m.step_size)
        except InexactConversion as e:
            raise PreTradeReject("alignment", str(e)) from e
        if req.size <= 0 or req.price <= 0:
            raise PreTradeReject("alignment", "non-positive price or size")
        # minimums and max size
        if req.size < m.min_size:
            raise PreTradeReject("min_size", f"{req.size} < {m.min_size}")
        exempt = req.reduce_only and v is Venue.ARCUS
        if not exempt and req.notional < m.min_notional:
            raise PreTradeReject("min_notional", f"${req.notional:.2f} < ${m.min_notional}")
        if m.max_size is not None and req.size > m.max_size:
            raise PreTradeReject("max_size", f"{req.size} > {m.max_size}")
        # post-only must not cross
        if req.tif is TIF.POST_ONLY:
            bid, ask = self.ctx.bbo(v, b)
            if req.side is Side.BUY and ask is not None and req.price >= ask:
                raise PreTradeReject("post_only_cross", f"bid {req.price} >= best ask {ask}")
            if req.side is Side.SELL and bid is not None and req.price <= bid:
                raise PreTradeReject("post_only_cross", f"ask {req.price} <= best bid {bid}")
        # oracle deviation band (Arcus rejects OracleDeviation; market orders must be within 10% of mark)
        ora = self.ctx.oracle(v, b)
        lim = self.limits_for(v, b)
        if ora is not None and ora > 0:
            dev = abs(req.price - ora) / ora
            band = Decimal("0.10") if req.tif in (TIF.IOC, TIF.FOK) else lim.oracle_dev_frac
            if dev > band:
                raise PreTradeReject("oracle_deviation", f"price {req.price} is {dev:.2%} from oracle {ora}")
        if req.reduce_only:
            return
        # position cap: this order alone must not take the position beyond the cap if it fills;
        # exposure cap: position + every resting same-side order + this one must stay within 2x the cap.
        pos = self.ctx.position(v, b)
        px = req.price
        resting = self.ctx.open_notional(v, b, req.side, req.client_id if modify else None)
        after = pos * px + (req.notional if req.side is Side.BUY else -req.notional)
        increasing = abs(after) > abs(pos * px)
        if increasing and abs(after) > lim.position_cap_usd:
            raise PreTradeReject("position_cap", f"|pos| would reach ${abs(after):.2f} > cap ${lim.position_cap_usd}")
        worst = abs(after) + resting if (after >= 0) == (req.side is Side.BUY) else abs(after)
        if increasing and worst > 2 * lim.position_cap_usd:
            raise PreTradeReject("exposure_cap", f"position + resting ${worst:.2f} > 2x cap ${lim.position_cap_usd}")
        # OI cap headroom (Arcus): OI-increasing fills are rejected at the cap
        oi, cap = self.ctx.oi(v, b)
        if cap is not None and oi is not None and oi * px + req.notional > cap:
            raise PreTradeReject("oi_cap", f"market OI ${oi * px:.0f} + order would exceed cap ${cap:.0f}")
        # free collateral and leverage (use off-hours IMF when the RWA market is outside RTH)
        acct = self.ctx.account(v)
        imf = m.offhours_imf if (m.is_outside_rth and m.offhours_imf) else m.imf
        need = req.notional * imf
        if acct.equity > 0:
            if acct.free_collateral - need < 0:
                raise PreTradeReject("free_collateral", f"needs ${need:.2f}, free ${acct.free_collateral:.2f}")
            lev = abs(after) / acct.equity
            if lev > lim.leverage_cap:
                raise PreTradeReject("leverage_cap", f"gross leverage {lev:.2f}x > {lim.leverage_cap}x")

    # ================================================================ kill switches
    def on_pnl(self, *, venue: Venue, session_id: str, session_pnl: Decimal, session_margin: Decimal,
               stop_loss_pct: float, take_profit_pct: float | None, day_pnl: Decimal, capital: Decimal,
               equity: Decimal, ts_us: int, daily_stop_usd: Decimal | None = None,
               kill_usd: Decimal | None = None) -> list[RiskDecision]:
        out: list[RiskDecision] = []
        if session_margin > 0 and session_pnl <= -session_margin * Decimal(stop_loss_pct) / 100:
            out.append(self._log(RiskDecision("session_sl", RiskAction.FLATTEN_SESSION, venue, None,
                                              f"session {session_id} loss ${-session_pnl:.2f} >= {stop_loss_pct}% of margin",
                                              "next scheduled session"), ts_us))
        if take_profit_pct and session_margin > 0 and session_pnl >= session_margin * Decimal(take_profit_pct) / 100:
            out.append(self._log(RiskDecision("session_tp", RiskAction.FLATTEN_SESSION, venue, None,
                                              f"session {session_id} profit ${session_pnl:.2f} >= TP {take_profit_pct}%",
                                              "next scheduled session"), ts_us))
        day = utc_date_str(ts_us)
        day_lim = daily_stop_usd if daily_stop_usd is not None else capital * Decimal(self.limits.daily_loss_pct) / 100
        if day_lim > 0 and day_pnl < -day_lim and self.venue_stopped_day.get(venue) != day:
            self.venue_stopped_day[venue] = day
            out.append(self._log(RiskDecision("daily_loss", RiskAction.STOP_VENUE_DAY, venue, None,
                                              f"day loss ${-day_pnl:.2f} > daily stop ${day_lim:.2f}",
                                              "manual or next UTC day"), ts_us))
        key = venue.value
        peak = max(self.equity_peak.get(key, equity), equity)
        self.equity_peak[key] = peak
        dd_lim = kill_usd if kill_usd is not None else capital * Decimal(self.limits.drawdown_pct) / 100
        if dd_lim > 0 and (peak - equity) > dd_lim and not self.all_stopped:
            self.all_stopped = f"drawdown ${peak - equity:.2f} > ${dd_lim:.2f} from the peak"
            out.append(self._log(RiskDecision("drawdown", RiskAction.STOP_ALL, None, None, self.all_stopped,
                                              "manual only (bot resume)"), ts_us))
        return out

    def roll_day(self, ts_us: int) -> None:
        day = utc_date_str(ts_us)
        for v, d in list(self.venue_stopped_day.items()):
            if d != day:
                del self.venue_stopped_day[v]

    def safety_pause(self, view: MarketView, now_us: int) -> RiskDecision | None:
        """6 sigma 1-s move, spread > 3x its 1 h median, or displayed depth < 30% of median -> pause quotes.
        Resume after `resume_s` of normal readings."""
        key = (view.venue, view.base)
        s = self.safety
        sig = max(view.vol_1s.sigma(), s.min_sigma_1s)
        reasons = []
        if view.vol_1s.n > 60 and abs(view.last_move_1s) > s.move_sigma_1s * sig:
            reasons.append(f"1s move {view.last_move_1s:.5f} > {s.move_sigma_1s} sigma ({sig:.5f})")
        sb = view.book.spread_bps()
        med = view.spread_med_1h.median()
        if (sb is not None and med is not None and len(view.spread_med_1h.buf) >= s.warmup_samples
                and sb > s.spread_x_median * med and sb - med > s.spread_min_excess_bps):
            reasons.append(f"spread {sb:.1f} bps > {s.spread_x_median}x median {med:.1f}")
        dm = view.depth_med_1h.median()
        if dm and len(view.depth_med_1h.buf) >= s.warmup_samples:
            d = float(view.book.depth_notional(True, within_bps=25) + view.book.depth_notional(False, within_bps=25))
            if d < s.depth_frac_min * dm:
                reasons.append(f"depth ${d:.0f} < {s.depth_frac_min:.0%} of median ${dm:.0f}")
        if reasons:
            self._pause_normal_since.pop(key, None)
            already = self.paused_until_us.get(key, 0) > now_us
            self.paused_until_us[key] = now_us + int(s.resume_s * US_PER_S)
            if not already:
                return self._log(RiskDecision("safety_pause", RiskAction.PAUSE_QUOTES, view.venue, view.base,
                                              "; ".join(reasons), f"{s.resume_s:.0f} s of normal readings"), now_us)
        return None

    def event_window(self, venue: Venue, base: str, category: str, ts_us: int, skip: set[str]) -> RiskDecision | None:
        evs = self.calendar.active_events(ts_us, base, skip)
        if evs:
            return RiskDecision("event_window", RiskAction.NO_NEW_QUOTES, venue, base,
                                ", ".join(sorted({e.kind for e in evs})), "window end")
        return None

    def band_or_oi(self, view: MarketView, m: Market) -> RiskDecision | None:
        key = (view.venue, view.base)
        why = []
        status = view.status or (str(m.status).upper() if view.venue is Venue.ARCUS else None)
        if view.venue is Venue.ARCUS and status and status != "ONLINE":
            why.append(f"market is {status}")   # halted, delisted, or a new listing not yet trading
        if view.venue is Venue.ARCUS and (view.upper_in_zone or view.lower_in_zone):
            why.append("Arcus off-hours band in expansion zone")
        if view.oi is not None and view.oi_cap and view.mid() is not None:
            mid = view.mid()
            assert mid is not None
            if view.oi * mid >= view.oi_cap * Decimal("0.98"):
                why.append("OI cap reached")
        if why:
            new = key not in self.market_stopped
            self.market_stopped[key] = "; ".join(why)
            d = RiskDecision("band_or_oi", RiskAction.STOP_MARKET_QUOTING, view.venue, view.base, "; ".join(why),
                             "zone cleared")
            return self._log(d) if new else d
        self.market_stopped.pop(key, None)
        return None

    def liquidation_distance(self, venue: Venue, base: str, *, collateral: Decimal, notional: Decimal, mmf: Decimal,
                             sigma_1h: float) -> tuple[float, RiskDecision | None]:
        """A6.7: adverse move to liquidation ~ (C - N x MMF) / N, in sigma units of 1 h returns."""
        if notional <= 0:
            self.reduce_active.discard((venue, base))
            return math.inf, None
        dist = float((collateral - notional * mmf) / notional)
        n_sigma = dist / sigma_1h if sigma_1h > 0 else math.inf
        key = (venue, base)
        if n_sigma < self.limits.liq_distance_sigma and key not in self.reduce_active:
            self.reduce_active.add(key)
            return n_sigma, self._log(RiskDecision(
                "liq_distance", RiskAction.REDUCE_HALF, venue, base,
                f"distance to liquidation {dist:.2%} = {n_sigma:.1f} sigma < {self.limits.liq_distance_sigma}",
                f"back above {self.limits.liq_resume_sigma} sigma"))
        if n_sigma >= self.limits.liq_resume_sigma:
            self.reduce_active.discard(key)
        return n_sigma, None

    def on_reject(self, venue: Venue, reason: str, base: str | None = None,
                  now_us: int | None = None) -> RiskDecision | None:
        if reason in CRITICAL_REJECTS:
            self.venue_crit_stop[venue] = reason
            return self._log(RiskDecision("critical_reject", RiskAction.STOP_VENUE_CRIT, venue, None,
                                          f"unexpected {reason}: stop venue", "manual"))
        if base is None or now_us is None or reason in ROUTINE_REJECTS:
            return None
        # Reject breaker: the same rejection over and over (no margin, price too far from the oracle, a cap) means
        # re-sending is pointless and burns the order budget. Pause this market with a doubling back-off.
        q = self._reject_hist.setdefault((venue, base, reason), [])
        q.append(now_us)
        q[:] = [t for t in q if now_us - t <= REJECT_WINDOW_S * US_PER_S]
        if len(q) < REJECT_LIMIT:
            return None
        q.clear()
        key = (venue, base)
        backoff = min(REJECT_BACKOFF_MAX_S, self._reject_backoff_s.get(key, REJECT_BACKOFF_S / 2) * 2)
        self._reject_backoff_s[key] = backoff
        self.reject_pause_until_us[key] = (now_us + int(backoff * US_PER_S), reason)
        return self._log(RiskDecision("reject_breaker", RiskAction.PAUSE_QUOTES, venue, base,
                                      f"{REJECT_LIMIT} x {reason} within {REJECT_WINDOW_S} s: pausing quotes",
                                      f"after {backoff:.0f} s (doubles if it repeats)"), now_us)

    def enter_safe_mode(self, venue: Venue, why: str) -> RiskDecision:
        self.safe_mode[venue] = why
        return self._log(RiskDecision("safe_mode", RiskAction.SAFE_MODE, venue, None, why,
                                      "manual (bot resume) after investigation"))

    def resume(self, venue: Venue | None = None, *, all_: bool = False) -> None:
        if all_:
            self.all_stopped = None
        if venue is None:
            self.safe_mode.clear()
            self.venue_crit_stop.clear()
        else:
            self.safe_mode.pop(venue, None)
            self.venue_crit_stop.pop(venue, None)

    # ================================================================ quoting gate
    def quoting_allowed(self, venue: Venue, base: str, now_us: int) -> tuple[bool, str]:
        if self.all_stopped:
            return False, f"stopped: {self.all_stopped}"
        if venue in self.safe_mode:
            return False, f"safe mode: {self.safe_mode[venue]}"
        if venue in self.venue_crit_stop:
            return False, f"critical stop: {self.venue_crit_stop[venue]}"
        if venue in self.venue_stopped_day:
            return False, "daily loss stop"
        key = (venue, base)
        if self.paused_until_us.get(key, 0) > now_us:
            return False, "safety pause"
        rp = self.reject_pause_until_us.get(key)
        if rp and rp[0] > now_us:
            return False, f"repeated rejects ({rp[1]})"
        if key in self.operator_paused:
            return False, self.operator_paused[key]
        if key in self.market_stopped:
            return False, self.market_stopped[key]
        return True, "ok"
