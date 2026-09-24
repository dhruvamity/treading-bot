"""Cross-venue funding and basis carry (spec DN module B, A6.6).

For LONG Arcus / SHORT Lighter with equal base size S the hourly carry is
    carry_AL(h) = -r_A(h) x S x P_A_oracle + r_L(h) x S x P_L_index      (reverse pair = -carry_AL)
Entry by EV per direction d and horizon H in {4, 8, 12, 24, 48, 72} h:
    EV_d(H) = H x spread_fcst_d(H) + E[basis convergence]_d - c_entry - E[c_exit] - lambda x Risk_d(H)
Enter only if the best EV clears `entry_ev_bps` (bps of leg notional) and both venues are healthy.
Execution: ALO on Arcus inside the spread (re-price up to n times, never chase with a taker), IOC hedge on Lighter
per Arcus fill (aggregated to >= $10). Exit on the first of: forecast EV below `exit_ev_bps`, basis converged,
avoid-window (earnings / ex-dividend within 24 h), max hold. Margin: < 3x MM -> alert with a transfer suggestion;
< 1.8x MM -> reduce both legs proportionally. Never leave one leg alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from bot.common.config import DNSession
from bot.strategies import quoting as qt
from bot.strategies.base import SessionEvent, StrategyContext, StrategyOutput
from bot.venues.base import TIF, Fill, OrderRequest, Side, Venue


@dataclass(frozen=True, slots=True)
class CarrySignal:
    r_a: float  # predicted Arcus hourly rate (fraction; + = longs pay)
    r_l: float  # predicted Lighter hourly rate
    basis_exec_long_a: float  # (ask_A - bid_L) / mid at planned size
    basis_exec_short_a: float  # (bid_A - ask_L) / mid
    basis_mean: float
    basis_sigma: float
    basis_halflife_h: float
    spread_halflife_h: float  # AR(1) decay of the funding spread
    spread_mean: float


def forecast_spread(current: float, H: float, *, model: str, mean: float, halflife_h: float) -> float:
    """Average forecast spread over the next H hours."""
    if model == "persistence" or halflife_h <= 0 or math.isinf(halflife_h):
        return current
    k = math.log(2) / halflife_h
    # mean of exponential decay toward `mean` over [0, H]
    avg_excess = (current - mean) * (1 - math.exp(-k * H)) / (k * H)
    return mean + avg_excess


def ev_bps(sig: CarrySignal, direction: int, H: float, *, model: str, cost_entry_bps: float, cost_exit_bps: float,
           risk_lambda: float) -> float:
    """direction +1 = LONG Arcus / SHORT Lighter, -1 = reverse. Result in bps of leg notional."""
    spread_now = direction * (sig.r_l - sig.r_a)  # per hour, fraction of notional
    spread_mean = direction * sig.spread_mean
    avg = forecast_spread(spread_now, H, model=model, mean=spread_mean, halflife_h=sig.spread_halflife_h)
    carry = H * avg
    b_exec = sig.basis_exec_long_a if direction > 0 else -sig.basis_exec_short_a
    # Entering LONG A at a basis above its mean loses when it reverts; expected convergence PnL:
    conv = 0.0
    if sig.basis_halflife_h > 0 and not math.isinf(sig.basis_halflife_h):
        decay = 1 - math.exp(-math.log(2) * H / sig.basis_halflife_h)
        conv = -(b_exec - direction * sig.basis_mean) * decay
    risk = risk_lambda * sig.basis_sigma * math.sqrt(max(H, 1e-9) / 24)
    return (carry + conv) / qt.BP - cost_entry_bps - cost_exit_bps - risk / qt.BP


class DNCarry:
    name = "dn_carry"
    min_hold_h = 0.0
    entry_relax_bps = 0.0

    def __init__(self, params: DNSession) -> None:
        self.p = params
        self.direction = 0  # +1 long Arcus / short Lighter, -1 reverse, 0 flat
        self.target_base = 0.0
        self.entered_us = 0
        self.reprices = 0
        self.last_arcus_px: float | None = None
        self.phase = "flat"  # flat | entering | holding | exiting
        self.last_ev: dict[int, float] = {}
        self.exit_reason = ""

    def on_start(self, ctx: StrategyContext) -> None:
        return None

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        return None

    def on_session_event(self, ctx: StrategyContext, ev: SessionEvent) -> None:
        return None

    # ---------------------------------------------------------------- signal
    def signal(self, ctx: StrategyContext) -> CarrySignal | None:
        av, lv = ctx.view, ctx.other_view
        if lv is None:
            return None
        ra = av.predicted_funding_h if av.predicted_funding_h is not None else av.last_funding_h
        rl = lv.predicted_funding_h if lv.predicted_funding_h is not None else lv.last_funding_h
        amid, lmid = av.mid_f(), lv.mid_f()
        if ra is None or rl is None or amid is None or lmid is None:
            return None
        notional = Decimal(str(self.p.collateral_per_leg_usd * self.p.leverage_per_leg))
        a_ask = av.book.impact_price(False, notional) or av.book.best_ask()[0]  # type: ignore[index]
        a_bid = av.book.impact_price(True, notional) or av.book.best_bid()[0]  # type: ignore[index]
        l_ask = lv.book.impact_price(False, notional) or lv.book.best_ask()[0]  # type: ignore[index]
        l_bid = lv.book.impact_price(True, notional) or lv.book.best_bid()[0]  # type: ignore[index]
        mid = (amid + lmid) / 2
        stats = ctx.extra.get("carry_stats", {})
        return CarrySignal(
            r_a=ra, r_l=rl,
            basis_exec_long_a=(float(a_ask) - float(l_bid)) / mid,
            basis_exec_short_a=(float(a_bid) - float(l_ask)) / mid,
            basis_mean=float(stats.get("basis_mean", (amid - lmid) / mid)),
            basis_sigma=float(stats.get("basis_sigma", 5 * qt.BP)),
            basis_halflife_h=float(stats.get("basis_halflife_h", math.inf)),
            spread_halflife_h=float(stats.get("spread_halflife_h", 6.0)),
            spread_mean=float(stats.get("spread_mean", 0.0)))

    def costs_bps(self, ctx: StrategyContext) -> tuple[float, float]:
        """Entry: Arcus maker (0 fee, ~1 tick adverse) + Lighter IOC (0 fee, half spread + slippage)."""
        lv = ctx.other_view
        l_half = (lv.book.spread_bps() or 2.0) / 2 if lv is not None else 2.0
        a_tick = float(ctx.market.tick_size) / (ctx.view.mid_f() or 1.0) / qt.BP
        one_way = a_tick + l_half + 0.5
        return one_way, one_way

    def best_ev(self, sig: CarrySignal, ctx: StrategyContext) -> tuple[int, float, float]:
        ce, cx = self.costs_bps(ctx)
        best = (0, -math.inf, 0.0)
        for d in (1, -1):
            for H in self.p.horizons_h:
                v = ev_bps(sig, d, H, model=self.p.spread_forecast, cost_entry_bps=ce, cost_exit_bps=cx,
                           risk_lambda=self.p.risk_lambda)
                self.last_ev[d * H] = v
                if v > best[1]:
                    best = (d, v, float(H))
        return best

    # ---------------------------------------------------------------- tick
    def on_tick(self, ctx: StrategyContext) -> StrategyOutput:
        out = StrategyOutput()
        am, lm = ctx.market, ctx.other_market
        amid = ctx.view.mid_f()
        lmid = ctx.other_view.mid_f() if ctx.other_view else None
        if lm is None or amid is None or lmid is None:
            out.reason = "carry: missing book"
            return out
        leg_notional = self.p.collateral_per_leg_usd * self.p.leverage_per_leg
        sig = self.signal(ctx)
        held_h = (ctx.now_us - self.entered_us) / 3.6e9 if self.entered_us else 0.0
        a_inv, l_inv = float(ctx.inventory), float(ctx.other_inventory)
        resid = a_inv + l_inv
        # ---- always hedge residuals first (never leave one leg alone)
        l_min = max(self.p.min_hedge_usd, float(lm.min_notional), float(lm.min_size) * lmid)
        if abs(resid) * lmid >= l_min:
            h = self._hedge(ctx, resid, lmid, "carry: hedge Arcus fill residual")
            if h is not None:
                out.hedge_intents.append(h)
        margin_x = float(ctx.extra.get("margin_x_mm", math.inf))
        if margin_x < self.p.margin_crit_x_mm and self.phase in ("holding", "entering"):
            self.target_base *= 0.5
            out.reason = f"carry: margin {margin_x:.2f}x MM < {self.p.margin_crit_x_mm}: cut both legs by half"
        elif margin_x < self.p.margin_warn_x_mm:
            out.metrics["margin_warn"] = margin_x
        # ---- state machine
        if self.phase == "flat":
            if sig is None or not ctx.quoting_allowed or not ctx.other_venue_healthy or ctx.extra.get("avoid_window"):
                out.reason = out.reason or "carry flat: " + ("no signal" if sig is None else "blocked")
                out.set(Venue.ARCUS, am.base, [])
                return out
            dirn, ev, H = self.best_ev(sig, ctx)
            if ev > self.p.entry_ev_bps - self.entry_relax_bps:
                self.direction, self.phase, self.reprices = dirn, "entering", 0
                self.target_base = leg_notional / amid
                self.entered_us = ctx.now_us
                out.reason = (f"carry ENTER {'long A/short L' if dirn > 0 else 'short A/long L'}: EV {ev:.1f} bps "
                              f"over {H:.0f}h (rA={sig.r_a:.2e}, rL={sig.r_l:.2e})")
            else:
                out.reason = f"carry flat: best EV {ev:.1f} bps < {self.p.entry_ev_bps} bps"
                out.set(Venue.ARCUS, am.base, [])
                return out
        if self.phase in ("entering", "holding") and sig is not None:
            _ce, cx = self.costs_bps(ctx)
            fwd = max(ev_bps(sig, self.direction, H, model=self.p.spread_forecast, cost_entry_bps=0.0,
                             cost_exit_bps=cx, risk_lambda=self.p.risk_lambda) for H in self.p.horizons_h)
            reasons = []
            if held_h >= self.min_hold_h and fwd < self.p.exit_ev_bps:
                reasons.append(f"forecast EV {fwd:.1f} bps < exit {self.p.exit_ev_bps}")
            if held_h >= self.p.max_hold_h:
                reasons.append(f"max hold {self.p.max_hold_h:.0f}h")
            if ctx.extra.get("avoid_window"):
                reasons.append("earnings/ex-dividend within 24h")
            if reasons:
                self.phase = "exiting"
                self.exit_reason = "; ".join(reasons)
        target = self.direction * self.target_base if self.phase in ("entering", "holding") else 0.0
        if self.phase == "exiting" and abs(a_inv) * amid < float(am.min_notional) and abs(l_inv) * lmid < l_min:
            self.phase, self.direction, self.entered_us = "flat", 0, 0
            out.reason = f"carry exited ({self.exit_reason})"
            out.set(Venue.ARCUS, am.base, [])
            return out
        gap = target - a_inv
        orders = []
        b, a = ctx.view.book.best_bid(), ctx.view.book.best_ask()
        tick = float(am.tick_size)
        venue_min = max(float(am.min_notional), float(am.min_size) * amid)
        if b is not None and a is not None and abs(gap) * amid >= venue_min:
            side = Side.BUY if gap > 0 else Side.SELL
            inside = self.p.arcus_inside_spread_ticks * tick
            px = float(b[0]) + inside if side is Side.BUY else float(a[0]) - inside
            px = min(px, float(a[0]) - tick) if side is Side.BUY else max(px, float(b[0]) + tick)
            if self.last_arcus_px is not None and px != self.last_arcus_px:
                self.reprices += 1
            self.last_arcus_px = px
            clip = min(abs(gap), max(venue_min * 1.5, 12.0) / amid)
            if self.reprices <= self.p.arcus_reprice_max or self.phase == "exiting":
                d = qt.to_desired(am, side, px, qt.base_for_usd(clip * amid, amid, am), "carry",
                                  reduce_only=self.phase == "exiting")
                if d:
                    orders.append(d)
        elif self.phase == "entering" and abs(gap) * amid < venue_min:
            self.phase = "holding"
        out.set(Venue.ARCUS, am.base, orders)
        out.reason = out.reason or (f"carry {self.phase} dir={self.direction:+d} arcus={a_inv:+.6f} "
                                    f"lighter={l_inv:+.6f} held={held_h:.1f}h")
        out.metrics.update({"phase_" + self.phase: 1.0, "held_h": held_h, "reprices": float(self.reprices)})
        out.half_spread_ticks = 1.0
        return out

    def _hedge(self, ctx: StrategyContext, resid: float, lmid: float, why: str) -> OrderRequest | None:
        lm = ctx.other_market
        assert lm is not None
        side = Side.SELL if resid > 0 else Side.BUY
        slip = self.p.hedge_slippage_cap_bps * qt.BP
        px = lmid * (1 - slip) if side is Side.SELL else lmid * (1 + slip)
        step = float(lm.step_size)
        size = int(abs(resid) / step) * step
        if size < float(lm.min_size) or size <= 0:
            return None
        return OrderRequest(Venue.LIGHTER_RH, lm.base, side, lm.round_price(Decimal(str(px)), is_bid=side is Side.BUY),
                            Decimal(str(size)).quantize(lm.step_size), TIF.IOC, tag="hedge", reason=why)

    def on_stop(self, ctx: StrategyContext) -> StrategyOutput:
        self.phase = "exiting"
        self.exit_reason = "session stop"
        return self.on_tick(ctx)
