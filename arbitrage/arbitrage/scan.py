"""One scan: every market both venues list, ranked by what holding it would pay at the collateral on hand.

Per scan: two list calls to Lighter and one to Arcus, then the funding history and price history of the CANDIDATES
only (the widest differences right now), kept on disk so that a later scan asks only for the hours it lacks.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from arbitrage import feeds
from arbitrage.config import STATE, Config
from arbitrage.rank import Leg, Plan, plan
from arbitrage.venues import Arcus, Lighter, sigma_day

HIST_H = 168
SIGMA_TTL_S = 6 * 3600


@dataclass
class Scan:
    ts: float
    collateral: dict[str, float]
    common: int
    plans: list[Plan] = field(default_factory=list)
    legs: dict[str, tuple[Leg, Leg]] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)


class History:
    """Hourly funding rates per venue and market, on disk: {hour start: rate}."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, venue: str, symbol: str) -> Path:
        return self.root / "history" / f"{venue}_{symbol}.json"

    def load(self, venue: str, symbol: str) -> dict[int, float]:
        try:
            return {int(k): float(v) for k, v in json.loads(self.path(venue, symbol).read_text()).items()}
        except (OSError, ValueError):
            return {}

    def save(self, venue: str, symbol: str, rows: dict[int, float]) -> None:
        keep = dict(sorted(rows.items())[-24 * 60:])
        p = self.path(venue, symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(keep))

    async def fresh(self, venue: str, symbol: str, client: Arcus | Lighter, now: float) -> dict[int, float]:
        rows = self.load(venue, symbol)
        last_full = int(now) // 3600 * 3600
        newest = max(rows) if rows else 0
        if newest < last_full:
            missing = HIST_H if not rows else min(HIST_H, (last_full - newest) // 3600 + 2)
            rows.update(await client.history(symbol, missing))
            self.save(venue, symbol, rows)
        return rows


def aligned(a: dict[int, float], b: dict[int, float], hours: int = HIST_H) -> tuple[list[float], list[float]]:
    ks = sorted(set(a) & set(b))[-hours:]
    return [a[k] for k in ks], [b[k] for k in ks]


async def sigma_for(symbol: str, arcus: Arcus, root: Path, now: float) -> float:
    p = root / "sigma.json"
    try:
        cache = json.loads(p.read_text())
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(symbol)
    if hit and now - hit[0] < SIGMA_TTL_S:
        return float(hit[1])
    s = sigma_day(await arcus.closes_1h(symbol))
    cache[symbol] = [now, s]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache))
    return s


SIDE_AGE_S = 1800.0      # ProFunding's side is asked for again at most this often (its free key: 100 requests a day)


async def run(cfg: Config, *, symbols: list[str] | None = None, top: int = 10,
              collateral: dict[str, float] | None = None, root: Path = STATE) -> Scan:
    arcus, lighter = Arcus(), Lighter()
    hist = History(root)
    now = time.time()
    try:
        la, ll = await asyncio.gather(arcus.legs(), lighter.legs())
        if collateral is None:
            collateral = {"arcus": 0.0, "lighter": 0.0}
            if cfg.arcus_address:
                collateral["arcus"] = (await arcus.equity(cfg.arcus_address, cfg.arcus_account))[1]
            if cfg.lighter_account is not None:
                collateral["lighter"] = (await lighter.equity(cfg.lighter_account))[1]
        common = sorted(set(la) & set(ll))
        want = [s.upper() for s in symbols] if symbols else sorted(
            common, key=lambda s: -abs(la[s].next_rate_h - ll[s].next_rate_h))[:top]
        out = Scan(ts=now, collateral=collateral, common=len(common))
        # `profunding_side`: which venue is short is ProFunding's answer for the pair (its LighterRH / Arcus row)
        sides: dict[str, str] | None = None
        no_side = ""
        if cfg.settings.profunding_side:
            try:
                rows = await feeds.profunding(cfg.profunding_key, root / "profunding.json", max_age_s=SIDE_AGE_S)
                sides = {k: str(v["short"]) for k, v in rows.items()}
                if not rows and not cfg.profunding_key:
                    no_side = "PROFUNDING_API_KEY is not set, and profunding_side asks ProFunding for the side"
            except Exception as e:   # noqa: BLE001  whatever went wrong there, the side is not known
                no_side = f"ProFunding could not be read ({str(e)[:80]}), and profunding_side asks it for the side"
        for sym in want:
            if sym not in la or sym not in ll:
                continue
            side = sides.get(sym) if sides is not None else None
            ha, hl = await hist.fresh("arcus", sym, arcus, now), await hist.fresh("lighter", sym, lighter, now)
            xa, xl = aligned(ha, hl)
            sig = await sigma_for(sym, arcus, root, now)
            p = plan(la[sym], ll[sym], xa, xl, sig, collateral, cfg.settings, short_venue=side)
            if p.go or symbols:      # worth a closer look: price it with both books' real spreads
                try:
                    (ab, aa), (lb, lk) = await arcus.top(sym), await lighter.top(sym)
                    spreads = {"arcus": (aa - ab) / ab * 1e4, "lighter": (lk - lb) / lb * 1e4}
                    p = plan(la[sym], ll[sym], xa, xl, sig, collateral, cfg.settings, spreads, short_venue=side)
                    p.spreads_bp = spreads
                except (RuntimeError, KeyError, IndexError, ZeroDivisionError):
                    p.reasons.append("could not read both order books")
            if cfg.settings.profunding_side and side is None:
                p.reasons.append(no_side or f"ProFunding lists no Arcus / LighterRH pair for {sym} now, and "
                                            "profunding_side asks it for the side")
            out.plans.append(p)
            out.legs[sym] = (la[sym], ll[sym])
        out.plans.sort(key=lambda p: (not p.go, -p.income_day, -p.edge_h))
        out.calls = {"arcus": arcus.http.calls, "lighter": lighter.http.calls}
        return out
    finally:
        await arcus.http.close()
        await lighter.http.close()
