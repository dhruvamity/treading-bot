"""The second list's intraday rules (intraday2.py) on made-up days whose every number is known."""

import math

import numpy as np

from daytrade import bars as B
from daytrade import intraday2 as I
from daytrade.core import MARKETS
from daytrade.sim import run
from daytrade.strategies import RTH_OPEN

D0 = 19_730                                  # a local day number (2024-01-08, a Monday)


def minutes(closes, start=100.0, high=None, low=None):
    """390 one-minute bars: each opens at the previous close; `high`/`low` widen one minute each."""
    o = np.r_[start, closes[:-1]]
    h, lo = np.maximum(o, closes), np.minimum(o, closes)
    if high is not None:
        h[100] = high
    if low is not None:
        lo[200] = low
    return o, h, lo, np.asarray(closes, dtype=float)


def weekdays(n):
    """The first n weekdays from D0 (a weekend is no session)."""
    out, d = [], D0
    while len(out) < n:
        if B.weekday(d) < 5:
            out.append(d)
        d += 1
    return out


def bars_of(days):
    rows = []
    for d, (o, h, lo, c) in zip(weekdays(len(days)), days, strict=True):
        for k in range(390):
            rows.append((B.local_to_utc(d, RTH_OPEN + 60 * k, B.NY), o[k], h[k], lo[k], c[k], 1000.0))
    return B.from_rows(rows)


def quiet_days(n, close=100.0):
    """Days that open and close at `close` and trade 0.5 either side once: a true range of exactly 1.0."""
    return [minutes(np.full(390, close), close, close + 0.5, close - 0.5) for _ in range(n)]


def test_session_numbers_come_from_earlier_days_only():
    days = I.session_days(bars_of(quiet_days(16)), 5)
    assert len(days) == 15                                   # the first day has no previous close
    assert math.isnan(days[12].ctx["atr14"]) and days[14].ctx["atr14"] == 1.0 and days[14].ctx["open"] == 100.0
    assert days[14].ctx["prev_ret"] == 0.0
    before = I.session_days(bars_of([*quiet_days(16), minutes(np.full(390, 140.0))]), 5)
    assert before[14].ctx["atr14"] == days[14].ctx["atr14"]   # a wild later day changes nothing before it
    assert np.allclose(before[-1].ctx["sigma"], 0.0)          # the quiet days moved nothing away from their opens


def test_atr_breakout_goes_with_the_break_and_holds_to_the_close():
    up = np.r_[np.linspace(100.0, 101.2, 30), np.full(360, 101.5)]          # through 100 + 1 ATR by 10:00
    days = quiet_days(16) + [minutes(up)]
    s = I.AtrBreakout("QQQ")
    tr = run(s, I.session_days(bars_of(days), 5), MARKETS["QQQ"].venues["lighter"], 0.01)
    assert len(tr) == 1
    t = tr[0]
    assert t.side == 1 and t.reason == "time" and t.stop0 == 101.0 - 0.25 and t.exit == 101.5
    assert B.local(np.array([t.t_entry]), B.NY)[0] % B.DAY == RTH_OPEN + 30 * 60   # its 5-minute bar closed at 10:00


def test_vwap_rebound_buys_a_turn_under_vwap_after_a_down_day_and_sells_at_vwap():
    down_day = minutes(np.full(390, 99.5), 100.0)                            # the day before closed 0.5% lower
    fall = np.linspace(100.0, 98.9, 65)                                      # 09:30-10:35, every 5 minutes lower
    turn = np.linspace(98.92, 99.05, 5)                                      # 10:35-10:40: closes above 10:35's
    rise = np.linspace(99.1, 99.8, 50)
    today = minutes(np.r_[fall, turn, rise, np.full(390 - 120, 99.8)], 99.5)
    days = [*quiet_days(15), down_day, today]
    s = I.VwapRebound("QQQ")
    tr = run(s, I.session_days(bars_of(days), 5), MARKETS["QQQ"].venues["lighter"], 0.01)
    assert len(tr) == 1
    t = tr[0]
    assert t.side == 1 and t.reason == "target" and 99.0 < t.entry < t.target == t.exit < 99.8
    assert I.bp(t, gross=True) > 0
    assert not run(I.VwapRebound("QQQ", weak="down1"), I.session_days(bars_of(days), 5),
                   MARKETS["QQQ"].venues["lighter"], 0.01)                   # it fell 0.5%, not 1%


def test_noise_breakout_looks_only_every_30_minutes_and_leaves_under_vwap():
    calm = [minutes(np.full(390, 100.1), 100.0) for _ in range(16)]          # 0.1% from the open all day
    path = np.r_[np.full(10, 100.5), np.full(50, 100.6), np.full(60, 100.2), np.full(270, 100.2)]
    days = [*calm, minutes(path, 100.1)]
    tr = run(I.NoiseBreakout("QQQ"), I.session_days(bars_of(days), 1), MARKETS["QQQ"].venues["lighter"], 0.01)
    assert len(tr) == 1
    t = tr[0]
    entry_sod = B.local(np.array([t.t_entry]), B.NY)[0] % B.DAY
    assert t.side == 1 and entry_sod == RTH_OPEN + 30 * 60                   # not at 09:40, when it first broke out
    assert t.reason == "trail" and t.exit == 100.2                           # 11:00: back under VWAP


def test_the_main_versions_are_the_first_of_their_lists():
    assert I.is_main(I.vwap_versions("QQQ")[0].name)
    assert [I.is_main(s.name) for s in I.breakout_versions("QQQ")[:3]] == [True, True, False]
    names = [s.name for s in I.vwap_versions("QQQ") + I.breakout_versions("QQQ")]
    assert len(names) == len(set(names))


def test_results_in_basis_points_and_dollars():
    net = np.array([10.0, -5.0])
    usd, worst = I.grow(net, 5)
    assert math.isclose(usd, 1000 * 1.005 * 0.9975) and math.isclose(worst, 0.25)
