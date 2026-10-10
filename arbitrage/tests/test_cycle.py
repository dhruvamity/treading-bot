"""`arbitrage cycle`, `arbitrage basis` and `arbitrage fills` on made-up prices and order books (no network)."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from arbitrage import backtest as bt
from arbitrage import basis, cycle, fills, history
from arbitrage.cycle import NEW_YORK, Rules
from arbitrage.rank import Settings, max_leverage
from tests.test_study import series


def ny(day: int, hour: int, minute: int = 0) -> int:
    return int(dt.datetime(2026, 10, day, hour, minute, tzinfo=NEW_YORK).timestamp())


def test_the_week_has_three_parts_and_arcus_allows_less_leverage_in_two_of_them() -> None:
    assert cycle.open_hours(ny(9, 19, 59)) and not cycle.open_hours(ny(9, 20))       # Friday 9 Oct 2026, 20:00
    assert cycle.weekend(ny(9, 20)) and cycle.weekend(ny(10, 12)) and cycle.weekend(ny(12, 3, 59))
    assert cycle.open_hours(ny(12, 4)) and not cycle.weekend(ny(12, 4))              # Monday 04:00: open again
    assert not cycle.open_hours(ny(13, 2)) and not cycle.weekend(ny(13, 2))          # a Tuesday night
    s = series("SPY", "INDICES")                         # the fixture's margins: 10% open, 15% closed on Arcus
    assert cycle.top_leverage(s, ny(12, 10)) == pytest.approx(10.0)
    assert cycle.top_leverage(s, ny(10, 10)) == pytest.approx(1 / 0.15)
    d = cycle.distances(s, 0.9)
    assert d["open"]["arcus"] == pytest.approx((1 / 9 - 0.066667) * 100) and d["closed"]["leverage"] < 7


def test_a_cycle_costs_its_fills_and_collects_the_funding_in_between() -> None:
    s = series("SPY", "INDICES", arcus=2e-5, lighter=4e-6, hours=240)        # flat prices: nothing reaches a stop
    held = cycle.run(s, 240.0, Rules(every_h=0, leverage=5, round_trip_bp=1.0))
    n = 0.9 * 5 * 120.0
    assert held["cycles"] == 1 and held["stops"] == 0 and held["transfers"] == 0
    assert held["oi"] == pytest.approx(n, rel=0.01) and held["volume"] == pytest.approx(2 * n)
    assert held["funding"] == pytest.approx(1.6e-5 * n * 238, rel=1e-6)      # every payment after the opening hour
    assert held["cycle_cost"] == pytest.approx(n * 0.5e-4)                   # one way in: half a cycle
    three = cycle.run(s, 240.0, Rules(every_h=3, leverage=5, round_trip_bp=1.0))
    assert three["cycles"] == 80 and three["volume"] == pytest.approx(4 * n * 80 - 2 * n, rel=1e-6)
    assert three["cycle_cost"] == pytest.approx(n * 1e-4 * 80 - n * 0.5e-4, rel=1e-6)
    assert three["funding"] == pytest.approx(held["funding"], rel=1e-6)      # the same hours held
    assert three["net"] < held["net"] and three["per_million"] > 0
    free = cycle.run(s, 240.0, Rules(every_h=3, leverage=5, round_trip_bp=0.0))
    assert free["net"] == pytest.approx(free["funding"]) and free["per_million"] < 0      # then it only collects
    other = series("SPY", "INDICES", arcus=1e-6, lighter=4e-6, hours=240)
    flipped = cycle.run(other, 240.0, Rules(every_h=3, leverage=5))
    assert flipped["funding"] > 0                        # Lighter pays more: it is short there instead
    assert cycle.run(series("SPY", "INDICES", arcus=1e-6, lighter=4e-6, hours=240), 240.0,
                     Rules(every_h=3, leverage=5, flip=False))["funding"] < 0


def test_a_move_past_the_stop_closes_it_and_the_money_has_to_be_moved() -> None:
    s = series("SPY", "INDICES", hours=240)
    for i, t in enumerate(s.hours):
        if i >= 100:                                     # the price is 3% higher from hour 100 on
            s.px["arcus"][t] = s.px["lighter"][t] = (103.5, 102.5, 103.0)
    # the fixture starts on a Monday morning in New York: opened at the venues' highest while open, 10x
    r = cycle.run(s, 240.0, Rules(every_h=0, leverage=0, stop_bp=3.0, transfer_h=2.0, rebalance_share=0.45))
    assert r["stops"] == 1 and r["transfers"] == 1 and r["waiting_h"] >= 2      # 1.7% of $1,080 changed venues
    lev, stop, _ = max_leverage(s.legs["arcus"], s.legs["lighter"], 0.0, Settings())
    assert cycle.stop_distance(s, lev) == pytest.approx(stop)                # the stop is the live bot's own
    assert r["stop_cost"] == pytest.approx(0.9 * 10 * 120 * 3e-4, rel=1e-6)
    assert r["liq_reached"] == 0                         # 3% is past the stop (1.7%) and short of liquidation (4.4%)
    far = series("SPY", "INDICES", hours=240)
    far.px["lighter"][far.hours[100]] = (120.0, 99.5, 100.0)                 # one hour's high is 20% up on Lighter,
    assert cycle.run(far, 240.0, Rules(every_h=0))["liq_reached"] == 0       # where the leg is long: a gain
    far.px["arcus"][far.hours[100]] = (120.0, 99.5, 100.0)                   # and on Arcus, where it is short
    assert cycle.run(far, 240.0, Rules(every_h=0))["liq_reached"] == 1
    broken = series("SPY", "INDICES", hours=240)
    broken.px["arcus"][broken.hours[100]] = (100.5, 60.0, 61.0)              # a broken feed, not a market
    assert cycle.bar(broken, "arcus", broken.hours[100]) is None
    assert cycle.run(broken, 240.0, Rules(every_h=0))["stops"] == 0
    text = cycle.report({"SPY": s}, 240.0, {"SPY": 1.0})
    for part in ("highest leverage 10x while open, 6.7x while closed", "every 3 h", "the venues' highest",
                 "0.25 bp a cycle", "$ per $1M"):
        assert part in text


def test_the_gap_between_the_venues_is_read_by_the_minute_and_over_each_weekend(tmp_path: Path) -> None:
    head = ["ts", "open", "high", "low", "close", "volume"]
    start = ny(9, 12)                                    # Friday noon to Monday noon
    a_rows, b_rows = [], []
    for k in range(72 * 60):
        t = start + k * 60
        gap = -0.10 if cycle.weekend(t) else -0.05       # Arcus 10 bp under Lighter at the weekend, 5 otherwise
        b_rows.append((t, 100, 100, 100, 100.0, 50.0 if k % 7 else 0.0))    # a minute with no volume: no trade
        a_rows.append((t, 1, 1, 1, 100.0 + gap, 50.0, 3))
    history.write(tmp_path / "lighter_SPY_px1m.csv", head, b_rows)
    history.write(tmp_path / "arcus_SPY_perp1m.csv", [*head, "trades"], a_rows)
    b = basis.minutes(tmp_path / "lighter_SPY_px1m.csv")
    assert len(b) == len(b_rows) - len([k for k in range(72 * 60) if k % 7 == 0])
    g = basis.gaps(basis.minutes(tmp_path / "arcus_SPY_perp1m.csv"), b)
    rows = {r["when"]: r for r in basis.by_part(g)}
    assert rows["weekend"]["mean"] == pytest.approx(-10.0, abs=0.01)
    assert rows["stock market open"]["mean"] == pytest.approx(-5.0, abs=0.01)
    (wk,) = basis.weekends(basis.minutes(tmp_path / "arcus_SPY_perp1m.csv"), b)
    assert wk["friday"] == "2026-10-09" and wk["before"] == pytest.approx(-5.0, abs=0.01)
    assert wk["median"] == pytest.approx(-10.0, abs=0.01) and wk["cash_open"] == pytest.approx(-5.0, abs=0.01)
    ch = {r["hours"]: r for r in basis.changes(g)}
    assert ch[1]["median"] == 0.0 and ch[24]["p99"] == pytest.approx(5.0, abs=0.01)     # across the weekend's edge
    text = basis.report(tmp_path, ["SPY", "QQQ"])
    assert "Each weekend" in text and "2026-10-09" in text and "QQQ: no minute files" in text


def book(root: Path, day: str, t0: int, bbo: list[tuple[float, float, float, float, float]],
         trades: list[tuple[float, float, float, bool]]) -> fills.Book:
    d = root / day
    d.mkdir(parents=True)
    us = fills.US
    ts = np.array([t0 + int(x[0] * us) for x in bbo])
    bid, ask = np.array([x[1] for x in bbo]), np.array([x[2] for x in bbo])
    np.savez(d / "bbo-1.npz", ts=ts, bid=bid, ask=ask, bid_sz=np.array([x[3] for x in bbo]),
             ask_sz=np.array([x[4] for x in bbo]))
    lv = np.arange(10) * 0.01
    np.savez(d / "depth-1.npz", ts=ts, bp=bid[:, None] - lv, bs=np.full((len(ts), 10), 5.0),
             ap=ask[:, None] + lv, as_=np.full((len(ts), 10), 5.0))
    np.savez(d / "trades-1.npz", ts=np.array([t0 + int(x[0] * us) for x in trades]),
             px=np.array([x[1] for x in trades]), sz=np.array([x[2] for x in trades]),
             buy=np.array([x[3] for x in trades]), tid=np.arange(len(trades)))
    return fills.Book(root, [day])


def test_a_limit_order_waits_its_turn_in_the_queue_and_a_taker_order_eats_the_depth(tmp_path: Path) -> None:
    t0 = ny(13, 11) * fills.US                           # a Tuesday, in the cash session
    quotes = [(s, 100.00, 100.02, 10.0, 10.0) for s in range(0, 40)]
    b = book(tmp_path / "a", "2026-10-13", t0, quotes,
             [(5, 100.00, 4.0, False), (6, 100.02, 9.0, True), (8, 100.00, 8.0, False), (20, 99.99, 1.0, False)])
    assert fills.part(t0) == "cash session 09:30-16:00" and fills.part(ny(10, 11) * fills.US) == "weekend"
    got, left = b.passive(t0 + 1, True, 3.0, 30)         # a bid for 3 behind the 10 already there
    # 4 sold at our price: still 6 ahead. 8 more: 2 of them reach us. Then a trade below our price: the rest
    assert [(round((t - t0) / fills.US), px, round(u, 6)) for t, px, u in got] == [(8, 100.0, 2.0), (20, 100.0, 1.0)]
    assert left == 0.0
    got, left = b.passive(t0 + 1, False, 3.0, 30)        # an offer for 3: only 9 bought at our price, 10 were ahead
    assert got == [] and left == 3.0
    assert b.sweep(t0 + 1, True, 12.0) == pytest.approx((5 * 100.02 + 5 * 100.03 + 2 * 100.04) / 12)
    assert b.sweep(t0 - 60 * fills.US, True, 1.0) is None and b.mid(t0 + 1) == pytest.approx(100.01)

    moved = ([(s, 100.00, 100.02, 10.0, 10.0) for s in range(0, 10)]
             + [(s, 100.05, 100.07, 2.0, 2.0) for s in range(10, 40)])
    m = book(tmp_path / "m", "2026-10-13", t0, moved, [(30, 100.05, 5.0, False)])
    got, left = m.passive(t0 + 1, True, 1.0, 35)         # the best bid left ours behind: moved up after 3 s
    assert [(px, u) for _, px, u in got] == [(100.05, 1.0)] and left == 0.0


def test_a_close_started_near_the_stop_is_done_before_it_on_the_leg_that_gains(tmp_path: Path) -> None:
    t0 = ny(13, 11) * fills.US                           # the price climbs a cent a second, buyers lifting the offer
    quotes = [(s, 100.00 + s * 0.01, 100.02 + s * 0.01, 10.0, 10.0) for s in range(0, 200)]
    trades = [(s + 0.5, 100.02 + s * 0.01, 50.0, True) for s in range(0, 200)]
    b = book(tmp_path / "up", "2026-10-13", t0, quotes, trades)
    rows = {(r["share"], r["leg"]): r for r in fills.near_stop(b, 500.0, (0.01, 0.02), hold_s=150, step_s=1000)}
    win, lose = rows[(0.8, "gains")], rows[(0.8, "loses")]
    assert win["entries"] == 1 and win["stopped"] == 100 and win["closes"] == 1 and win["stop_followed"] == 100
    assert win["done"] == 100 and win["median_wait"] <= 2      # a long sells into the buyers at once
    assert lose["done"] == 0 and lose["filled"] == 0           # a short's bid is left behind: the stop is a taker order


def test_replace_keeps_the_fixture_untouched() -> None:
    s = series("SPY", "INDICES")
    assert replace(s, hours=s.hours[:10]).hours != s.hours and isinstance(s, bt.Series)
