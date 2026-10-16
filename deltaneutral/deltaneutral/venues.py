"""The two venues for the bot, paper or live, and what it reads besides orders: each venue's money, each market's
margin rules, whether Arcus's stock markets are closed, and the funding rates.

- paper: the arbitrage's PaperVenue (orders, fills and positions only here) on the venues' real best bid and ask,
  read from their public APIs; the funding the venues really paid is credited every hour.
- live: the arbitrage's own live clients (arbitrage/exec/arcus.py and lighter.py: the same keys as the other bots,
  in arcus/.env and lighter/.env). Lighter's client trades one market, so each market gets its own, sharing one
  nonce counter (Lighter refuses a nonce that does not rise). Each venue's money is read from its public account
  endpoint (ARCUS_ADDRESS, ARCUS_ACCOUNT_INDEX, LIGHTER_ACCOUNT_INDEX).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from arbitrage import venues as AV
from arbitrage.exec.venue import PaperVenue, Spec, TradeVenue

from . import ROOT, STATE
from . import plan as P
from .plan import ARCUS, LIGHTER, VENUES

RULES_S = 300.0                     # market rules and funding are read again this often
ENV_KEYS = ("ARCUS_ADDRESS", "ARCUS_ACCOUNT_INDEX", "LIGHTER_ACCOUNT_INDEX", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "BOT_ROLE")


def _env_file(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def read_env() -> dict[str, str]:
    """The bot's one credentials file (treading-bot/arcus/.env), lighter/.env for Lighter's account, then
    deltaneutral/.env, then the process environment. Only the names this part uses (and DN_*)."""
    ours = lambda k: k in ENV_KEYS or k.startswith("DN_")       # noqa: E731
    out: dict[str, str] = {}
    for p in (ROOT.parent / "lighter" / ".env", ROOT.parent / "arcus" / ".env", ROOT / ".env"):
        out.update({k: v for k, v in _env_file(p).items() if ours(k)})
    out.update({k: v for k, v in os.environ.items() if ours(k) and v})
    return out


def no_trading(env: dict[str, str]) -> str:
    """Why this machine must not run the bot ("" when it may): it trades where BOT_ROLE is all or trader."""
    r = (env.get("BOT_ROLE") or "all").strip().lower()
    return "" if r in ("all", "trader") else (f"this machine is a {r} (BOT_ROLE={r} in arcus/.env): the bot runs on "
                                              "the trader machine")


def rules_from_legs(a: AV.Leg | None, lt: AV.Leg | None) -> dict[str, P.Rules] | None:
    if a is None or lt is None:
        return None
    return {ARCUS: P.Rules(imf=a.imf, imf_off=a.imf_off or a.imf, mmf=a.mmf, taker_bp=2.25,
                           min_notional=a.min_notional, step=a.step),
            LIGHTER: P.Rules(imf=lt.imf, imf_off=lt.imf, mmf=lt.mmf, min_notional=lt.min_notional, step=lt.step)}


class Reader:
    """What the engine reads besides the books: (money per venue, rules per venue per market, off-hours?), and the
    funding rates now (`funding()`: {(venue, market): rate an hour}). `equity` is a coroutine per venue."""

    def __init__(self, equity: dict[str, Any], symbols: Any) -> None:
        self.equity_of = equity
        self.symbols = symbols                 # callable: the markets in play
        self.arcus, self.lighter = AV.Arcus(), AV.Lighter()
        self._rules: dict[str, dict[str, P.Rules]] = {ARCUS: {}, LIGHTER: {}}
        self._rates: dict[tuple[str, str], float] = {}
        self._off = False
        self._at = 0.0

    async def refresh(self) -> None:
        al, ll = await self.arcus.legs(), await self.lighter.legs()
        rules: dict[str, dict[str, P.Rules]] = {ARCUS: {}, LIGHTER: {}}
        rates: dict[tuple[str, str], float] = {}
        off = False
        for s in set(al) & set(ll):
            r = rules_from_legs(al[s], ll[s])
            if r is None:
                continue
            for v in VENUES:
                rules[v][s] = r[v]
            rates[(ARCUS, s)] = al[s].next_rate_h
            rates[(LIGHTER, s)] = ll[s].rate_h
        for s in self.symbols():
            if s in al:
                off = off or al[s].off_hours
        self._rules, self._rates, self._off, self._at = rules, rates, off, time.time()

    async def __call__(self) -> tuple[dict[str, float], dict[str, dict[str, P.Rules]], bool]:
        if time.time() - self._at > RULES_S or any(s not in self._rules[ARCUS] for s in self.symbols()):
            await self.refresh()
        eq = {v: float(await self.equity_of[v]()) for v in VENUES}
        return eq, self._rules, self._off

    def funding(self) -> dict[tuple[str, str], float]:
        return dict(self._rates)

    async def trailing(self, symbols: tuple[str, ...], hours: int = 24) -> dict[tuple[str, str], float]:
        """Each venue's average hourly rate over the last `hours` hours, per market: what the way round is decided on
        (as in the backtest). While the stock market is closed Arcus charges every market the same fixed rate, so
        the rate of the moment would decide on noise."""
        out: dict[tuple[str, str], float] = {}
        if not self.lighter.ids:
            await self.lighter.legs()
        for s in symbols:
            for v, client in ((ARCUS, self.arcus), (LIGHTER, self.lighter)):
                h = await client.history(s, hours)
                if h:
                    out[(v, s)] = sum(h.values()) / len(h)
        return out

    async def close(self) -> None:
        await self.arcus.http.close()
        await self.lighter.http.close()


# ------------------------------------------------------------------------------------------------ paper
class PaperSide(PaperVenue):
    """A paper venue that trades several markets (the arbitrage's PaperVenue keeps one market's rules)."""

    def __init__(self, name: str, client: Any, taker_bp: float, collateral: float) -> None:
        super().__init__(name, client.top, Spec(0.01, 0.0001, 0.0001, 5.0, taker_bp), collateral)
        self.client = client
        self.specs: dict[str, Spec] = {}

    async def start(self, symbol: str) -> Spec:
        legs = await self.client.legs()          # also fills Lighter's market ids
        leg = legs[symbol]
        self.specs[symbol] = Spec(leg.tick, leg.step, leg.min_size, leg.min_notional, self.spec.taker_bp)
        return self.specs[symbol]

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        self.leverage[symbol] = leverage

    async def stop(self) -> None:
        await self.client.http.close()


class PaperFunding:
    """Credits the paper positions what the venues really paid, each published hour once (state/paper-funding.json)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.until = float(json.loads(path.read_text())["until"])
        except (OSError, ValueError, KeyError):
            self.until = time.time() // 3600 * 3600

    async def apply(self, sides: dict[str, PaperSide], symbols: tuple[str, ...], since: float) -> dict[str, float]:
        paid = {v: 0.0 for v in VENUES}
        start = max(self.until, since)
        newest = self.until
        for v, side in sides.items():
            for s in symbols:
                p = side.pos.get(s, 0.0)
                hist = await side.client.history(s, 4)
                t = side.tops.get(s)
                if t is None:
                    continue
                for h, rate in hist.items():
                    if start < h <= time.time():
                        if p:
                            x = -p * t.mid * rate              # + rate: longs pay
                            side.collateral += x
                            paid[v] += x
                        newest = max(newest, float(h))
        self.until = newest
        self.path.write_text(json.dumps({"until": self.until}))
        return paid


def paper(collateral: dict[str, float]) -> tuple[dict[str, PaperSide], dict[str, Any]]:
    sides = {ARCUS: PaperSide(ARCUS, AV.Arcus(), 2.25, collateral.get(ARCUS, 0.0)),
             LIGHTER: PaperSide(LIGHTER, AV.Lighter(), 0.0, collateral.get(LIGHTER, 0.0))}

    def equity_of(v: str) -> Any:
        async def f() -> float:
            return float(await sides[v].free_collateral() or 0.0)
        return f
    return sides, {v: equity_of(v) for v in VENUES}


# ------------------------------------------------------------------------------------------------ live
class LighterPair:
    """Lighter, live, two markets: one arbitrage LighterTrade per market, sharing the first one's nonce and order-id
    counters."""
    name = LIGHTER

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.legs: dict[str, Any] = {}

    async def start(self, symbol: str) -> Spec:
        from arbitrage.exec.lighter import LighterTrade

        t = self.legs.get(symbol) or LighterTrade(self.state_dir)
        spec = await t.start(symbol)
        first = next(iter(self.legs.values()), None)
        if first is not None and first is not t:
            t.nonces, t.ids = first.nonces, first.ids
        self.legs[symbol] = t
        return spec

    async def stop(self) -> None:
        for t in self.legs.values():
            await t.stop()

    @property
    def maker_life_s(self) -> float | None:
        """How long a resting order may stay before the engine replaces it (Lighter drops it at its expiry)."""
        first = next(iter(self.legs.values()), None)
        return first.maker_life_s if first is not None else None

    async def top(self, symbol: str) -> Any:
        return await self.legs[symbol].top(symbol)

    async def position(self, symbol: str) -> float | None:
        return await self.legs[symbol].position(symbol)

    async def free_collateral(self) -> float | None:
        return await next(iter(self.legs.values())).free_collateral()

    async def maker(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> str:
        return await self.legs[symbol].maker(symbol, side, size, price, reduce_only)

    async def taker(self, symbol: str, side: int, size: float, worst: float, reduce_only: bool) -> str:
        return await self.legs[symbol].taker(symbol, side, size, worst, reduce_only)

    async def cancel(self, symbol: str, order_id: str) -> None:
        await self.legs[symbol].cancel(symbol, order_id)

    async def order(self, symbol: str, order_id: str) -> Any:
        return await self.legs[symbol].order(symbol, order_id)

    async def cancel_all(self, symbol: str) -> None:
        await self.legs[symbol].cancel_all(symbol)

    async def set_stops(self, symbol: str, position: float, stop: float, take: float) -> bool:
        return bool(await self.legs[symbol].set_stops(symbol, position, stop, take))

    async def set_leverage(self, symbol: str, leverage: float) -> None:
        await self.legs[symbol].set_leverage(symbol, leverage)


def live(env: dict[str, str]) -> tuple[dict[str, TradeVenue], dict[str, Any]]:
    from arbitrage.exec.arcus import ArcusTrade

    addr = env.get("ARCUS_ADDRESS", "").strip()
    a_idx = int(env.get("ARCUS_ACCOUNT_INDEX") or 0)
    l_idx = env.get("LIGHTER_ACCOUNT_INDEX", "").strip()
    if not addr or not l_idx:
        raise RuntimeError("ARCUS_ADDRESS and LIGHTER_ACCOUNT_INDEX must be in arcus/.env: the bot reads each "
                           "venue's money from them")
    arcus, lighter = AV.Arcus(), AV.Lighter()

    async def a_eq() -> float:
        return (await arcus.equity(addr, a_idx))[0]

    async def l_eq() -> float:
        return (await lighter.equity(int(l_idx)))[0]
    venues: dict[str, TradeVenue] = {ARCUS: ArcusTrade(ROOT.parent / "arcus"),
                                     LIGHTER: LighterPair(STATE)}     # type: ignore[dict-item]
    return venues, {ARCUS: a_eq, LIGHTER: l_eq}


def account_names(env: dict[str, str]) -> dict[str, str]:
    """How the transfer notice names the two accounts."""
    addr = env.get("ARCUS_ADDRESS", "")
    a = f"Arcus ({addr[:6]}…{addr[-4:]}, subaccount {env.get('ARCUS_ACCOUNT_INDEX') or 0})" if addr else "Arcus"
    li = env.get("LIGHTER_ACCOUNT_INDEX", "")
    return {ARCUS: a, LIGHTER: f"Lighter (account {li})" if li else "Lighter"}
