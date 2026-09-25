"""Shared quoting building blocks (spec "Shared building blocks", A6.3, A6.4).

    u     = clamp((I - I*) x m / I_cap, -1, 1)
    r     = m x (1 - kappa x u x h)                      reservation price
    q_bid = q x max(0, 1 - u);  q_ask = q x max(0, 1 + u)
Execution anchors: Aggressive = improve best by 1 tick if spread > 1 tick else join; Normal = r +/- max(h, spread/2);
Passive = r +/- (h + k x sigma_1m). Tread offset in bps is added (negative = inward). Post-only guard: bid < best
ask, ask > best bid. Rounding: bids DOWN, asks UP to the tick of the price band.
Auto grid spacing: delta = clamp(k x sigma_1h / sqrt(F), delta_min, delta_max), delta_min >= max(2 f_m + 1 bp, 2 ticks).
"""

from __future__ import annotations

import math
from decimal import Decimal

from bot.common.decimal import clamp
from bot.core.order_manager import DesiredOrder
from bot.venues.base import Market, Side

BP = 1e-4


def skew_u(inventory_base: float, target_base: float, mid: float, cap_usd: float) -> float:
    if cap_usd <= 0:
        return 0.0
    return clamp((inventory_base - target_base) * mid / cap_usd, -1.0, 1.0)


def reservation(mid: float, u: float, h: float, kappa: float) -> float:
    return mid * (1 - kappa * u * h)


def skewed_sizes(q: float, u: float, min_q: float = 0.0) -> tuple[float, float]:
    """q_bid = q x max(0, 1 - u), q_ask = q x max(0, 1 + u); a side is dropped only at |u| = 1 (the spec: at u = 1
    stop adding and quote only the exit side). Below that, sizes never fall under the venue minimum `min_q`, which
    at $50-100 capital would otherwise silently remove a side after a single fill."""
    qb = 0.0 if u >= 1 else max(min_q, q * (1 - u))
    qa = 0.0 if u <= -1 else max(min_q, q * (1 + u))
    return qb, qa


def anchors(style: str, *, r: float, mid: float, h: float, best_bid: float, best_ask: float, tick: float,
            sigma_1m: float, k_passive: float, offset_bps: float) -> tuple[float, float]:
    spread = best_ask - best_bid
    if style == "aggressive":
        if spread > tick * 1.5:
            bid, ask = best_bid + tick, best_ask - tick
        else:
            bid, ask = best_bid, best_ask
    elif style == "passive":
        half = (h + k_passive * sigma_1m) * mid
        bid, ask = r - half, r + half
    else:  # normal
        half = max(h * mid, spread / 2)
        bid, ask = r - half, r + half
    off = offset_bps * BP * mid
    return bid - off, ask + off


def post_only_guard(bid: float, ask: float, best_bid: float, best_ask: float, tick: float) -> tuple[float, float]:
    if bid >= best_ask:
        bid = best_ask - tick
    if ask <= best_bid:
        ask = best_bid + tick
    return bid, ask


def bias_target_usd(bias: str, progress: float, peak_usd: float) -> float:
    """Tread bias path: rises to +/-B over the first half of the session, back to 0 by the end."""
    if bias == "neutral" or peak_usd == 0:
        return 0.0
    shape = 2 * progress if progress <= 0.5 else 2 * (1 - progress)
    sign = 1.0 if bias == "long_skew" else -1.0
    return sign * peak_usd * max(0.0, min(1.0, shape))


def participation_mult(our_fill_usd_5m: float, market_usd_5m: float, cap_pct: float) -> float:
    """Widen h by 50% while our share of market volume over 5 min exceeds the cap."""
    if market_usd_5m <= 0:
        return 1.0
    return 1.5 if our_fill_usd_5m / market_usd_5m * 100 > cap_pct else 1.0


def vol_spacing(sigma_1h: float, fills_per_hour: float, *, k_delta: float, delta_min: float, delta_max: float,
                maker_fee: float, tick_frac: float) -> float:
    """All in fractions (1 bp = 1e-4)."""
    lo = max(delta_min, 2 * maker_fee + 1 * BP, 2 * tick_frac)
    if fills_per_hour <= 0 or sigma_1h <= 0:
        return max(lo, delta_max) if sigma_1h <= 0 else lo
    raw = k_delta * sigma_1h / math.sqrt(fills_per_hour)
    return clamp(raw, lo, max(lo, delta_max))


def order_size_usd(explicit: float | str, *, venue_min_usd: float, inventory_cap_usd: float, levels: int) -> float:
    """q = max(1.2 x venue minimum, capital x target leverage / (2N)), with the target leverage implied by the
    inventory cap (I_cap / capital), i.e. q = max(1.2 x venue minimum, I_cap / (2N)). Using leverage_max here would
    size single orders above the inventory cap (spec capital plan: $35, I_cap $30, ~0.9x effective)."""
    if explicit != "auto":
        return max(float(explicit), venue_min_usd * 1.2)
    return max(1.2 * venue_min_usd, inventory_cap_usd / max(1, 2 * levels))


def to_desired(m: Market, side: Side, price: float | Decimal, size_base: float | Decimal, tag: str, *,
               post_only: bool = True, reduce_only: bool = False) -> DesiredOrder | None:
    """Round (bids down, asks up, band tick) and convert to engine integers. None if size rounds to 0."""
    p = m.round_price(Decimal(str(price)), is_bid=side is Side.BUY)
    if p <= 0:
        return None
    q = int(Decimal(str(size_base)) / m.step_size)
    if q <= 0:
        return None
    return DesiredOrder(side, int(p / m.tick_size), q, tag, post_only, reduce_only)


def base_for_usd(usd: float, price: float, m: Market) -> float:
    """Base size for a USD notional, rounded UP to the step so the venue minimum is met."""
    if price <= 0:
        return 0.0
    step = float(m.step_size)
    return math.ceil(usd / price / step) * step

