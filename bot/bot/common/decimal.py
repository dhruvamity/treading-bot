"""Exact money maths: Decimal at API boundaries, integer ticks/quantums inside the engine.

Rules (prompt pack B3.2, A6.3):
- never float for order prices or sizes;
- bids round DOWN, asks round UP to the tick of the price band they fall in;
- conversions to engine integers must be exact or raise.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation, getcontext

getcontext().prec = 40

ZERO = Decimal(0)
ONE = Decimal(1)


class InexactConversion(ValueError):
    """Raised when a price or size is not an exact multiple of its unit."""


def D(x: object) -> Decimal:
    """Build a Decimal from str/int/Decimal. Floats go through repr so 0.1 stays 0.1."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    try:
        return Decimal(str(x))
    except InvalidOperation as e:
        raise ValueError(f"not a decimal: {x!r}") from e


def to_units_exact(value: Decimal | str, unit: Decimal | str) -> int:
    """value / unit as an exact integer, else InexactConversion. Used for signed ticks/quantums."""
    v, u = D(value), D(unit)
    if u <= 0:
        raise ValueError(f"unit must be positive, got {u}")
    n = v / u
    if n != n.to_integral_value():
        raise InexactConversion(f"{v} is not a multiple of {u}")
    return int(n)


def from_units(n: int, unit: Decimal | str) -> Decimal:
    return (D(n) * D(unit)).normalize()


def round_to_step(value: Decimal, step: Decimal, *, up: bool) -> Decimal:
    q = (value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return q * step


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return round_to_step(value, step, up=False)


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return round_to_step(value, step, up=True)


def tick_for_price(price: Decimal, tiers: Sequence[tuple[Decimal, Decimal | None]], default_tick: Decimal) -> Decimal:
    """Tick of the band `price` falls in. `tiers` = [(tick, up_to_price or None)] in ascending order.

    Arcus: a price in a coarser band must be a multiple of that band's tick; the signing divisor is
    always the top-level tickSize (docs: authentication, "tickTiers ... never changes the signed integer").
    Band edges: a band covers prices <= up_to_price (strictly below the next band).
    """
    for tick, up_to in tiers:
        if up_to is None or price <= up_to:
            return tick
    return default_tick


def round_price(
    price: Decimal,
    *,
    is_bid: bool,
    tiers: Sequence[tuple[Decimal, Decimal | None]] = (),
    default_tick: Decimal,
) -> Decimal:
    """Bids round down, asks round up, to the tick of the band. Re-checks the band after rounding
    because rounding up can cross into a coarser band."""
    tick = tick_for_price(price, tiers, default_tick) if tiers else default_tick
    out = round_to_step(price, tick, up=not is_bid)
    if tiers:
        tick2 = tick_for_price(out, tiers, default_tick)
        if tick2 != tick:
            out = round_to_step(price, tick2, up=not is_bid)
    return out


def bps(x: Decimal | float) -> Decimal:
    """Fraction -> basis points."""
    return D(x) * Decimal(10_000)


def from_bps(b: Decimal | float | int) -> Decimal:
    return D(b) / Decimal(10_000)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def dclamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return lo if x < lo else hi if x > hi else x
