"""Backtest policies for the Tread.fi modes the scout menu does not have: Mid 0, Signal (RSI skew) and Dynamic Grid.

They plug into the scout simulator (bot/scout/sim.py) under their own `mode` names, so the simulator's fill model,
latency, order budget and risk rules apply to them unchanged. Importing this module registers them.

- `tmid`  Mid 0: the tightest post-only bid and ask strictly around the mid (a bid below it, an ask above it). On a
          one-tick book that is the touch; on a wider book it improves both sides towards the mid. Tread's negative
          Mid spreads (Mid -1, -3) put both quotes past the mid, where two post-only orders would cross each other,
          so on one venue with maker orders only they reduce to this.
- `rsiskew`  Tread's Signal mode: quotes at mid +/- d, skewed by RSI(14) on 1-minute prices. RSI high: the ask moves
          towards the mid and the bid away (s = (RSI - 50) / 50; bid = mid x (1 - d(1 + s)), ask = mid x (1 + d(1 - s))).
- `dgrid`  Dynamic Grid (an approximation: Tread's model is not published). Each minute it measures the efficiency
          ratio of the last 30 one-minute prices and the 1-minute volatility. Choppy (ER < 0.35): a Grid around the
          last fill (the `anchor` policy); trending: a trailing grid (the `rgrid` policy). The spacing is half the
          1-minute volatility, at least `spacing_bps` and at most 10 bps. It changes mode or spacing only when flat,
          or after the other regime has held for 10 minutes.
"""

from __future__ import annotations

import dataclasses
import math

from bot.common.indicators import rsi
from bot.scout import sim
from bot.scout.sim import (
    BP,
    BUY,
    SELL,
    AnchorPolicy,
    Book,
    Config,
    MarketInfo,
    MidPolicy,
    Policy,
    RGridPolicy,
    Risk,
)

Quote = tuple[int, float, float, str]


def tightest_pair(mid: float, tick: float) -> tuple[float, float]:
    """The highest tick strictly below the mid and the lowest tick strictly above it."""
    x = mid / tick
    return (math.ceil(x - 1e-9) - 1) * tick, (math.floor(x + 1e-9) + 1) * tick


class TMidPolicy(MidPolicy):
    """Mid 0 (see the module docstring). Sizes skew against inventory like every Mid setting. With `kappa` > 0 the
    side that would add to the position also steps back from the mid by round(2 x kappa x |u|) ticks (u = position /
    cap), at most to the touch."""

    def quotes(self, b: Book) -> tuple[list[Quote], float]:
        tick = self.m.tick
        bid, ask = tightest_pair(b.mid, tick)
        bid, ask = max(bid, b.bid), min(ask, b.ask)   # never behind the touch
        u = self.u(b)
        back = round(2 * self.c.kappa * abs(u))
        if back and u > 0:
            bid = max(b.bid, bid - back * tick)
        elif back and u < 0:
            ask = min(b.ask, ask + back * tick)
        if bid >= b.ask:
            bid = b.ask - tick
        if ask <= b.bid:
            ask = b.bid + tick
        q = self.q_base(b.mid)
        nb, ns = self.caps(b, q)
        return self.two_sided(b, [(bid, ask, "0")], q, u, nb, ns), 0.0


class RsiSkewPolicy(Policy):
    """Tread's Signal mode (see the module docstring)."""

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self._min = -1
        self._rsi: float | None = None

    def quotes(self, b: Book) -> tuple[list[Quote], float]:
        tick = self.m.tick
        minute = b.t // (60 * sim.S)
        if minute != self._min:
            self._min = minute
            self._rsi = rsi(list(b.closes), 14)
        s = 0.0 if self._rsi is None else max(-1.0, min(1.0, (self._rsi - 50) / 50))
        d = self.c.spacing_bps * BP
        bid, ask = b.mid * (1 - d * (1 + s)), b.mid * (1 + d * (1 - s))
        if bid >= b.ask:
            bid = b.ask - tick
        if ask <= b.bid:
            ask = b.bid + tick
        bid, ask = sim.round_bid(bid, tick), sim.round_ask(ask, tick)
        if ask <= bid:
            bid, ask = tightest_pair(b.mid, tick)
        q = self.q_base(b.mid)
        nb, ns = self.caps(b, q)
        return self.two_sided(b, [(bid, ask, "0")], q, self.u(b), nb, ns), 0.0


class VMidPolicy(MidPolicy):
    """Volatility-gated Mid 0: Mid 0 while the market is calm, wider when it moves. Each minute: d = min(spacing_bps,
    level_step_bps x the 1-minute volatility in bps), and the full spacing_bps when the 30-minute efficiency ratio is
    0.35 or more (a trend). Under 0.5 bp it quotes like Mid 0 (the tightest pair around the mid), else mid +/- d.
    Sizes skew against inventory like every Mid setting."""

    ER_TREND = 0.35

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self._min = -1
        self.d = cfg.spacing_bps

    def quotes(self, b: Book) -> tuple[list[Quote], float]:
        c, tick = self.c, self.m.tick
        minute = b.t // (60 * sim.S)
        if minute != self._min:
            self._min = minute
            er = efficiency_ratio(list(b.closes)[-31:])
            self.d = c.spacing_bps if er >= self.ER_TREND else min(c.spacing_bps, c.level_step_bps * b.sigma_1m / BP)
        u = self.u(b)
        if self.d < 0.5:
            bid, ask = tightest_pair(b.mid, tick)
            bid, ask = max(bid, b.bid), min(ask, b.ask)
        else:
            h = self.d * BP
            bid, ask = sim.round_bid(b.mid * (1 - h), tick), sim.round_ask(b.mid * (1 + h), tick)
        if bid >= b.ask:
            bid = b.ask - tick
        if ask <= b.bid:
            ask = b.bid + tick
        q = self.q_base(b.mid)
        nb, ns = self.caps(b, q)
        return self.two_sided(b, [(bid, ask, "0")], q, u, nb, ns), 0.0


def efficiency_ratio(closes: list[float]) -> float:
    if len(closes) < 3:
        return 0.0
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return abs(closes[-1] - closes[0]) / path if path > 0 else 0.0


class DGridPolicy(Policy):
    """Dynamic Grid approximation (see the module docstring)."""

    ER_TREND = 0.35
    SWITCH_AFTER_MIN = 10
    MAX_BPS = 10.0

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self.sub: Policy | None = None
        self.regime = ""
        self.d = 0.0
        self._min = -1
        self._other_since = -1

    def _build(self, regime: str, d: float) -> Policy:
        if regime == "trend":
            return RGridPolicy(dataclasses.replace(self.c, mode="rgrid", spacing_bps=d, reset_pct=0.25),
                               self.r, self.m)
        return AnchorPolicy(dataclasses.replace(self.c, mode="anchor", spacing_bps=d, reset_pct=0.25),
                            self.r, self.m)

    def quotes(self, b: Book) -> tuple[list[Quote], float]:
        minute = b.t // (60 * sim.S)
        if minute != self._min:
            self._min = minute
            er = efficiency_ratio(list(b.closes)[-31:])
            want = "trend" if er >= self.ER_TREND else "chop"
            d = max(self.c.spacing_bps, min(self.MAX_BPS, 0.5 * b.sigma_1m / BP))
            if want == self.regime:
                self._other_since = -1
            elif self._other_since < 0:
                self._other_since = minute
            flat = abs(b.pos) < self.m.step / 2
            held = self._other_since >= 0 and minute - self._other_since >= self.SWITCH_AFTER_MIN
            respace = self.d > 0 and abs(d - self.d) / self.d > 0.25
            if self.sub is None or (want != self.regime and (flat or held)) or (flat and respace):
                self.sub, self.regime, self.d, self._other_since = self._build(want, d), want, d, -1
        assert self.sub is not None
        self.sub.scale = self.scale
        return self.sub.quotes(b)

    def on_fill(self, side: int, px: float, qty: float, tag: str, t: int, pos_after: float) -> None:
        if self.sub is not None:
            self.sub.on_fill(side, px, qty, tag, t, pos_after)


sim.POLICIES.update({"tmid": TMidPolicy, "rsiskew": RsiSkewPolicy, "dgrid": DGridPolicy, "vmid": VMidPolicy})
sim.CACHEABLE.add("tmid")

__all__ = ["BUY", "SELL", "DGridPolicy", "RsiSkewPolicy", "TMidPolicy", "VMidPolicy", "efficiency_ratio",
           "tightest_pair"]
