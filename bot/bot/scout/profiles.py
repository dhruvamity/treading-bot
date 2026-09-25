"""What the owner is after decides which setups make the top 3. Three lists from the same scan:

- breakeven:  every check passes, including "loses at most 0.25% of the capital a day" (the scout's GO). Most maker
              volume first. The default, and what the pilot offers after each scan.
- volume:     any setting whose losses cost at most `volume_cost` dollars per $1,000 of volume (/set volume_cost),
              with every check that is not about money still passing (fills, kill, liquidation, fresh data, trend,
              volatility, new listing). Most volume first: paying a known price for volume.
- aggressive: the same budget, but only Mid quoting at or inside the best bid/ask ("improve touch", "touch 1bp"):
              the fastest flipping, the most fills.

The daily stop still applies to every run: a setting whose backtest hit it has that already in its numbers, since
the scout backtests with the owner's own stops. The lists are ranked from the scan's `all` rows at view time, so a
new budget shows at once. The scout also re-checks the last 24 h of the settings within the budget (scan.passes_long)
so the volume lists judge "is it still working now" like the breakeven list does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AGGRESSIVE = ("improve touch", "touch 1bp")
RECENT_X = 2.0     # the last 24 h may cost up to this multiple of the budget (one day is noisy)
MONEY_PREFIXES = ("loses $", "hit the daily stop", "only ", "last 24 h lost", "last 6 h lost")   # older scans


@dataclass(frozen=True)
class Profile:
    key: str
    icon: str
    title: str
    blurb: str
    settings: tuple[str, ...] | None = None   # None: every setting in the scout's menu
    budget: bool = False                        # judged by cost per $1,000 instead of breakeven


PROFILES: dict[str, Profile] = {p.key: p for p in (
    Profile("breakeven", "🟢", "Breakeven", "passes every check, about breakeven or better; most volume first"),
    Profile("volume", "🔥", "Volume", "the most volume for at most your cost per $1,000 traded; any setting",
            budget=True),
    Profile("aggressive", "⚡", "Aggressive Mid", "quotes at or inside the best bid/ask and flips fast; most volume "
            "within your cost per $1,000", settings=AGGRESSIVE, budget=True),
)}
ALIASES = {"be": "breakeven", "safe": "breakeven", "vol": "volume", "agg": "aggressive", "mid": "aggressive"}


def profile_of(name: str | None) -> Profile:
    key = (name or "breakeven").lower()
    key = ALIASES.get(key, key)
    if key not in PROFILES:
        raise ValueError(f"unknown list {name!r}: breakeven, volume or aggressive")
    return PROFILES[key]


def cost_1k(c: dict[str, Any]) -> float | None:
    """Dollars lost per $1,000 of maker volume on the full days (0 when it made money)."""
    if c.get("cost_1k") is not None:
        return float(c["cost_1k"])
    v = float(c.get("volume_day") or 0)
    return max(0.0, -float(c.get("pnl_day") or 0)) / v * 1000 if v > 0 else None


def money_reasons(c: dict[str, Any]) -> list[str]:
    if "money_reasons" in c:
        return list(c["money_reasons"])
    return [r for r in c.get("reasons") or [] if r.startswith(MONEY_PREFIXES)]


def verdict(c: dict[str, Any], profile: Profile | str, budget: float) -> list[str]:
    """Why this scan row is not in the profile's list (empty: it is)."""
    p = profile if isinstance(profile, Profile) else profile_of(profile)
    if p.settings is not None and (c.get("setting") or c["config"].split(" @ ")[0]) not in p.settings:
        return [f"not a {p.title} setting"]
    if not p.budget:
        return list(c.get("reasons") or [])
    if not c.get("days"):
        return list(c.get("reasons") or []) or ["no full day of data yet"]
    money = set(money_reasons(c))
    out = [r for r in c.get("reasons") or [] if r not in money]
    cost = cost_1k(c)
    if cost is None:
        out.append("no volume in the backtest")
    elif cost > budget:
        out.append(f"costs ${cost:.2f} per $1,000 (budget ${budget:.2f})")
    if not c.get("recent_checked", True):
        out.append("last 24 h not re-checked yet (next scan)")
    elif float(c.get("recent_volume") or 0) > 0:
        rc = max(0.0, -float(c.get("recent_pnl") or 0)) / float(c["recent_volume"]) * 1000
        if rc > RECENT_X * budget:
            out.append(f"last 24 h cost ${rc:.2f} per $1,000")
    return out


def top(scan: dict[str, Any] | None, profile: Profile | str, budget: float, n: int = 3) -> list[dict[str, Any]]:
    """The profile's best setup per market, most maker volume first (then PnL), at most n."""
    p = profile if isinstance(profile, Profile) else profile_of(profile)
    best: dict[str, dict[str, Any]] = {}
    for c in sorted((c for c in (scan or {}).get("all") or [] if not verdict(c, p, budget)),
                    key=lambda c: (-float(c["volume_day"]), -float(c["pnl_day"]))):
        best.setdefault(c["market"], c)
    return sorted(best.values(), key=lambda c: (-float(c["volume_day"]), -float(c["pnl_day"])))[:n]


def nearest(scan: dict[str, Any] | None, profile: Profile | str, budget: float, n: int = 3) -> list[dict[str, Any]]:
    """When a budget list is empty: the cheapest setups that pass everything but the budget (one per market)."""
    p = profile if isinstance(profile, Profile) else profile_of(profile)
    rows = []
    for c in (scan or {}).get("all") or []:
        why = verdict(c, p, 1e9)
        if not why and cost_1k(c) is not None:
            rows.append(c)
    best: dict[str, dict[str, Any]] = {}
    for c in sorted(rows, key=lambda c: (cost_1k(c) or 0.0, -float(c["volume_day"]))):
        best.setdefault(c["market"], c)
    return sorted(best.values(), key=lambda c: (cost_1k(c) or 0.0, -float(c["volume_day"])))[:n]


def at_max(scan: dict[str, Any] | None, c: dict[str, Any]) -> dict[str, Any] | None:
    """The same market and setting at the market's maximum leverage (c itself when it is already there)."""
    if c.get("at_max"):
        return c
    setting = c.get("setting") or c["config"].split(" @ ")[0]
    return next((x for x in (scan or {}).get("all") or [] if x["market"] == c["market"] and x.get("at_max")
                 and (x.get("setting") or x["config"].split(" @ ")[0]) == setting), None)
