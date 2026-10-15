"""The overnight and swing backtest (swing.py), the third list's rules (swing_rules.py) and the monthly rotation
(rotation.py) on made-up prices whose every number is known."""

import numpy as np

from daytrade import bars as B
from daytrade import rotation as Rot
from daytrade import swing as S
from daytrade import swing_rules as R

V = S.Venue("test", 50, 0.012, 1.0, 2.0, 0.0, "none")
D0 = 19_730                                   # 2024-01-08, a Monday


def daily(c, h=None, lo=None) -> S.Coarse:
    """One bar a weekday: opens at the previous close, 09:30 to 16:00 New York."""
    c = np.asarray(c, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) if h is None else np.asarray(h, dtype=float)
    lo = np.minimum(o, c) if lo is None else np.asarray(lo, dtype=float)
    days = []
    d = D0
    while len(days) < len(c):
        if B.weekday(d) < 5:
            days.append(d)
        d += 1
    day = np.array(days, dtype=np.int64)
    t_o = np.array([B.local_to_utc(int(x), 9 * 3600 + 1800, B.NY) for x in day], dtype=np.int64)
    t_c = np.array([B.local_to_utc(int(x), 16 * 3600, B.NY) for x in day], dtype=np.int64)
    fine = B.Bars(t_o, o, h, lo, c, np.zeros(len(c)))
    idx = np.arange(len(c), dtype=np.int64)
    return S.Coarse("test", t_o, t_c, o, h, lo, c, day, idx, idx + 1, fine, B.NY)


class NoFunding(S.Funding):
    def __init__(self, per_hour: float = 0.0):
        self.mean, self.h0, self.h1, self.cum = per_hour, 0, 0, np.zeros(1)


def test_a_scaled_in_trade_counts_its_share_once():
    t = S.STrade(1, 0, 3600, 100.0, 103.0, 1 / 3, 1, 0.01, 0.0, 0.0, 0.0, float("nan"), "rule")
    a = S.equity([t], V, lev=1)
    assert abs(a.final - 1010.0) < 1e-9          # net is already a share of the full size: 1% of $1,000


def test_overnight_buys_the_close_sells_the_open_and_pays_the_night_funding():
    cb = daily([100.0, 101.0, 102.0, 103.0])
    tr = S.simulate(R.Overnight("SPY"), cb, V, NoFunding(1e-5))
    assert len(tr) == 3 and tr[0].entry == 100.0 and tr[0].exit == 100.0      # the next open is the close
    assert tr[0].reason == "open" and abs(tr[0].cost - 2e-4) < 1e-12           # 1 bp a side
    assert abs(tr[0].fund - 17 * 1e-5) < 1e-12        # 16:00 to 09:30 the next day: 17 hour marks paid


def test_percent_b_waits_for_three_closes_under_its_band_and_leaves_over_0_8():
    up = list(np.linspace(100, 130, 220))                        # a rising year: above the 200-day average
    dip = [129.0, 127.5, 126.5, 125.8]                            # %b(5) under 0.2 at the last three of these
    back = [128.0, 131.0, 133.0]
    cb = daily(up + dip + back)
    tr = S.simulate(R.PercentB("SPY"), cb, V, NoFunding())
    assert len(tr) == 1
    b = R.pct_b(cb.c, 5, 2.0)
    first = next(k for k in range(200, len(b)) if (b[k - 2:k + 1] < 0.2).all())
    k_in = int(np.searchsorted(cb.t_c, tr[0].t_in))
    assert k_in == first == 222 and tr[0].entry == cb.c[first] == 126.5     # the third close running under 0.2
    k_out = int(np.searchsorted(cb.t_c, tr[0].t_out))
    assert b[k_out] > 0.8 and (b[k_in:k_out] <= 0.8).all()


def test_the_rsi_rules_read_only_closes_up_to_the_bar():
    c = list(np.linspace(100, 140, 230)) + [139.0, 137.0, 135.0, 134.0, 136.0, 138.0, 141.0]
    cb = daily(c)
    for rule in (R.CumRsi("SPY"), R.RsiLevel("SPY", 2, 15, exit="high"), R.Rsi2("SPY")):
        full = S.simulate(rule, cb, V, NoFunding())
        assert full, rule.name
        k = int(np.searchsorted(cb.t_c, full[0].t_in))
        cut = daily(c[:k + 2])                     # the future after the next bar taken away: the same entry
        again = S.simulate(rule, cut, V, NoFunding())
        assert again and again[0].t_in == full[0].t_in and again[0].entry == full[0].entry, rule.name


def test_ema_rsi_needs_the_rsi_to_have_been_oversold():
    fall = list(np.linspace(120, 90, 40))                         # RSI(14) far under 30
    rise = list(np.linspace(91, 110, 25))
    cb = daily(fall + rise)
    tr = S.simulate(R.EmaRsiRecover("SPY", within=20), cb, V, NoFunding())
    assert len(tr) == 1 and tr[0].side == 1
    r = R.rsi(cb.c, 14)
    k = int(np.searchsorted(cb.t_c, tr[0].t_in))
    assert np.nanmin(r[k - 19:k + 1]) < 30 <= r[k]
    assert not S.simulate(R.EmaRsiRecover("SPY", within=2), cb, V, NoFunding())    # recovered before the cross


def hours(n_days: int, drift_hour: int | None = None, drift: float = 0.001, start_day: int = D0) -> S.Coarse:
    """Hourly gold bars, 23 an hour of gold's day (23:00 to 22:00 London), weekdays only: flat, except that the hour
    `drift_hour` of gold's day rises `drift` every day."""
    rows, px = [], 2000.0
    d = start_day
    made = 0
    while made < n_days:
        if B.weekday(d) < 5:
            for g in range(23):
                t = B.local_to_utc(d, 0, B.LONDON) - 3600 + g * 3600     # 23:00 London the evening before
                o = px
                if g == drift_hour:
                    px *= 1 + drift
                rows.append((t, o, max(o, px), min(o, px), px, 1.0))
            made += 1
        d += 1
    return S.hourly(B.from_rows(rows), "test")


def test_a_fixed_session_window_is_in_at_its_first_hour_and_out_after_its_last():
    cb = hours(3)
    tr = S.simulate(R.SessionWindow("london", 1), cb, V, NoFunding())
    assert len(tr) == 3
    for x in tr:
        loc_in = B.local(np.array([x.t_in]), B.LONDON)[0] % B.DAY
        loc_out = B.local(np.array([x.t_out]), B.LONDON)[0] % B.DAY
        assert loc_in == 7 * 3600 and loc_out == 12 * 3600 and x.reason == "window"


def test_the_monthly_window_is_chosen_from_earlier_months_only():
    cb = hours(130, drift_hour=13, drift=0.0005)                  # 12:00-13:00 London rises every day
    rule = R.SessionWindow("wf", months=2, max_len=4, t_min=2.0)
    tr = S.simulate(rule, cb, V, NoFunding())
    assert tr and all(x.side == 1 for x in tr)
    first = min(x.t_in for x in tr)
    assert B.day_str(int(first) // B.DAY) >= "2024-03-01"         # two whole months seen first
    loc = B.local(np.array([tr[-1].t_in]), B.LONDON)[0] % B.DAY
    assert loc <= 12 * 3600 < (B.local(np.array([tr[-1].t_out]), B.LONDON)[0] % B.DAY)   # the window holds 12:00
    assert all(x.gross > 0 for x in tr)


def test_rotation_holds_last_months_best_and_pays_only_when_it_switches(monkeypatch):
    monkeypatch.setattr(Rot, "START", "2024-01-01")              # the two-year window, moved onto these months
    days = np.arange(np.datetime64("2024-01-01"), np.datetime64("2024-06-01"))
    days = days[np.is_busday(days)]
    m = days.astype("datetime64[M]").astype(int) - days.astype("datetime64[M]").astype(int)[0]
    a = 100 * np.cumprod(np.where(m == 1, 1.002, 1.0))            # A wins February only
    b = 100 * np.cumprod(np.where(m >= 2, 1.003, 1.0))            # B wins from March
    c = np.vstack([a, b])
    s = Rot.Series(days, c, c.copy(), c.copy())
    monkeypatch.setitem(Rot.ETF_COST_BP, "A", 1.0)
    monkeypatch.setitem(Rot.ETF_COST_BP, "B", 1.0)
    r = Rot.simulate(Rot.Version(("A", "B")), s, ("A", "B"))
    assert [h[1] for h in r.holds] == ["A", "B", "B"]             # ranked at the ends of Feb, Mar and Apr
    assert r.switches == 2                                        # into A, then into B; staying in B is free
    assert abs(r.holds[0][2] + 1e-4) < 1e-12                      # A is flat in March: only its 1 bp to buy
    assert abs(r.holds[1][2] - ((1 - 2e-4) * b[m == 3][-1] / b[m == 2][-1] - 1)) < 1e-12   # out of A, into B
    assert abs(r.holds[2][2] - (b[m == 4][-1] / b[m == 3][-1] - 1)) < 1e-12
