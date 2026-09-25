"""Backtest one strategy config on one market's tape, with the live bot's risk rules and a conservative fill model.

Fill model (best bid/offer + trades only, so it works for every Arcus market we record):
- The bot decides once a second from the best bid/offer at that second. New orders go live, and cancels take effect,
  `latency_ms` later. A post-only order that would cross the book when it arrives is rejected.
- A resting order fills only when a taker trades THROUGH its price (a taker selling below our bid would have hit us
  first). A trade AT our price does not count: we cannot know our place in that queue.
- One taker order (Arcus sequenceNumber) fills us for at most what it printed strictly beyond our price: with our
  order there, the taker would have used up the better levels and the queue at our price before reaching us. This
  matters for large (leveraged) orders; we are assumed to be last in the queue, so it is a lower bound on fills.
- Maker fee 0. A taker exit fills at the opposite best price, pays the taker fee, and pays `slip_bps` extra on any
  size beyond what the best level shows.

Risk rules (the live bot enforces the same ones, from the same session fields):
- position cap: no new order that could take |position| past the cap;
- position stop: the open position loses `pos_stop_usd` -> cancel quotes, exit with a reduce-only maker order at the
  touch, cross the spread after `exit_taker_after_s`, then wait `cooldown_s`;
- daily stop: day PnL below -`daily_stop_usd` -> same exit, no new orders until 00:00 UTC, then resume by itself;
- kill: equity more than `kill_usd` below its peak -> taker flatten, stop for good (live needs a manual resume);
- safety pause: spread > 3x its 1-h median (and > 1 bp above it), or a 1-s move > 6 sigma -> no quotes for 30 s;
- a gap in the data over 10 minutes pulls all quotes (the live bot would see a stale feed);
- skip windows (Config.skip_et, the session's `skip_et`): no new quotes inside those New York hours on NYSE trading
  days; a position is worked off with a reduce-only maker order at the touch, as during a safety pause;
- liquidation distance: (equity - notional x MMF) / notional under 4 sigma of 1-h returns -> cut half the position
  with a taker order (the live risk engine's REDUCE_HALF); re-armed above 6 sigma;
- liquidation: equity at or below notional x MMF -> the venue closes everything (counted as a kill).

Leverage sets the size: at leverage L the position may reach capital x L (Arcus's own limit), so the inventory cap is
capital x L / 1.25 (the risk engine's hard cap is 1.25x it) and each order is half the cap. RWA perps outside their
session need 1.5x the initial margin to open, so the cap (and the order size with it) drops to `cap_off_usd` there.
The capital and the stops (as % of it) come from bot/common/sizing.py, the same code the live engine sizes with.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from bot.common.indicators import ema, rsi
from bot.common.sizing import Pct, sizes
from bot.common.time import NEW_YORK
from bot.scout.tape import DayTape

S = 1_000_000
BP = 1e-4
BUY, SELL = 1, -1


# ------------------------------------------------------------------------------------------------ config
@dataclass(frozen=True)
class Risk:
    capital_usd: float = 100.0
    order_usd: float = 25.0
    cap_usd: float = 50.0
    daily_stop_usd: float = 2.0
    pos_stop_usd: float = 1.0
    kill_usd: float = 10.0
    exit_taker_after_s: float = 20.0
    cooldown_s: float = 60.0
    cap_off_usd: float | None = None   # inventory cap outside the RWA session (higher initial margin); None = cap_usd
    leverage: float = 0.0              # the leverage the sizes came from (0 = sizes set by hand)
    leverage_off: float = 0.0
    used_usd: float = 0.0              # the capital the sizes and stops use (< capital_usd when order_max
                                       # binds); 0 = all of it
    order_max_usd: float = 0.0         # the liquidity ceiling when it limits these sizes (0 = it does not)
    liq_ceiling_usd: float = 0.0       # the market's liquidity ceiling for one order, binding or not (information)
    min_capital_usd: float = 0.0       # below this the venue minimum order, not the capital, would set the size

    @classmethod
    def at_leverage(cls, lev: float, lev_off: float | None = None, **kw: Any) -> Risk:
        """Sizes for leverage `lev`: position up to capital x lev, inventory cap = that / 1.25, order = cap / 2.
        The dollar stops are kept as given (see for_capital for stops that scale with the capital)."""
        base = cls(**kw)
        lev_off = lev if lev_off is None else min(lev_off, lev)
        cap = base.capital_usd * lev / 1.25
        return cls(**{**asdict(base), "order_usd": cap / 2, "cap_usd": cap,
                      "cap_off_usd": base.capital_usd * lev_off / 1.25, "leverage": lev, "leverage_off": lev_off})

    @classmethod
    def for_capital(cls, capital: float, lev: float, lev_off: float | None = None, *, pct: Pct | None = None,
                    order_max: float | None = None, min_capital: float = 0.0, **kw: Any) -> Risk:
        """Sizes and stops for `capital` at leverage `lev` (bot/common/sizing.py: the live engine's numbers)."""
        lev_off = lev if lev_off is None else min(lev_off, lev)
        s = sizes(capital, lev, lev_off, pct=pct, order_max=order_max)
        binds = s.capital < capital
        return cls(capital_usd=capital, order_usd=s.order, cap_usd=s.cap, cap_off_usd=s.cap_off,
                   daily_stop_usd=s.daily_stop, pos_stop_usd=s.pos_stop, kill_usd=s.kill, leverage=lev,
                   leverage_off=lev_off, used_usd=s.capital, order_max_usd=(order_max or 0.0) if binds else 0.0,
                   liq_ceiling_usd=order_max or 0.0, min_capital_usd=min_capital, **kw)

    @property
    def used(self) -> float:
        return self.used_usd or self.capital_usd

    def off_scale(self) -> float:
        return self.cap_off_usd / self.cap_usd if self.cap_off_usd and self.cap_usd else 1.0


@dataclass(frozen=True)
class Config:
    """One strategy setting. `mode` and the fields map 1:1 onto a session file (see scout/pilot.py)."""

    name: str
    mode: str                   # mid | grid | rgrid | signal | anchor
    style: str = "passive"      # mid: passive (fixed distance from mid) | normal | aggressive
    spacing_bps: float = 3.0
    levels: int = 1
    level_step_bps: float = 2.0
    kappa: float = 0.0
    reset_pct: float = 0.5      # grid / rgrid; anchor: the soft reset distance
    recentre_after_s: float = 120.0
    rgrid_ema_s: float = 300.0
    rgrid_cut_after_s: float = 20.0
    rsi_low: float = 25.0       # signal
    rsi_high: float = 75.0
    tp_bps: float = 15.0
    sl_bps: float = 25.0
    max_hold_min: float = 120.0
    cooldown_min: float = 5.0
    safety: bool = True         # the live safety pause (session safety_pause); off = thresholds out of reach
    skip_et: tuple[str, ...] = ()   # "HH:MM-HH:MM" New York windows on NYSE trading days with no new quotes


@dataclass(frozen=True)
class MarketInfo:
    tick: float
    step: float
    min_notional: float = 5.0
    min_size: float = 0.0
    taker_fee: float = 2.25 * BP
    maker_fee: float = 0.0
    mmf: float = 0.0            # maintenance margin fraction (0 = no liquidation checks)


@dataclass(frozen=True)
class SimParams:
    latency_ms: float = 150.0
    slip_bps: float = 5.0
    warmup_s: int = 2 * 3600
    gap_s: int = 600
    pool_start: int = 20_000    # Arcus order pool: 20,000 + $0.10 of lifetime fills per unit (bot/core/budget.py)
    liq_sigma: float = 4.0      # app risk.liq_distance_sigma
    liq_resume_sigma: float = 6.0
    front_of_queue: bool = False  # research only: prints AT our price fill us too (an upper bound on fills)


@dataclass
class Result:
    market: str
    config: str
    start_us: int
    end_us: int
    hours: float = 0.0
    maker_fills: int = 0
    maker_usd: float = 0.0
    taker_fills: int = 0
    taker_usd: float = 0.0
    fees: float = 0.0
    pnl: float = 0.0            # net, marked at mid, minus the cost of closing what is left (taker)
    min_equity_delta: float = 0.0
    pos_stops: int = 0
    day_stops: int = 0
    first_day_stop_us: int = 0
    killed: bool = False
    quoting_s: float = 0.0
    actions: int = 0            # order-pool actions (places + modifies); cancels use their own pool
    rejects: int = 0
    wide_s: float = 0.0         # seconds the budget governor doubled the requote tolerance
    frozen_s: float = 0.0       # seconds the order pool was nearly empty (cancels only)
    end_pos_usd: float = 0.0
    max_pos_usd: float = 0.0    # largest |position| reached
    liq_reduces: int = 0        # liquidation-distance cuts
    liquidated: bool = False
    tail_pnl: float = 0.0       # PnL over the last `tail_s` of the window (lower-time-frame check)
    tail_fills: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------------------------------------ helpers
def round_bid(p: float, tick: float) -> float:
    return math.floor(p / tick + 1e-9) * tick


def round_ask(p: float, tick: float) -> float:
    return math.ceil(p / tick - 1e-9) * tick


def base_for_usd(usd: float, px: float, step: float) -> float:
    return math.ceil(usd / px / step - 1e-9) * step if px > 0 else 0.0


@dataclass
class Order:
    side: int
    px: float
    qty: float
    tag: str
    live_from: int
    cancel_at: int = 1 << 62
    reduce_only: bool = False


@dataclass
class Book:
    """What a policy sees each second."""

    t: int
    bid: float
    ask: float
    mid: float
    pos: float                   # signed base units
    entry: float | None
    sigma_1m: float
    sigma_1h: float
    closes: deque[float]


# ------------------------------------------------------------------------------------------------ policies
class Policy:
    """Desired quotes, the same prices and sizes the live strategy computes (bot/strategies/*)."""

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        self.c, self.r, self.m = cfg, risk, mi
        self.scale = 1.0   # set each second: 1 in session, risk.off_scale() outside it (RWA off-hours margin)

    @property
    def cap(self) -> float:
        return self.r.cap_usd * self.scale

    @property
    def order_usd(self) -> float:
        return self.r.order_usd * self.scale

    def venue_min_usd(self, mid: float) -> float:
        return max(self.m.min_notional, self.m.min_size * mid)

    def q_base(self, mid: float) -> float:
        return base_for_usd(max(self.order_usd, self.venue_min_usd(mid) * 1.2), mid, self.m.step)

    def caps(self, b: Book, q: float) -> tuple[bool, bool]:
        inv = b.pos * b.mid
        cap = self.cap
        lim = 1.2 * cap
        qu = q * b.mid
        return (inv + qu > lim or inv >= cap), (inv - qu < -lim or inv <= -cap)

    def u(self, b: Book) -> float:
        return max(-1.0, min(1.0, b.pos * b.mid / self.cap)) if self.cap > 0 else 0.0

    def two_sided(self, b: Book, levels: list[tuple[float, float, str]], q: float, u: float,
                  no_buys: bool, no_sells: bool) -> list[tuple[int, float, float, str]]:
        min_q = base_for_usd(self.venue_min_usd(b.mid) * 1.01, b.mid, self.m.step)
        step = self.m.step
        qb = 0.0 if u >= 1 else math.floor(max(min_q, q * (1 - u)) / step + 1e-9) * step
        qa = 0.0 if u <= -1 else math.floor(max(min_q, q * (1 + u)) / step + 1e-9) * step
        out = []
        for bp, ap, tag in levels:
            if not no_buys and qb > 0:
                out.append((BUY, bp, qb, f"b{tag}"))
            if not no_sells and qa > 0:
                out.append((SELL, ap, qa, f"a{tag}"))
        return out

    def half_spread_ticks(self, b: Book) -> float:
        return self.c.spacing_bps * BP * b.mid / self.m.tick

    def quotes(self, b: Book) -> tuple[list[tuple[int, float, float, str]], float]:
        """[(side, px, qty, tag)], taker qty to send now (signed; 0 = none)."""
        raise NotImplementedError

    def on_fill(self, side: int, px: float, qty: float, tag: str, t: int, pos_after: float) -> None:
        return None


class MidPolicy(Policy):
    def quotes(self, b: Book) -> tuple[list[tuple[int, float, float, str]], float]:
        c, tick = self.c, self.m.tick
        h = c.spacing_bps * BP
        u = self.u(b)
        r = b.mid * (1 - c.kappa * u * h)
        spread = b.ask - b.bid
        if c.style == "aggressive":
            bid, ask = (b.bid + tick, b.ask - tick) if spread > tick * 1.5 else (b.bid, b.ask)
        elif c.style == "passive":
            bid, ask = r - h * b.mid, r + h * b.mid
        else:
            half = max(h * b.mid, spread / 2)
            bid, ask = r - half, r + half
        if bid >= b.ask:
            bid = b.ask - tick
        if ask <= b.bid:
            ask = b.bid + tick
        n = max(1, min(3, c.levels))
        step = c.level_step_bps * BP * b.mid
        levels = [(round_bid(bid - i * step, tick), round_ask(ask + i * step, tick), str(i)) for i in range(n)]
        q = self.q_base(b.mid)
        nb, ns = self.caps(b, q)
        return self.two_sided(b, levels, q, u, nb, ns), 0.0


class GridPolicy(Policy):
    """Static geometric grid; a filled buy at j re-lists as a sell at j+1 and vice versa. Re-centres when the mid stays
    more than reset_pct away for recentre_after_s; carried inventory is then exited by skewing sizes (skew_exit)."""

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self.center: float | None = None
        self.points: dict[int, int] = {}
        self.n = 0
        self.out_since: int | None = None
        self.skew = False

    def reset(self, mid: float) -> None:
        self.center = mid
        q_usd = max(self.order_usd, self.venue_min_usd(mid) * 1.2)
        self.n = max(1, min(12, self.c.levels)) if self.c.levels else max(1, int(self.cap // q_usd))
        self.points = {j: BUY for j in range(-self.n, 0)} | {j: SELL for j in range(1, self.n + 1)}
        self.out_since = None

    def quotes(self, b: Book) -> tuple[list[tuple[int, float, float, str]], float]:
        if self.center is None:
            self.reset(b.mid)
        assert self.center is not None
        dev = abs(b.mid - self.center) / self.center
        if dev <= self.c.reset_pct / 100:
            self.out_since = None
        elif self.out_since is None:
            self.out_since = b.t
        elif (b.t - self.out_since) / S >= self.c.recentre_after_s:
            self.reset(b.mid)
            self.skew = b.pos != 0
        if self.skew and b.pos == 0:
            self.skew = False
        q = self.q_base(b.mid)
        u = self.u(b) if self.skew else 0.0
        min_q = base_for_usd(self.venue_min_usd(b.mid) * 1.01, b.mid, self.m.step)
        qb, qa = (max(min_q, q * (1 - u)) if u < 1 else 0.0, max(min_q, q * (1 + u)) if u > -1 else 0.0) \
            if u else (q, q)
        nb, ns = self.caps(b, max(qb, qa))
        tick, d = self.m.tick, self.c.spacing_bps * BP
        out = []
        for j, side in sorted(self.points.items()):
            px = self.center * (1 + d) ** j
            if side == BUY and not nb and qb > 0:
                out.append((BUY, round_bid(min(px, b.ask - tick), tick), qb, f"g{j}"))
            elif side == SELL and not ns and qa > 0:
                out.append((SELL, round_ask(max(px, b.bid + tick), tick), qa, f"g{j}"))
        return out, 0.0

    def on_fill(self, side: int, px: float, qty: float, tag: str, t: int, pos_after: float) -> None:
        if not tag.startswith("g"):
            return
        j = int(tag[1:])
        if side == BUY and self.points.get(j) == BUY:
            self.points.pop(j, None)
            if j + 1 <= self.n:
                self.points[j + 1] = SELL
        elif side == SELL and self.points.get(j) == SELL:
            self.points.pop(j, None)
            if j - 1 >= -self.n:
                self.points[j - 1] = BUY


class RGridPolicy(Policy):
    """Trailing grid on an EMA of mid; jumps to mid past reset_pct; inventory beyond 1.5 clips that is more than one
    level under water is cut (maker at the touch, then a taker order every rgrid_cut_after_s)."""

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self.ema: float | None = None
        self.center: float | None = None
        self.last_t = 0
        self.cut_side = 0
        self.cut_since = 0

    def quotes(self, b: Book) -> tuple[list[tuple[int, float, float, str]], float]:
        dt = (b.t - self.last_t) / S if self.last_t else 1.0
        self.last_t = b.t
        a = 1 - math.exp(-dt / max(1.0, self.c.rgrid_ema_s))
        self.ema = b.mid if self.ema is None else self.ema + a * (b.mid - self.ema)
        if self.center is None:
            self.center = self.ema
        d = self.c.spacing_bps * BP
        q1 = base_for_usd(max(self.order_usd, self.venue_min_usd(b.mid) * 1.2), b.mid, self.m.step)
        if abs(b.mid - self.center) / self.center > self.c.reset_pct / 100:
            self.center = b.mid
        elif self.cut_side == 0:
            self.center = self.ema
        entry = b.entry or b.mid
        adverse = (b.mid - entry) / entry * (-1 if b.pos > 0 else 1) if b.pos else 0.0
        if self.cut_side == 0 and abs(b.pos) - 1.5 * q1 > 0 and adverse > d:
            self.cut_side = BUY if b.pos < 0 else SELL
            self.cut_since = b.t
        tick = self.m.tick
        n = max(1, min(3, self.c.levels))
        levels = []
        for k in range(1, n + 1):
            bp, ap = self.center * (1 - k * d), self.center * (1 + k * d)
            if bp >= b.ask:
                bp = b.ask - tick
            if ap <= b.bid:
                ap = b.bid + tick
            levels.append((round_bid(bp, tick), round_ask(ap, tick), str(k)))
        q = self.q_base(b.mid)
        nb, ns = self.caps(b, q)
        out = self.two_sided(b, levels, q, self.u(b), nb or self.cut_side == SELL, ns or self.cut_side == BUY)
        taker = 0.0
        if self.cut_side:
            if abs(b.pos) <= 1.5 * q1 or (self.cut_side == SELL and b.pos < 0) or (self.cut_side == BUY and b.pos > 0):
                self.cut_side = 0
            else:
                side = SELL if b.pos > 0 else BUY
                out.append((side, b.ask if side == SELL else b.bid, abs(b.pos), "exit"))
                if (b.t - self.cut_since) / S >= self.c.rgrid_cut_after_s:
                    size = max(abs(b.pos) - q1, 0.0)
                    taker = -size if b.pos > 0 else size
                    self.cut_since = b.t
        return out, taker


class SignalPolicy(Policy):
    """RSI(14) on 1-minute mids with a flat-trend filter; maker entry at the touch, maker take-profit, taker stop,
    maker exit after max_hold_min, cooldown after each trade."""

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self.entry_px: float | None = None
        self.entry_t = 0
        self.cool_until = 0
        self._ind_min = -1                                  # the indicators only change when a minute closes
        self._ind: tuple[float | None, float | None, float | None] = (None, None, None)

    def quotes(self, b: Book) -> tuple[list[tuple[int, float, float, str]], float]:
        c, tick = self.c, self.m.tick
        minute = b.t // (60 * S)
        if minute != self._ind_min:   # closes are appended on minute boundaries only
            closes = list(b.closes)
            self._ind = (rsi(closes, 14), ema(closes[-120:], 20), ema(closes[-180:], 60))
            self._ind_min = minute
        r, e20, e60 = self._ind
        sig_px = b.mid * b.sigma_1h
        flat = e20 is not None and e60 is not None and sig_px > 0 and abs(e20 - e60) < 1.0 * sig_px
        if b.pos != 0:
            if self.entry_px is None:
                self.entry_px, self.entry_t = b.mid, b.t
            long = b.pos > 0
            pnl_bps = (b.mid - self.entry_px) / self.entry_px / BP * (1 if long else -1)
            if pnl_bps <= -c.sl_bps:
                self.cool_until = b.t + int(c.cooldown_min * 60 * S)
                self.entry_px = None
                return [], -b.pos
            if (b.t - self.entry_t) / 60e6 >= c.max_hold_min:
                return [(SELL if long else BUY, b.ask if long else b.bid, abs(b.pos), "sig_time")], 0.0
            tp = self.entry_px * (1 + c.tp_bps * BP) if long else self.entry_px * (1 - c.tp_bps * BP)
            tp = max(tp, b.bid + tick) if long else min(tp, b.ask - tick)
            tp = round_ask(tp, tick) if long else round_bid(tp, tick)
            return [(SELL if long else BUY, tp, abs(b.pos), "sig_tp")], 0.0
        self.entry_px = None
        if b.t < self.cool_until or r is None or not flat:
            return [], 0.0
        q = self.q_base(b.mid)
        if r < c.rsi_low:
            return [(BUY, b.bid, q, "sig_entry_long")], 0.0
        if r > c.rsi_high:
            return [(SELL, b.ask, q, "sig_entry_short")], 0.0
        return [], 0.0

    def on_fill(self, side: int, px: float, qty: float, tag: str, t: int, pos_after: float) -> None:
        if tag.startswith("sig_entry"):
            self.entry_px, self.entry_t = px, t
        elif pos_after == 0:
            self.cool_until = t + int(self.c.cooldown_min * 60 * S)


class AnchorPolicy(Policy):
    """Quotes around the last fill (the Grid mode of Tread users): flat, mid +/- max(d, half the spread); holding a
    position, last fill x (1 -/+ d), so a sell never goes below the last buy + d. When the mid runs more than
    reset_pct against the position from the last fill, it stops adding and closes at the touch; flat again, it
    starts over around the mid."""

    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo) -> None:
        super().__init__(cfg, risk, mi)
        self.ref: float | None = None
        self.resetting = False

    def quotes(self, b: Book) -> tuple[list[tuple[int, float, float, str]], float]:
        c, tick = self.c, self.m.tick
        d = c.spacing_bps * BP
        if b.pos == 0 or self.ref is None:
            self.ref, self.resetting = None, False
            half = max(d * b.mid, (b.ask - b.bid) / 2)
            bid, ask = b.mid - half, b.mid + half
        else:
            adverse = (self.ref - b.mid) / self.ref if b.pos > 0 else (b.mid - self.ref) / self.ref
            if c.reset_pct > 0 and adverse > c.reset_pct / 100:
                self.resetting = True
            if self.resetting:
                side = SELL if b.pos > 0 else BUY
                return [(side, b.ask if side == SELL else b.bid, abs(b.pos), "exit")], 0.0
            bid, ask = self.ref * (1 - d), self.ref * (1 + d)
        if bid >= b.ask:
            bid = b.ask - tick
        if ask <= b.bid:
            ask = b.bid + tick
        q = self.q_base(b.mid)
        nb, ns = self.caps(b, q)
        return self.two_sided(b, [(round_bid(bid, tick), round_ask(ask, tick), "0")], q, 0.0, nb, ns), 0.0

    def on_fill(self, side: int, px: float, qty: float, tag: str, t: int, pos_after: float) -> None:
        if abs(pos_after) < self.m.step / 2:
            self.ref, self.resetting = None, False
        elif tag != "exit":
            self.ref = px


POLICIES: dict[str, type[Policy]] = {"mid": MidPolicy, "grid": GridPolicy, "rgrid": RGridPolicy,
                                     "signal": SignalPolicy, "anchor": AnchorPolicy}


# ------------------------------------------------------------------------------------------------ simulator
NEVER = 1 << 62


class Window:
    """One market's tape over [w0, end), prepared once and shared by every config: the best bid/offer as of each whole
    second, data age, and the safety-pause flags the live bot computes at 1 Hz (bot/core/marketdata.py, risk.py)."""

    def __init__(self, tape: DayTape, start_us: int, end_us: int, warmup_s: int = 2 * 3600,
                 alive_ts: np.ndarray | None = None, rth: Any = None, holidays: list[str] | None = None) -> None:
        """alive_ts: timestamps that prove the recorder was up (the busiest market's rows). A quiet book can go
        many minutes without a change; that is not missing data, a recorder outage is.
        rth: callable(seconds array) -> bool array, True while the underlying's session is open (RWA perps);
        None = always in session (crypto).
        holidays: NYSE full-day holidays (ISO dates), for the skip windows (Config.skip_et)."""
        b, tr = tape.bbo, tape.trades
        self._cols: tuple[list[Any], ...] | None = None
        self._skip: dict[tuple[str, ...], list[bool]] = {}
        self.holidays = set(holidays or ())
        self.market, self.start_us, self.end_us = tape.market, start_us, end_us
        first = int(b["ts"][0]) if len(b["ts"]) else start_us
        w0 = max(start_us - warmup_s * S, first)
        self.w0 = w0 - w0 % S
        n = self.n = max(0, int((end_us - self.w0) // S))
        self.bbo = b
        self.tts, self.tpx, self.tsz, self.tbuy, self.tseq = tr["ts"], tr["px"], tr["sz"], tr["buy"], tr["seq"]
        t = self.t = self.w0 + np.arange(n, dtype=np.int64) * S
        self.rth = np.asarray(rth(t), bool) if rth is not None else np.ones(n, bool)
        if not n or not len(b["ts"]):
            self.ok = np.zeros(n, bool)
            return
        idx = np.searchsorted(b["ts"], t, side="right") - 1
        have = idx >= 0
        idx = np.clip(idx, 0, None)
        self.bid = np.where(have, b["bid"][idx], np.nan)
        self.ask = np.where(have, b["ask"][idx], np.nan)
        self.bsz = np.where(have, b["bid_sz"][idx], 0.0)
        self.asz = np.where(have, b["ask_sz"][idx], 0.0)
        last = np.where(have, b["ts"][idx], 0)
        for src in (self.tts, alive_ts if alive_ts is not None else np.zeros(0, np.int64)):
            if len(src):  # a trade, or any row from the recorder, proves the feed was alive
                k = np.searchsorted(src, t, side="right") - 1
                last = np.maximum(last, np.where(k >= 0, src[np.clip(k, 0, None)], 0))
        self.age = np.where(have, (t - last) / S, np.inf)
        self.ok = have & (self.bid > 0) & (self.ask > self.bid)
        self.mid = np.where(self.ok, (self.bid + self.ask) / 2, np.nan)
        self.trade_idx = np.searchsorted(self.tts, np.append(t, t[-1] + S), side="left")
        # safety pause: 6-sigma 1-s move (EWMA, 1-min half-life, floor 1 bp) or spread > 3x its 1-h median
        good = self.ok & np.roll(self.ok, 1)
        good[0] = False
        ret = np.zeros(n)
        ret[good] = np.log(self.mid[good] / np.roll(self.mid, 1)[good])
        alpha = 1 - math.exp(math.log(0.5) / 60)
        var = np.zeros(n)
        cnt = np.cumsum(good)
        v = 0.0
        seen = False
        r2 = ret * ret
        for i in np.flatnonzero(good):
            v = r2[i] if not seen else (1 - alpha) * v + alpha * r2[i]
            seen = True
            var[i] = v
        # forward-fill var over seconds without a new return
        pos = np.where(good, np.arange(n), 0)
        np.maximum.accumulate(pos, out=pos)
        var = var[pos]
        move = (cnt > 60) & (np.abs(ret) > 6 * np.maximum(np.sqrt(var), 1e-4))
        spread = np.where(self.ok, (self.ask - self.bid) / np.where(self.ok, self.mid, 1) / BP, np.nan)
        self.spread_bps = spread
        med = np.full(n, np.nan)
        for m0 in range(0, n, 60):
            w = spread[max(0, m0 - 3600):m0]
            w = w[~np.isnan(w)]
            if len(w) >= 600:
                med[m0:m0 + 60] = np.median(w)
        wide = self.ok & ~np.isnan(med) & (spread > 3 * med) & (spread - med > 1.0)
        cs = np.cumsum((move | wide).astype(np.int64))
        self.paused = (cs - np.concatenate([np.zeros(min(30, n), np.int64), cs[:-30]])) > 0

    def columns(self) -> tuple[list[Any], ...]:
        """The per-second arrays as Python lists: t, ok, mid, bid, ask, age, paused, rth, bid size, ask size, trade
        index. Reading one element at a time is several times faster from a list than from a numpy array, and the
        simulation reads them every second; built once and shared by every config run on this window."""
        if self._cols is None:
            self._cols = (self.t.tolist(), self.ok.tolist(), self.mid.tolist(), self.bid.tolist(), self.ask.tolist(),
                          self.age.tolist(), self.paused.tolist(), self.rth.tolist(), self.bsz.tolist(),
                          self.asz.tolist(), self.trade_idx.tolist())
        return self._cols

    def skip(self, windows: tuple[str, ...]) -> list[bool]:
        """Per second: True inside any "HH:MM-HH:MM" New York window on an NYSE trading day (weekdays that are not
        full holidays; the live bot's TradingCalendar.in_skip_window). Built once per set of windows."""
        if windows not in self._skip:
            out = np.zeros(self.n, bool)
            if self.n:
                d = dt.datetime.fromtimestamp(int(self.t[0]) / S, NEW_YORK).date() - dt.timedelta(days=1)
                last = dt.datetime.fromtimestamp(int(self.t[-1]) / S, NEW_YORK).date()
                while d <= last:
                    if d.weekday() < 5 and d.isoformat() not in self.holidays:
                        for w in windows:
                            (ah, am), (bh, bm) = ((int(x) for x in hm.split(":")) for hm in w.split("-"))
                            t0 = int(dt.datetime.combine(d, dt.time(ah, am), NEW_YORK).timestamp()) * S
                            t1 = int(dt.datetime.combine(d, dt.time(bh, bm), NEW_YORK).timestamp()) * S
                            out |= (self.t >= t0) & (self.t < t1)
                    d += dt.timedelta(days=1)
            self._skip[windows] = out.tolist()
        return self._skip[windows]

    def bbo_at(self, ts: int) -> tuple[float, float]:
        k = int(np.searchsorted(self.bbo["ts"], ts, side="right")) - 1
        if k < 0:
            return math.nan, math.nan
        return float(self.bbo["bid"][k]), float(self.bbo["ask"][k])


class Sim:
    def __init__(self, cfg: Config, risk: Risk, mi: MarketInfo, sp: SimParams | None = None) -> None:
        self.cfg, self.risk, self.mi = cfg, risk, mi
        self.sp = sp or SimParams()

    def run(self, w: Window, *, tail_s: int = 6 * 3600) -> Result:
        cfg, risk, mi, sp = self.cfg, self.risk, self.mi, self.sp
        res = Result(w.market, cfg.name, w.start_us, w.end_us)
        if not w.n or not w.ok.any():
            res.notes.append("no data")
            return res
        policy = POLICIES[cfg.mode](cfg, risk, mi)
        T, OK, MID, BID, ASK, AGE, PAUSED, RTH, BSZ, ASZ, TIDX = w.columns()
        SKIP = w.skip(cfg.skip_et) if cfg.skip_et else None
        lat = int(sp.latency_ms * 1000)
        tick = mi.tick
        tts, tpx, tsz, tbuy, tseq = w.tts, w.tpx, w.tsz, w.tbuy, w.tseq
        ntr = len(tts)
        orders: list[Order] = []
        # "ov" counts changes to the live orders (fills, cancels, placements): with it, a second whose order diff
        # would change nothing (same wanted quotes, book, budget mode, orders) skips the diff (see `sync`)
        st: dict[str, Any] = {"pos": 0.0, "entry": None, "cash": risk.capital_usd, "ov": 0, "purge_at": NEVER}
        last_fp: tuple[Any, ...] | None = None
        start_eq: float | None = None
        peak_eq = -math.inf
        day_eq: float | None = None
        day = -1
        state = "normal"   # normal | exit_pos | exit_day | day_stopped | cooldown | killed
        exit_since = cool_until = 0
        done_seq = -1
        closes: deque[float] = deque(maxlen=240)
        vol_1m: float | None = None
        a1m = 1 - math.exp(math.log(0.5) / 30)
        last_min_mid: float | None = None
        tail_from = w.end_us - tail_s * S
        tail_eq0: float | None = None
        last_mid = math.nan
        off_scale = risk.off_scale()
        liq_active = False
        cache_key: tuple[Any, ...] | None = None
        cache_q: list[tuple[int, float, float, str]] = []
        # Arcus order-pool governor (bot/core/budget.py): pool grows 1 unit per $0.10 filled; WIDE (2x requote
        # tolerance) when order actions per filled $ over 6 h > 8 or the pool is under 20%; cancels only under 5%.
        bud: dict[str, Any] = {"pool": float(sp.pool_start), "cap": float(sp.pool_start), "total": 0,
                               "acts": deque(), "fills": deque(), "a6": 0, "f6": 0.0, "mult": 1.0}

        def act(t: int) -> None:
            res.actions += 1
            bud["pool"] -= 1
            bud["total"] += 1
            bud["acts"].append(t)
            bud["a6"] += 1

        def budget_mode(t: int) -> float:
            cut = t - 6 * 3600 * S
            while bud["acts"] and bud["acts"][0] < cut:
                bud["acts"].popleft()
                bud["a6"] -= 1
            while bud["fills"] and bud["fills"][0][0] < cut:
                bud["f6"] -= bud["fills"].popleft()[1]
            frac = bud["pool"] / bud["cap"]
            if frac < 0.05:
                return math.inf
            if frac < 0.20:
                return 2.0
            if bud["total"] >= 200 and (bud["a6"] / bud["f6"] if bud["f6"] > 0 else math.inf) > 8:
                return 2.0
            return 1.0

        def book_fill(side: int, px: float, qty: float, maker: bool, t: int, tag: str) -> None:
            pos, entry = st["pos"], st["entry"]
            fee = qty * px * (mi.maker_fee if maker else mi.taker_fee)
            st["cash"] -= side * qty * px + fee
            res.fees += fee
            new = pos + side * qty
            if pos == 0 or (pos > 0) == (side > 0):
                entry = px if pos == 0 or entry is None else (entry * abs(pos) + px * qty) / (abs(pos) + qty)
            elif (new > 0) != (pos > 0) and abs(new) > mi.step / 2:
                entry = px
            if abs(new) < mi.step / 2:
                new, entry = 0.0, None
            st["pos"], st["entry"] = new, entry
            units = qty * px / 0.10
            bud["pool"] += units
            bud["cap"] += units
            bud["fills"].append((t, qty * px))
            bud["f6"] += qty * px
            if maker:
                res.maker_fills += 1
                res.maker_usd += qty * px
                if t >= tail_from:
                    res.tail_fills += 1
            else:
                res.taker_fills += 1
                res.taker_usd += qty * px
            policy.on_fill(side, px, qty, tag, t, new)

        def taker(qty_signed: float, i: int, t: int) -> None:
            pos = st["pos"]
            side = BUY if qty_signed > 0 else SELL
            qty = abs(qty_signed)
            if (side == SELL and pos > 0) or (side == BUY and pos < 0):
                qty = min(qty, abs(pos))
            if qty <= 0 or not OK[i]:
                return
            px = ASK[i] if side == BUY else BID[i]
            shown = ASZ[i] if side == BUY else BSZ[i]
            extra = max(0.0, qty - shown) / qty if shown > 0 else 1.0
            book_fill(side, px * (1 + side * sp.slip_bps * BP * extra), qty, False, t, "taker")

        def cancel_all(t: int) -> None:
            for o in orders:
                if o.cancel_at == NEVER:
                    o.cancel_at = t + lat
                    st["ov"] += 1
                    st["purge_at"] = min(st["purge_at"], o.cancel_at)

        def sync(desired: list[tuple[int, float, float, str, bool]], i: int, t: int, half_ticks: float) -> bool:
            """The order manager's diff (bot/core/order_manager.py plan): keep a live order within the requote
            tolerance and 20% of size, else replace it; the old one keeps filling until its cancel takes effect.
            Returns whether it did anything (a cancel, a placement, or a rejected placement): a diff that did
            nothing does nothing again until one of its inputs changes, which lets the caller skip it."""
            acted = False
            mult = bud["mult"]
            tol = max(2.0, 0.25 * half_ticks) * (1.0 if math.isinf(mult) else mult)
            active = [o for o in orders if o.cancel_at == NEVER]
            used: set[int] = set()
            new: list[Order] = []
            bb, ba = BID[i], ASK[i]
            arr_bid = arr_ask = math.nan
            for side, px, qty, tag, ro in desired:
                if qty <= 0 or px <= 0:
                    continue
                if side == BUY and px >= ba:
                    px = ba - tick
                if side == SELL and px <= bb:
                    px = bb + tick
                if not ro and qty * px < mi.min_notional:
                    continue
                m = next((o for o in active if id(o) not in used and o.side == side and o.tag == tag), None)
                if m is not None:
                    used.add(id(m))
                    if abs(m.px - px) / tick <= tol and abs(m.qty - qty) / qty <= 0.2 and m.reduce_only == ro:
                        continue
                    if math.isinf(mult):
                        continue  # cancels only: leave it
                    m.cancel_at = t + lat
                    acted = True
                elif math.isinf(mult):
                    continue
                if math.isnan(arr_bid):
                    arr_bid, arr_ask = w.bbo_at(t + lat)
                acted = True
                if (side == BUY and px >= arr_ask) or (side == SELL and px <= arr_bid):
                    res.rejects += 1
                    continue
                new.append(Order(side, px, qty, tag, t + lat, reduce_only=ro))
                act(t)
            for o in active:
                if id(o) not in used:
                    o.cancel_at = t + lat
                    acted = True
            orders[:] = [o for o in orders if o.cancel_at > t] + new
            st["purge_at"] = min((o.cancel_at for o in orders), default=NEVER)
            if acted:
                st["ov"] += 1
            return acted

        for i in range(w.n):
            t = T[i]
            ok = OK[i]
            if ok:
                mid = MID[i]
                if t % (60 * S) == 0:
                    if last_min_mid:
                        r = math.log(mid / last_min_mid)
                        vol_1m = r * r if vol_1m is None else (1 - a1m) * vol_1m + a1m * r * r
                    last_min_mid = mid
                    closes.append(mid)
                last_mid = mid
            if t < w.start_us:
                continue
            if not ok:
                cancel_all(t)
            else:
                pos = st["pos"]
                eq = st["cash"] + pos * mid
                if start_eq is None:
                    start_eq = eq
                if tail_eq0 is None and t >= tail_from:
                    tail_eq0 = eq
                if eq - start_eq < res.min_equity_delta:
                    res.min_equity_delta = eq - start_eq
                d = t // (86_400 * S)
                if d != day:
                    day, day_eq = d, eq
                    if state == "day_stopped":
                        state = "normal"
                policy.scale = 1.0 if RTH[i] else off_scale
                notional = abs(pos) * mid
                if notional > res.max_pos_usd:
                    res.max_pos_usd = notional
                # ---- risk rules
                if eq > peak_eq:
                    peak_eq = eq
                if state != "killed" and notional > 0 and mi.mmf > 0 and eq <= notional * mi.mmf:
                    state, res.killed, res.liquidated = "killed", True, True   # the venue closes it all
                    cancel_all(t)
                    taker(-pos, i, t)
                elif state != "killed" and peak_eq - eq > risk.kill_usd:
                    state, res.killed = "killed", True
                    cancel_all(t)
                    taker(-pos, i, t)
                elif state not in ("exit_day", "day_stopped", "killed") and day_eq is not None \
                        and eq - day_eq < -risk.daily_stop_usd:
                    state, exit_since = "exit_day", t
                    res.day_stops += 1
                    res.first_day_stop_us = res.first_day_stop_us or t
                elif state == "normal" and pos and st["entry"] is not None \
                        and pos * (mid - st["entry"]) <= -risk.pos_stop_usd:
                    state, exit_since = "exit_pos", t
                    res.pos_stops += 1
                if state == "cooldown" and t >= cool_until:
                    state = "normal"
                if notional > 0 and mi.mmf > 0 and state != "killed":   # live risk engine REDUCE_HALF
                    sig_h = math.sqrt(vol_1m * 60) if vol_1m else 0.0
                    n_sig = (eq - notional * mi.mmf) / notional / sig_h if sig_h > 0 else math.inf
                    if n_sig < sp.liq_sigma and not liq_active:
                        liq_active = True
                        res.liq_reduces += 1
                        half = math.floor(abs(pos) / 2 / mi.step + 1e-9) * mi.step
                        if half > 0:
                            taker(-half if pos > 0 else half, i, t)
                    elif n_sig >= sp.liq_resume_sigma:
                        liq_active = False
                desired: list[tuple[int, float, float, str, bool]] = []
                half_ticks = 2.0
                if state in ("exit_pos", "exit_day"):
                    if st["pos"] == 0:
                        if state == "exit_pos":
                            state, cool_until = "cooldown", t + int(risk.cooldown_s * S)
                        else:
                            state = "day_stopped"
                    elif (t - exit_since) / S >= risk.exit_taker_after_s:
                        cancel_all(t)
                        taker(-st["pos"], i, t)
                        exit_since = t
                    else:
                        side = SELL if st["pos"] > 0 else BUY
                        desired = [(side, ASK[i] if side == SELL else BID[i], abs(st["pos"]), "exit",
                                    True)]
                elif state == "normal":
                    if AGE[i] <= sp.gap_s and not (cfg.safety and PAUSED[i]) and not (SKIP and SKIP[i]):
                        key = (BID[i], ASK[i], st["pos"], policy.scale)
                        if cfg.mode == "mid" and key == cache_key:
                            q, tq = cache_q, 0.0
                        else:
                            sig = math.sqrt(vol_1m) if vol_1m else 0.0
                            b = Book(t, BID[i], ASK[i], mid, st["pos"], st["entry"], sig,
                                     sig * math.sqrt(60), closes)
                            q, tq = policy.quotes(b)
                            cache_key, cache_q = key, q
                        desired = [(s, p, qq, tg, tg == "exit" or tg in ("sig_tp", "sig_time")) for s, p, qq, tg in q]
                        half_ticks = cfg.spacing_bps * BP * mid / tick
                        res.quoting_s += 1
                        if tq:
                            taker(tq, i, t)
                    elif st["pos"] != 0:  # paused, stale or a skip window: the strategy's exit book
                        side = SELL if st["pos"] > 0 else BUY
                        desired = [(side, ASK[i] if side == SELL else BID[i], abs(st["pos"]), "exit",
                                    True)]
                if desired or orders:
                    bud["mult"] = budget_mode(t)
                    if math.isinf(bud["mult"]):
                        res.frozen_s += 1
                    elif bud["mult"] > 1:
                        res.wide_s += 1
                    fp = (tuple(desired), BID[i], ASK[i], bud["mult"], half_ticks, st["ov"])
                    if fp == last_fp:   # the diff did nothing last time and nothing it reads has changed
                        if t >= st["purge_at"]:
                            orders[:] = [o for o in orders if o.cancel_at > t]
                            st["purge_at"] = min((o.cancel_at for o in orders), default=NEVER)
                    else:
                        last_fp = None if sync(desired, i, t, half_ticks) else fp
            # ---- trades during this second fill resting orders. One taker order (one sequenceNumber) is handled
            # whole: with our order resting at p, the taker would have used up the better levels and the queue at p
            # first, so only what it printed strictly beyond p could have filled us.
            k, b_ = TIDX[i], TIDX[i + 1]
            if k == b_ or not orders:
                continue
            while k < b_:
                seq = int(tseq[k])
                j = k + 1
                if seq != 0:
                    while j < ntr and int(tseq[j]) == seq:
                        j += 1
                if seq != 0 and seq == done_seq:   # the tail of a taker order already handled last second
                    k = j
                    continue
                done_seq = seq
                ts = int(tts[k])
                ms = SELL if tbuy[k] else BUY
                cands = [o for o in orders if o.side == ms and o.live_from <= ts < o.cancel_at and o.qty > 0]
                if cands:
                    ppx, psz = tpx[k:j], tsz[k:j]
                    cands.sort(key=lambda o: -o.px if ms == BUY else o.px)   # the taker reaches our best price first
                    taken = 0.0
                    for o in cands:
                        if sp.front_of_queue:
                            beyond = float(psz[ppx <= o.px].sum() if ms == BUY else psz[ppx >= o.px].sum())
                        else:
                            beyond = float(psz[ppx < o.px].sum() if ms == BUY else psz[ppx > o.px].sum())
                        fq = min(o.qty, beyond - taken)
                        if o.reduce_only:
                            fq = min(fq, abs(st["pos"]))
                        if fq <= mi.step / 2:
                            continue
                        o.qty -= fq
                        st["ov"] += 1
                        taken += fq
                        book_fill(o.side, o.px, fq, True, ts, o.tag)
                k = j
            orders[:] = [o for o in orders if o.qty > mi.step / 2]

        # ---- mark at mid; charge the cost of crossing out of what is left
        pos = st["pos"]
        end_eq = st["cash"] + pos * last_mid if not math.isnan(last_mid) else st["cash"]
        if pos:
            k = w.n - 1
            half = float(w.ask[k] - w.bid[k]) / 2 if w.ok[k] else 0.0
            end_eq -= abs(pos) * (half + last_mid * mi.taker_fee)
            res.end_pos_usd = pos * last_mid
        res.pnl = end_eq - (start_eq if start_eq is not None else end_eq)
        res.tail_pnl = end_eq - (tail_eq0 if tail_eq0 is not None else end_eq)
        res.hours = float(w.ok[w.t >= w.start_us].sum()) / 3600
        return res
