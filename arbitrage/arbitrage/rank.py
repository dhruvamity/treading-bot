"""The arithmetic of one funding arbitrage: which venue to short, how big, what it should pay, where the stops go.

Both venues charge funding every hour: rate x position value, + = longs pay shorts. The same market held short on
the venue with the higher rate and long on the other earns the difference each hour, per dollar of each leg, while the
two legs' price moves cancel. What is left to lose:
- the cost of getting in and out (four fills);
- the two venues' prices drifting apart (the basis), which comes back but not on a schedule;
- a liquidation of the losing leg: its loss sits on one venue and the other leg's gain on the other, so each leg needs
  its own margin for the whole move. Both legs therefore carry a stop well inside the liquidation price and a take
  profit at the same distance: when either triggers, the pair is closed together.

Everything here is pure: no network, no clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

HOURS_YEAR = 8760.0
CRYPTO_CLASSES = ("CRYPTO", "CRYPTOCURRENCY", "MEME")


@dataclass(frozen=True)
class Leg:
    """One venue's side of a market, as that venue reports it."""
    venue: str                  # "arcus" | "lighter"
    symbol: str                 # the base symbol both venues share, e.g. "SPY"
    rate_h: float               # funding rate of the last payment, a fraction per hour (+ = longs pay)
    next_rate_h: float          # the venue's own estimate of the next payment's rate
    next_at: int                # unix seconds of the next payment
    mark: float
    imf: float                  # initial margin fraction at the highest leverage, regular hours
    imf_off: float              # the same while the underlying is closed (stocks on Arcus); else = imf
    mmf: float                  # maintenance margin fraction
    min_notional: float
    min_size: float
    step: float
    tick: float
    volume_24h: float
    oi_usd: float
    online: bool = True
    off_hours: bool = False
    category: str = ""          # the venue's own asset class: "EQUITIES", "INDICES", "COMMODITIES", "CRYPTO" (Arcus)


@dataclass(frozen=True)
class Settings:
    fill_cost_bp: float = 1.0       # what one maker fill costs against the mid, per leg (a tick or two, adverse fills)
    stop_frac: float = 0.5          # the stop sits at this share of the distance to liquidation
    stop_sigmas: float = 0.0        # ... and at least this many daily moves away, which lowers the leverage on a
                                    # market that moves a lot. 0 = off: the leverage is the highest the venues allow
                                    # (the owner's choice of 10 Oct 2026; it was 3)
    margin_use: float = 0.9         # share of a venue's collateral the position may use at that leverage
    hold_off_hours: bool = False    # False: sized by the margin of the hour it is opened in (Arcus asks 1.5 times
                                    # the margin while the stock market is closed, and keeps a position opened
                                    # earlier). True: always sized by the off-hours margin
    min_edge_apr: float = 5.0       # % a year on the notional of one leg, before costs
    max_breakeven_h: float = 48.0   # hours of funding that pay for getting in and out
    min_volume_24h: float = 100_000.0
    max_leverage: float = 50.0      # the owner's own ceiling: 50 = whatever the venues allow (it was 20)
    max_notional_usd: float = 0.0   # never more than this many dollars a leg (0 = no limit): for a first small run
    max_margin_usd: float = 0.0     # never more than this many dollars of each venue's money as margin (0 = no
                                    # limit: margin_use of the smaller balance). The position is this x leverage
    profunding_side: bool = False   # True: which venue is short is ProFunding's answer for the pair and nothing else;
                                    # a market it does not list, or a day it cannot be read, is not opened
    rwa_only: bool = True           # stocks, indices and commodities only: a market Arcus calls CRYPTO is never opened
    # ---- the money on the two venues (a position's gain lands on one venue and its loss on the other)
    rebalance_share: float = 0.40   # flat, one venue under this share of the money: say how much to move
    drift_close_share: float = 0.0  # open, one venue down to this share (its leg's loss counted): close after the
                                    # next funding payment so the money can be moved. 0 = off: the stop does it
    uneven_wait: bool = True        # flat with the money uneven (rebalance_share): open nothing until it has been
                                    # moved; the bot sees the balances itself and goes on when they are even again
    uneven_remind_min: float = 30.0  # ... and say how much to move again this often, in minutes
    # ---- holding: the owner changes these at any time (`arbitrage set`); the running bot reads them every loop
    min_hold_h: float = 24.0        # keep a position at least this long (a stop still closes it)
    max_hold_h: float = 0.0         # close it after this many hours whatever it pays (0 = no limit)
    exit_edge_apr: float = 0.0      # after min_hold_h: close once the last 24 h AND the next payment pay less than this
    cycle_h: float = 0.0            # > 0: close every this many hours and open again, on the side the funding then
                                    # points to, whatever it pays (no edge floor, no break-even check). It makes
                                    # volume and costs the fills: `arbitrage cyclecost` shows how much. 0 = off
    stop_pct: float = 0.0           # stop and take profit, % from the entry on both legs; 0 = dynamic (the two
                                    # stop_* settings above). A number here overrides it, up to 80% of the way to
                                    # liquidation
    stop_early: float = 0.8         # once the price is this share of the way to the stop the position is closed with
                                    # limit orders (Arcus as maker, no fee); if the stop itself is reached before
                                    # that is done, the rest goes with taker orders at once. 0 = taker orders at
                                    # the stop only. On Arcus's recorded SPY book such a close was filled before
                                    # the stop 91-100% of the time from 0.8, 69-95% from 0.9 (arbitrage/README 2a)
    # ---- execution
    requote_s: float = 3.0          # an order off the best price is moved back to it at most this often
    hedge_taker: bool = True        # a venue whose taker orders are free (Lighter) rests no order of its own: its
                                    # leg follows the other leg's fills with taker orders at once, so the two legs
                                    # are never apart for longer than one loop. False: both legs rest maker orders
    chase_s: float = 20.0           # once one leg has filled, the other follows the price as maker this long ...
    max_cross_bp: float = 5.0       # ... then crosses the spread, if that costs no more than this (half the spread
                                    # plus the taker fee); dearer than that, it keeps following
    enter_timeout_s: float = 600.0  # an entry not done by then is left at what has filled; an exit is finished by
                                    # crossing. Ten minutes: on recorded books an Arcus order at the best price was
                                    # filled within that in 96 to 100% of weekday cases (`arbitrage fills`)
    arcus_taker_bp: float = 2.25    # Arcus's taker fee (Lighter's standard account pays none)
    cross_slip_bp: float = 1.0      # what crossing costs beyond half the spread


@dataclass
class Plan:
    symbol: str
    short_venue: str
    long_venue: str
    edge_h: float                   # the hourly difference the plan counts on (the smallest of next, 24 h, 7 d)
    edge_next_h: float              # the three it is the smallest of, signed: + pays in this plan's direction
    edge_24h: float
    edge_7d: float
    leverage: float
    notional: float                 # per leg, dollars
    size: float                     # base units, the same on both legs
    stop_dist: float                # fraction of the price, the same for the stop and the take profit
    liq_dist: float
    income_day: float
    round_trip_usd: float
    breakeven_h: float
    next_payment_usd: float
    next_payment_at: int
    prices: dict[str, dict[str, float]] = field(default_factory=dict)   # venue -> entry, stop, take, liq
    spreads_bp: dict[str, float] = field(default_factory=dict)          # venue -> bid-ask spread when it was priced
    reasons: list[str] = field(default_factory=list)                    # why it is not a go (empty = go)

    @property
    def go(self) -> bool:
        return not self.reasons

    @property
    def edge_apr(self) -> float:
        return self.edge_h * HOURS_YEAR * 100


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def conservative_edge(next_d: float, d24: float, d7: float) -> float:
    """The signed hourly difference to count on: the smallest of the three when they agree in sign, else 0 (a spread
    that was the other way round yesterday is not a spread to hold)."""
    ds = (next_d, d24, d7)
    if all(d > 0 for d in ds):
        return min(ds)
    if all(d < 0 for d in ds):
        return max(ds)
    return 0.0


def max_leverage(a: Leg, b: Leg, sigma_day: float, s: Settings) -> tuple[float, float, float]:
    """(leverage, stop distance, liquidation distance): the highest leverage both venues allow at which a stop at
    stop_frac of the way to liquidation is still stop_sigmas daily moves from the entry."""
    imf = max(max(leg.imf, leg.imf_off if s.hold_off_hours or leg.off_hours else leg.imf) for leg in (a, b))
    mmf = max(a.mmf, b.mmf)
    lev = min(1.0 / imf if imf > 0 else 1.0, s.max_leverage)
    if sigma_day > 0 and s.stop_sigmas > 0:
        # liquidation distance 1/L - mmf must be >= stop_sigmas * sigma / stop_frac
        need = s.stop_sigmas * sigma_day / s.stop_frac + mmf
        lev = min(lev, 1.0 / need)
    lev = max(1.0, lev)
    liq = max(0.0, 1.0 / lev - mmf)
    stop = s.stop_frac * liq
    if s.stop_pct > 0:              # the owner's own stop: as set, but never past 80% of the way to liquidation
        stop = min(s.stop_pct / 100, 0.8 * liq)
    return lev, stop, liq


def exit_reason(hours_held: float, edge_next_h: float, edge_24h: float, s: Settings) -> str:
    """Why an open position should be closed now ("" = keep it). Both edges are hourly rates signed in the
    position's direction (+ = it is being paid). The same rule runs in the backtest and in the live bot."""
    if s.max_hold_h > 0 and hours_held >= s.max_hold_h:
        return f"held {hours_held:.0f} h: the limit set is {s.max_hold_h:g} h"
    if s.cycle_h > 0:               # the clock alone decides: what it pays is looked at again when it is reopened
        return f"cycle: held {hours_held:.1f} h, closing to open again (cycle_h {s.cycle_h:g})" \
            if hours_held >= s.cycle_h else ""
    if hours_held < s.min_hold_h:
        return ""
    floor = s.exit_edge_apr / 100 / HOURS_YEAR
    if edge_24h < floor and edge_next_h < floor:
        return (f"the difference is gone: last 24 h {edge_24h * HOURS_YEAR * 100:+.1f}% a year, next payment "
                f"{edge_next_h * HOURS_YEAR * 100:+.1f}%")
    return ""


def floor_to(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step if step > 0 else x


def round_trip_bp(s: Settings, spreads_bp: dict[str, float] | None = None) -> float:
    """In and out on both legs, in bp of one leg: per fill the larger of fill_cost_bp and half that venue's spread
    (a maker order that is not filled has to follow the price, and ends up paying about that)."""
    sp = spreads_bp or {}
    return 2 * sum(max(s.fill_cost_bp, sp.get(v, 0.0) / 2) for v in ("arcus", "lighter"))


def plan(arcus: Leg, lighter: Leg, hist_arcus: list[float], hist_lighter: list[float], sigma_day: float,
         collateral: dict[str, float], s: Settings | None = None, spreads_bp: dict[str, float] | None = None,
         short_venue: str | None = None) -> Plan:
    """A sized plan for one market. `hist_*` are each venue's hourly rates, oldest first, the same hours on both;
    `collateral` is the free collateral on each venue in dollars; `spreads_bp` each venue's bid-ask spread now;
    `short_venue` fixes which venue is short (`profunding_side`) instead of the venues' own rates deciding."""
    s = s or Settings()
    trip = round_trip_bp(s, spreads_bp) / 1e4
    diffs = [x - y for x, y in zip(hist_arcus, hist_lighter, strict=False)]          # arcus - lighter
    next_d = arcus.next_rate_h - lighter.next_rate_h
    d24, d7 = mean(diffs[-24:]), mean(diffs[-168:])
    edge = conservative_edge(next_d, d24, d7)
    # short where the rate is higher; with no steady difference, where it is higher right now
    short, long_ = (arcus, lighter) if (edge or next_d) >= 0 else (lighter, arcus)
    if short_venue is not None:
        short, long_ = (arcus, lighter) if short_venue == "arcus" else (lighter, arcus)
        if (edge > 0) != (short is arcus):       # the venues' own history points the other way: nothing to count on
            edge = 0.0
    sign = 1.0 if short is arcus else -1.0            # the three differences below are in the plan's direction
    lev, stop, liq = max_leverage(arcus, lighter, sigma_day, s)
    usable = min(collateral.get("arcus", 0.0), collateral.get("lighter", 0.0)) * s.margin_use
    if s.max_margin_usd > 0:
        usable = min(usable, s.max_margin_usd)
    px = (arcus.mark + lighter.mark) / 2
    step = max(arcus.step, lighter.step)
    budget = usable * lev if s.max_notional_usd <= 0 else min(usable * lev, s.max_notional_usd)
    size = floor_to(budget / px, step) if px > 0 else 0.0
    notional = size * px
    e = abs(edge)
    cost = notional * trip
    p = Plan(symbol=arcus.symbol, short_venue=short.venue, long_venue=long_.venue, edge_h=e,
             edge_next_h=sign * next_d, edge_24h=sign * d24, edge_7d=sign * d7, leverage=lev, notional=notional,
             size=size, stop_dist=stop, liq_dist=liq, income_day=notional * e * 24, round_trip_usd=cost,
             breakeven_h=trip / e if e > 0 else math.inf,
             next_payment_usd=notional * sign * next_d, next_payment_at=max(arcus.next_at, lighter.next_at))
    for leg, side in ((long_, 1.0), (short, -1.0)):          # side: +1 the leg gains when the price rises
        p.prices[leg.venue] = {"side": side, "entry": leg.mark, "stop": leg.mark * (1 - side * stop),
                               "take": leg.mark * (1 + side * stop), "liq": leg.mark * (1 - side * liq)}
    r = p.reasons
    if s.rwa_only and arcus.category.upper() in CRYPTO_CLASSES:
        r.append("a crypto market: the arbitrage trades stocks, indices and commodities only (rwa_only)")
    if not (arcus.online and lighter.online):
        r.append("a market is not open for trading")
    if len(diffs) < 24:
        r.append(f"only {len(diffs)} h of funding history on both venues (24 needed)")
    if s.cycle_h > 0:               # cycling: opened on the side the next payment points to, whatever it pays
        if next_d == 0.0 and edge == 0.0:
            r.append("both venues pay the same: no side to take")
    elif edge == 0.0:
        r.append("the difference changed sides: next payment, last 24 h and last 7 days do not agree")
    elif p.edge_apr < s.min_edge_apr:
        r.append(f"pays {p.edge_apr:.1f}% a year on the position, under the {s.min_edge_apr:g}% floor")
    if s.cycle_h <= 0 and e > 0 and p.breakeven_h > s.max_breakeven_h:
        r.append(f"needs {p.breakeven_h:.0f} h of funding to pay for getting in and out "
                 f"(limit {s.max_breakeven_h:g} h)")
    if min(arcus.volume_24h, lighter.volume_24h) < s.min_volume_24h:
        r.append(f"thin: ${min(arcus.volume_24h, lighter.volume_24h):,.0f} traded in 24 h on one venue")
    need = max(arcus.min_notional, lighter.min_notional, arcus.min_size * px, lighter.min_size * px)
    if usable <= 0:
        r.append("no collateral on " + " and ".join(v for v in ("arcus", "lighter") if collateral.get(v, 0.0) <= 0))
    elif notional < need:
        r.append(f"position ${notional:,.0f} is under the venues' minimum order (${need:,.0f})")
    if sigma_day <= 0:
        r.append("no price history for the stop distance")
    return p
