"""backtest.py on made-up markets: what fills cost, which way the funding pays, the guard against liquidation, an
off-hours open, the transfers and the skip dates."""

import datetime as dt

import numpy as np

from deltaneutral import backtest as BT
from deltaneutral import plan as P
from deltaneutral.costs import Costs
from deltaneutral.plan import ARCUS, LIGHTER, Pair
from deltaneutral.series import FIVE, Market, blackout, next_open

RULES = {ARCUS: P.Rules(imf=0.1, imf_off=0.15, mmf=0.0667, taker_bp=2.25),
         LIGHTER: P.Rules(imf=0.1, imf_off=0.1, mmf=0.06)}
MON = int(dt.datetime(2026, 10, 5, 4, 0, tzinfo=dt.UTC).timestamp())     # Monday 00:00 New York
FREE = Costs({v: {s: {k: (0.0, 0.0) for k in ("rth", "ext", "off")} for s in ("X", "Y")} for v in (ARCUS, LIGHTER)})


def market(sym: str, t: np.ndarray, px: np.ndarray | float, fund_a: float = 0.0, fund_l: float = 0.0) -> Market:
    p = np.full(len(t), float(px)) if np.isscalar(px) else np.asarray(px, dtype=float)
    hours = (t % 3600 == 0).astype(float)
    return Market(sym, t, {ARCUS: p.copy(), LIGHTER: p.copy()},
                  {ARCUS: hours * fund_a, LIGHTER: hours * fund_l}, RULES)


def clock(days: int) -> np.ndarray:
    return np.arange(MON, MON + days * 86_400, FIVE, dtype=np.int64)


def test_flat_prices_cost_only_the_fills_and_every_day_is_a_cycle():
    t = clock(6)
    st = BT.Settings(arcus_maker=False, weekends=True)
    costs = Costs({v: {s: {k: (1.0, 1.0) for k in ("rth", "ext", "off")} for s in ("X", "Y")}
                   for v in (ARCUS, LIGHTER)})
    r = BT.run(Pair("X", "Y"), market("X", t, 100), market("Y", t, 50), costs, st, int(t[0]), int(t[-1]))
    assert len(r.cycles) == 6 and r.liquidations == 0 and r.guards == 0       # Monday to Saturday's 10:00 opens
    assert [c.end for c in r.cycles] == ["time"] * 5 + ["end of data"]
    parts = r.parts()
    assert abs(parts["price"]) < 1e-9 and abs(parts["funding"]) < 1e-9
    assert abs(r.pnl - parts["costs"]) < 1e-9
    # Arcus's fills cost 1 + 2.25 bp, Lighter's 1 bp: the first cycle's $450 a market, both markets, in and out
    first = r.cycles[0]
    assert abs(first.costs[ARCUS] + 450 * 2 * 2 * 3.25e-4) < 0.01 and abs(first.costs[LIGHTER] + 450 * 4e-4) < 0.01
    assert first.transfer > 0 and first.transfer_from == LIGHTER        # Arcus paid more: Lighter sends half the gap


def test_the_way_round_follows_the_funding():
    t = clock(4)
    # Arcus charges X's longs 1 bp an hour, Lighter nothing: X short on Arcus earns it
    mx, my = market("X", t, 100, fund_a=1e-4), market("Y", t, 50)
    r = BT.run(Pair("X", "Y"), mx, my, FREE, BT.Settings(), int(t[0]), int(t[-1]))
    assert all(c.way == -1 for c in r.cycles[1:])          # the first open has no 24 h behind it to read
    assert r.parts()["funding"] > 0


def test_the_guard_closes_before_the_losing_venue_is_liquidated():
    t = clock(3)
    x = np.full(len(t), 100.0)
    k = int((MON + 86_400 + 3 * 3600 - t[0]) // FIVE)     # a day in, X runs up 12% over two hours
    x[k:k + 24] = np.linspace(100, 112, 24)
    x[k + 24:] = 112
    st = BT.Settings(way="-1", arcus_maker=False)          # Arcus short X: Arcus loses
    guarded = BT.run(Pair("X", "Y"), market("X", t, x), market("Y", t, 50), FREE, st, int(t[0]), int(t[-1]))
    assert guarded.guards == 1 and guarded.liquidations == 0
    bare = BT.run(Pair("X", "Y"), market("X", t, x), market("Y", t, 50), FREE, BT.Settings(way="-1", guard=0),
                  int(t[0]), int(t[-1]))
    assert bare.liquidations == 1 and bare.total < guarded.total      # the venue's 1% fee
    assert any(c.end == "liquidated arcus" for c in bare.cycles)


def test_an_off_hours_open_is_sized_by_arcus_off_hours_margin():
    t = clock(2)
    st = BT.Settings(open_ny=22.0)                           # 22:00 New York: Arcus's stock markets are closed
    r = BT.run(Pair("X", "Y"), market("X", t, 100), market("Y", t, 50), FREE, st, int(t[0]), int(t[-1]))
    assert r.cycles[0].off_hours and abs(r.cycles[0].nx - 300) < 1e-6      # 90 / (15% + 15%)


def test_skip_dates_hold_nothing_from_the_close_before_to_the_close_after():
    a, b = blackout("2026-10-07")                            # a Wednesday report
    assert dt.datetime.fromtimestamp(a, dt.UTC).strftime("%d %H") == "06 20"   # 16:00 New York the day before
    assert dt.datetime.fromtimestamp(b, dt.UTC).strftime("%d %H") == "08 20"
    t = clock(5)
    st = BT.Settings(skip=("2026-10-07",))
    r = BT.run(Pair("X", "Y"), market("X", t, 100), market("Y", t, 50), FREE, st, int(t[0]), int(t[-1]))
    for c in r.cycles:                                       # the cycle that would run into it never opens
        assert c.closed <= a or c.opened >= b
    opened = [dt.datetime.fromtimestamp(c.opened, dt.UTC).strftime("%a") for c in r.cycles]
    assert opened == ["Mon", "Fri"] and r.cycles[1].opened == next_open(b, 10.0)
