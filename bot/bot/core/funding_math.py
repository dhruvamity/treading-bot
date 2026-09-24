"""Funding formulas for both venues (A3.9, A4.7), reproducing the docs' worked examples (D1 tests).

Arcus (hourly, median of ~minute premium samples, paid on the ORACLE price):
    premium = (max(impactBid - oracle, 0) - max(oracle - impactAsk, 0)) / oracle
    crypto: rate_h = clamp(premium/8 + clamp(0.0000125 - premium/8, +/-0.0000625), +/-4%)
    RWA   : rate_h = clamp(sofr_rate_hourly + premium/8, +/-4%), sofr_rate_hourly = (SOFR + 0.5%)/360/24
    RWA off-hours: locked at the base rate.
Lighter (hourly, MEAN of minute samples x multiplier M: crypto 1, RWA 0.5, pre-IPO 0.01; paid on the INDEX):
    premium = mean(premium_t) x M; smallClamp = 0.05% x M
    scp = premium + clamp(IR - premium, -smallClamp, +smallClamp); rate = clamp(scp, -4%, 4%) / 8
    IR = base_interest_rate (percent per 8 h in the API; fraction here)
Payment (both): payment = -position x pay_price x rate (positive rate: longs pay).
"""

from __future__ import annotations

from statistics import mean, median


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def premium(oracle_or_index: float, impact_bid: float, impact_ask: float) -> float:
    o = oracle_or_index
    return (max(impact_bid - o, 0.0) - max(o - impact_ask, 0.0)) / o


# ---------------------------------------------------------------------------------------------- Arcus
ARCUS_CRYPTO_BASE_H = 0.0000125
ARCUS_CRYPTO_DEADBAND_H = 0.0000625
CAP = 0.04


def arcus_crypto_rate(prem: float) -> float:
    return clamp(prem / 8 + clamp(ARCUS_CRYPTO_BASE_H - prem / 8, -ARCUS_CRYPTO_DEADBAND_H, ARCUS_CRYPTO_DEADBAND_H),
                 -CAP, CAP)


def sofr_rate_hourly(sofr_annual: float) -> float:
    return (sofr_annual + 0.005) / 360 / 24


def arcus_rwa_rate(prem: float, sofr_hourly: float, *, locked: bool = False) -> float:
    return clamp(sofr_hourly if locked else sofr_hourly + prem / 8, -CAP, CAP)


def arcus_hour_rate(samples: list[float], *, crypto: bool, sofr_hourly: float = 0.0, locked: bool = False) -> float:
    p = median(samples) if samples else 0.0
    return arcus_crypto_rate(p) if crypto else arcus_rwa_rate(p, sofr_hourly, locked=locked)


# ---------------------------------------------------------------------------------------------- Lighter
def lighter_rate(prem_raw_mean: float, interest_rate_8h: float, multiplier: float = 1.0) -> float:
    p = prem_raw_mean * multiplier
    small = 0.0005 * multiplier
    scp = p + clamp(interest_rate_8h - p, -small, small)
    return clamp(scp, -CAP, CAP) / 8


def lighter_hour_rate(samples: list[float], interest_rate_8h: float, multiplier: float = 1.0) -> float:
    return lighter_rate(mean(samples) if samples else 0.0, interest_rate_8h, multiplier)


def lighter_deadband(interest_rate_8h: float, multiplier: float) -> tuple[float, float]:
    """Raw-premium interval over which the Lighter rate equals the base rate."""
    small = 0.0005 * multiplier
    return (interest_rate_8h - small) / multiplier, (interest_rate_8h + small) / multiplier


def payment(position: float, pay_price: float, rate_h: float) -> float:
    """+ = received."""
    return -position * pay_price * rate_h


def carry_al(r_a: float, r_l: float, size: float, p_a_oracle: float, p_l_index: float) -> float:
    """Hourly carry of LONG Arcus / SHORT Lighter with equal base size S (A6.6)."""
    return -r_a * size * p_a_oracle + r_l * size * p_l_index


def breakeven_hours(spread_h: float, round_trip_cost_frac: float) -> float:
    return float("inf") if spread_h <= 0 else round_trip_cost_frac / spread_h
