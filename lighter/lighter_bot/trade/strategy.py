"""The setups, and the one piece of code that turns a setup and the market into orders.

The backtest (lighter_bot/scout/sim.py) and the running bot (lighter_bot/trade/engine.py) both call Quoter.plan() with the same
inputs, so they quote the same prices and sizes by construction.

The ideas come from the Arcus bot (Tread.fi's form: a reference price, a spread in bps, a directional bias, run limits);
the numbers were chosen again for Lighter:
- Mid: both sides `spread` bps from the mid, following it; a negative spread sits inside the mid.
- Touch (Lighter only): both sides `spread` bps behind the best bid and ask (0 joins them; negative steps inside).
  Lighter's books are 20-90 ticks wide on the crypto perps, so "at the mid" and "at the best price" are far apart;
  on Arcus's one-tick books they were the same thing.
- Grid: around the last fill: a sell never below the last buy + spread, a buy never above the last sell - spread;
  flat, it quotes around the mid; a soft reset closes the position at the touch once the mid has run
  `reset_pct` against it.
- Smart: Mid that leaves out the side that would add to the position while the book leans hard against it or the
  price has just moved against it.
- Bias: Long or Short holds `bias_frac` of the position cap on that side (sizes skew toward it); Neutral holds none.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field, replace

BP = 1e-4
US = 1_000_000
BUY, SELL = 1, -1

MODES = ("mid", "touch", "grid", "smart")
BIASES = ("neutral", "long", "short")
SPREAD_RANGE = (-5.0, 50.0)


@dataclass(frozen=True)
class Params:
    """Everything a setup does. The menu's setups fill these from the mode's defaults (DEFAULTS)."""

    mode: str = "mid"
    spread: float = 0.0            # bps: from the mid (mid, smart), behind the touch (touch), from the last fill (grid)
    bias: int = 0                  # +1 long, -1 short
    bias_frac: float = 0.5         # the share of the position cap a bias holds
    kappa: float = 0.0             # inventory skew of the reservation price (Mid, Smart), in half-spreads
    reset_pct: float = 0.5         # Grid's soft reset
    imbalance: float = 0.6         # Smart: leave a side out when the book leans this hard against it ...
    move_bps: float = 0.5          # ... or the mid moved this far against it ...
    lookback_s: float = 5.0        # ... over this many seconds
    hold_s: float = 0.0            # > 0: a position older than this is closed with a taker order (0 taker fee)
    tol_bps: float = 0.25          # requote only when the wanted price is this far (or 2 ticks) from the live one

    def with_(self, **kw: object) -> Params:
        return replace(self, **kw)   # type: ignore[arg-type]


# Hidden defaults per mode, from the Lighter backtests
DEFAULTS: dict[str, dict[str, float]] = {
    "mid": {"kappa": 1.0},
    "touch": {"kappa": 0.0},
    "grid": {"reset_pct": 0.5},
    "smart": {"kappa": 1.0},
}


@dataclass(frozen=True)
class Setup:
    mode: str = "mid"
    spread: float = 0.0
    bias: str = "neutral"

    @property
    def name(self) -> str:
        """'Mid 0', 'Touch +1 Long', 'Grid +3 Short'."""
        b = "" if self.bias == "neutral" else f" {self.bias.capitalize()}"
        return f"{self.mode.capitalize()} {spread_text(self.spread)}{b}"

    @property
    def sid(self) -> str:
        """A short id for buttons: m0n, t+1l, g+3s, s0n."""
        return f"{self.mode[0]}{spread_text(self.spread)}{self.bias[0]}"

    @property
    def sign(self) -> int:
        return {"long": 1, "short": -1}.get(self.bias, 0)

    def params(self, **override: float) -> Params:
        base = DEFAULTS.get(self.mode, {})
        p = Params(mode=self.mode, spread=self.spread, bias=self.sign)
        kw = {**base, **override}
        if self.mode in ("mid", "smart") and self.spread <= 0.5 and "kappa" not in override:
            # up to 0.5 bp from the mid the quotes sit at or inside the touch: skewing them only gives up fills
            # (Lighter, Sep 24: Mid +0.5 without skew cost 0.33 vs 0.38 bp on BTC, 0.04 vs 0.07 bp on SPY)
            kw["kappa"] = 0.0
        return replace(p, **kw)        # type: ignore[arg-type]


def spread_text(x: float) -> str:
    x = float(x) + 0.0
    s = f"{x:g}"
    return s if x <= 0 else f"+{s}"


def checked(s: Setup) -> Setup:
    if s.mode not in MODES:
        raise ValueError(f"unknown mode {s.mode!r}: Mid, Touch, Grid or Smart")
    if s.bias not in BIASES:
        raise ValueError(f"unknown bias {s.bias!r}: Long, Neutral or Short")
    lo, hi = SPREAD_RANGE
    if not lo <= s.spread <= hi:
        raise ValueError(f"spread {s.spread:g} bps is out of range ({lo:g} to +{hi:g})")
    if s.mode == "grid" and s.spread < 0:
        raise ValueError("a Grid spread cannot be negative (it would sell below the last buy)")
    return Setup(s.mode, round(float(s.spread), 2) + 0.0, s.bias)


def parse(text: str) -> Setup:
    """'mid 0', 'Touch +1 long', 'grid 3 short', 'smart 0', 'mid+1'."""
    t = " ".join(text.lower().replace(",", " ").replace("·", " ").split())
    words = t.split()
    bias = "neutral"
    if words and words[-1] in BIASES:
        bias = words.pop()
    if len(words) == 1:
        m = re.fullmatch(r"(mid|touch|grid|smart)([+-]?\d+(?:\.\d+)?)", words[0])
        if m:
            words = [m.group(1), m.group(2)]
    if len(words) == 2 and words[0] in MODES:
        try:
            spread = float(words[1].removesuffix("bps").removesuffix("bp"))
        except ValueError:
            raise ValueError(f"spread {words[1]!r} is not a number of bps") from None
        return checked(Setup(words[0], spread, bias))
    if len(words) == 1 and words[0] in MODES:
        raise ValueError(f"{words[0].capitalize()} needs a spread, e.g. {words[0]} 0 or {words[0]} +1")
    raise ValueError(f"unknown setup {text!r}: e.g. mid 0, touch 0, mid +1 long, grid +2 short, smart 0")


def from_sid(sid: str) -> Setup | None:
    m = re.fullmatch(r"([mtgs])([+-]?\d+(?:\.\d+)?)([nls])", sid or "")
    if not m:
        return None
    mode = {"m": "mid", "t": "touch", "g": "grid", "s": "smart"}[m.group(1)]
    bias = {"n": "neutral", "l": "long", "s": "short"}[m.group(3)]
    try:
        return checked(Setup(mode, float(m.group(2)), bias))
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------ quoting
@dataclass
class View:
    """What the quoter sees at one decision."""

    t: int                     # µs
    bid: float
    ask: float
    bid_sz: float = 0.0        # size at the best bid and ask, as the market shows it
    ask_sz: float = 0.0
    own_bid_sz: float = 0.0    # our own size resting at those prices (left out of the book's imbalance)
    own_ask_sz: float = 0.0
    pos: float = 0.0           # signed base units
    entry: float | None = None
    pos_since: int = 0         # µs: when the position was last flat (0 = unknown)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class Quote:
    side: int
    px: float
    qty: float
    tag: str
    reduce_only: bool = False


@dataclass
class Plan:
    quotes: list[Quote] = field(default_factory=list)
    taker: float = 0.0          # signed base units to cross now (0 = none)
    why: str = ""


@dataclass(frozen=True)
class Rules:
    """The market and the sizes the quoter works with."""

    tick: float
    step: float
    min_usd: float              # Lighter's minimum order at this price (max(min_quote, min_base x price))
    order_usd: float
    cap_usd: float


def round_bid(p: float, tick: float) -> float:
    return math.floor(p / tick + 1e-6) * tick


def round_ask(p: float, tick: float) -> float:
    return math.ceil(p / tick - 1e-6) * tick


def imbalance(bid_sz: float, ask_sz: float) -> float:
    tot = bid_sz + ask_sz
    return (bid_sz - ask_sz) / tot if tot > 0 else 0.0


class Quoter:
    def __init__(self, p: Params) -> None:
        self.p = p
        self.ref: float | None = None          # Grid: the last fill
        self.resetting = False
        self.mids: deque[tuple[int, float]] = deque()

    # ---------------------------------------------------------------- state from fills
    def on_fill(self, side: int, px: float, tag: str, pos_after: float, step: float) -> None:
        if abs(pos_after) < step / 2:
            self.ref, self.resetting = None, False
        elif not tag.startswith("exit"):
            self.ref = px

    # ---------------------------------------------------------------- helpers
    def move_bps(self, t: int, mid: float) -> float:
        """The mid's move over the lookback, in bp (0 until there is that much history, or after a gap)."""
        self.mids.append((t, mid))
        cut = t - int(self.p.lookback_s * US)
        while len(self.mids) > 1 and self.mids[1][0] <= cut:
            self.mids.popleft()
        t0, m0 = self.mids[0]
        fresh = t0 > cut - 3 * US
        return (mid / m0 - 1) / BP if t0 <= cut and fresh and m0 > 0 else 0.0

    def skew(self, v: View, r: Rules) -> float:
        """u in [-1, 1]: the position against the bias's target, as a share of the cap."""
        if r.cap_usd <= 0:
            return 0.0
        target = self.p.bias * self.p.bias_frac * r.cap_usd
        return max(-1.0, min(1.0, (v.pos * v.mid - target) / r.cap_usd))

    def sizes(self, v: View, r: Rules, u: float) -> tuple[float, float]:
        """(bid qty, ask qty): the order skewed toward the target, never below Lighter's minimum, and no order that
        could take the position past 1.2x the cap."""
        mid = v.mid
        q = max(r.order_usd, r.min_usd * 1.2) / mid
        min_q = math.ceil(r.min_usd * 1.01 / mid / r.step - 1e-9) * r.step
        qb = 0.0 if u >= 1 else max(min_q, q * (1 - u))
        qa = 0.0 if u <= -1 else max(min_q, q * (1 + u))
        qb = math.floor(qb / r.step + 1e-9) * r.step
        qa = math.floor(qa / r.step + 1e-9) * r.step
        inv = v.pos * mid
        lim = 1.2 * r.cap_usd
        if inv + qb * mid > lim or inv >= r.cap_usd:
            qb = 0.0
        if inv - qa * mid < -lim or inv <= -r.cap_usd:
            qa = 0.0
        return qb, qa

    def exit_quote(self, v: View, why: str) -> Plan:
        """A reduce-only maker order at the touch for the whole position (empty when flat)."""
        if v.pos == 0:
            return Plan(why=why)
        side = SELL if v.pos > 0 else BUY
        return Plan([Quote(side, v.ask if side == SELL else v.bid, abs(v.pos), "exit", True)], why=why)

    # ---------------------------------------------------------------- the plan
    def plan(self, v: View, r: Rules) -> Plan:
        p = self.p
        mid, tick = v.mid, r.tick
        move = self.move_bps(v.t, mid) if p.mode == "smart" else 0.0
        if p.hold_s > 0 and v.pos != 0 and v.pos_since and v.t - v.pos_since >= p.hold_s * US:
            return Plan(taker=-v.pos, why=f"held {p.hold_s:g}s: close with a taker order")
        u = self.skew(v, r)
        if p.mode == "grid":
            plan = self._grid(v, r)
            if plan is not None:
                return plan
            h = max(p.spread * BP * mid, (v.ask - v.bid) / 2)
            if self.ref is None:
                bid, ask = mid - h, mid + h
            else:
                bid, ask = self.ref * (1 - p.spread * BP), self.ref * (1 + p.spread * BP)
            u = u if p.bias else 0.0
        elif p.mode == "touch":
            off = p.spread * BP * mid
            bid, ask = v.bid - off, v.ask + off
        else:   # mid, smart
            h = p.spread * BP
            res = mid * (1 - p.kappa * u * h)
            bid, ask = res - h * mid, res + h * mid
        # post-only: never at or through the other side
        bid = min(bid, v.ask - tick)
        ask = max(ask, v.bid + tick)
        bid, ask = round_bid(bid, tick), round_ask(ask, tick)
        if ask - bid < tick / 2:
            # both sides rounded to the same price (quoting at the mid of a book an even number of ticks wide): the
            # side that would add to the position steps back one tick
            if u >= 0:
                bid = ask - tick
            else:
                ask = bid + tick
        qb, qa = self.sizes(v, r, u)
        if p.mode == "smart":
            imb = imbalance(max(0.0, v.bid_sz - v.own_bid_sz), max(0.0, v.ask_sz - v.own_ask_sz))
            if u >= 0 and (imb < -p.imbalance or move < -p.move_bps):
                qb = 0.0
            if u <= 0 and (imb > p.imbalance or move > p.move_bps):
                qa = 0.0
        out: list[Quote] = []
        if qb > 0:
            out.append(Quote(BUY, bid, qb, "b"))
        if qa > 0:
            out.append(Quote(SELL, ask, qa, "a"))
        return Plan(out, why=f"{p.mode} {spread_text(p.spread)} u={u:+.2f}")

    def _grid(self, v: View, r: Rules) -> Plan | None:
        """Grid's soft reset: None to quote normally, else the exit."""
        if v.pos == 0:
            self.ref, self.resetting = None, False
            return None
        if self.ref is None and v.entry is not None:
            self.ref = v.entry   # a position with no last fill (after a restart): its entry stands in
        if self.ref is None:
            return None
        mid = v.mid
        adverse = (self.ref - mid) / self.ref if v.pos > 0 else (mid - self.ref) / self.ref
        if self.p.reset_pct > 0 and adverse > self.p.reset_pct / 100:
            self.resetting = True
        if self.resetting:
            return self.exit_quote(v, f"grid reset: the mid ran {adverse:.2%} against the last fill")
        return None
