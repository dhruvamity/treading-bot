"""What the owner is after decides which setups make the top 3. Three lists from the same scan, all about volume
(2026-09-26: the owner wants the most volume at the most efficient cost, not breakeven setups):

- volume:     the most volume per day among the setups that cost at most `volume_cost` dollars per $1,000 traded
              (/set volume_cost), with every check that is not about money still passing (fills, kill, liquidation,
              fresh data, trend, volatility, new listing). The default (/top3), and what the pilot offers after scans.
- cheapest:   the same setups, cheapest per $1,000 first, among those trading at least CHEAP_MIN_TURNOVER times the
              capital a day (a setup that barely trades is cheap because it does nothing).
- max:        the most volume whatever it costs: every check that is not about money still applies. The last 24 h
              is not required (the scout re-checks it only for settings within the budget), and a row says so.
- manual:     not a list: a setup the owner picked by hand (market, setup, leverage). The pilot never pauses it for
              failing a list, only when its market goes offline.

The daily stop still applies to every run: a setting whose backtest hit it has that already in its numbers, since
the scout backtests with the owner's own stops. The lists are ranked from the scan's `all` rows at view time, so a
new budget shows at once. The scout also re-checks the last 24 h of the settings within the budget (scan.passes_long)
so the lists judge "is it still working now".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RECENT_X = 2.0     # the last 24 h may cost up to this multiple of the budget (one day is noisy)
CHEAP_MIN_TURNOVER = 50.0   # the cheapest list: setups that trade at least 50x the capital a day ($5,500 at $110)
MONEY_PREFIXES = ("loses $", "hit the daily stop", "only ", "last 24 h lost", "last 6 h lost")   # older scans


@dataclass(frozen=True)
class Profile:
    key: str
    icon: str
    title: str
    blurb: str
    budget: bool = True                         # judged by cost per $1,000 (every list is)
    any_cost: bool = False                      # no cost limit at all (max volume)
    by_cost: bool = False                       # cheapest per $1,000 first (else most volume first)
    min_turnover: float = 0.0                   # volume a day at least this many times the capital
    listed: bool = True                         # False: not a list (the owner's own pick): never judged


PROFILES: dict[str, Profile] = {p.key: p for p in (
    Profile("volume", "🚀", "Most Volume", "the most volume for at most your cost per $1,000 traded"),
    Profile("cheapest", "💎", "Cheapest", "the lowest cost per $1,000 traded, within your budget",
            by_cost=True, min_turnover=CHEAP_MIN_TURNOVER),
    Profile("max", "🔥", "Max Volume", "the most volume whatever it costs; safety checks still apply",
            any_cost=True),
    Profile("manual", "🎯", "Your pick", "a setup you picked by hand", listed=False),
)}
ALIASES = {"top": "volume", "top3": "volume", "vol": "volume", "aggressive": "volume", "agg": "volume",
           "mid": "volume", "breakeven": "cheapest", "be": "cheapest", "safe": "cheapest", "cheap": "cheapest",
           "maxvolume": "max", "mine": "manual"}
LISTS = tuple(p for p in PROFILES.values() if p.listed)
DEFAULT = "volume"


def profile_of(name: str | None) -> Profile:
    key = (name or DEFAULT).lower()
    key = ALIASES.get(key, key)
    if key not in PROFILES:
        raise ValueError(f"unknown list {name!r}: volume, cheapest or max")
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
    if not p.listed:
        return []
    if not c.get("days"):
        return list(c.get("reasons") or []) or ["no full day of data yet"]
    money = set(money_reasons(c))
    out = [r for r in c.get("reasons") or [] if r not in money]
    cost = cost_1k(c)
    if cost is None:
        out.append("no volume in the backtest")
    elif p.any_cost:
        return out
    elif cost > budget:
        out.append(f"costs ${cost:.2f} per $1,000 (budget ${budget:.2f})")
    cap = float(c.get("used_usd") or c.get("capital_usd") or 0)
    if p.min_turnover and cap and float(c.get("volume_day") or 0) < p.min_turnover * cap:
        out.append(f"trades under {p.min_turnover:g}x the capital a day (${p.min_turnover * cap:,.0f})")
    if not c.get("recent_checked", True):
        out.append("last 24 h not re-checked yet (next scan)")
    elif float(c.get("recent_volume") or 0) > 0:
        rc = max(0.0, -float(c.get("recent_pnl") or 0)) / float(c["recent_volume"]) * 1000
        if rc > RECENT_X * budget:
            out.append(f"last 24 h cost ${rc:.2f} per $1,000")
    return out


def _rank(p: Profile) -> Any:
    if p.by_cost:
        return lambda c: (cost_1k(c) or 0.0, -float(c["volume_day"]))
    return lambda c: (-float(c["volume_day"]), -float(c["pnl_day"]))


def top(scan: dict[str, Any] | None, profile: Profile | str, budget: float, n: int = 3) -> list[dict[str, Any]]:
    """The profile's best setup per market (most maker volume first, or the cheapest per $1,000 first), at most n."""
    p = profile if isinstance(profile, Profile) else profile_of(profile)
    best: dict[str, dict[str, Any]] = {}
    for c in sorted((c for c in (scan or {}).get("all") or [] if not verdict(c, p, budget)), key=_rank(p)):
        best.setdefault(c["market"], c)
    return sorted(best.values(), key=_rank(p))[:n]


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
