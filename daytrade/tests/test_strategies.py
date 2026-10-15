import numpy as np

from daytrade import bars as B
from daytrade.core import MARKETS
from daytrade.sim import run
from daytrade.strategies import EmaPullback, LondonSweep, OpeningRange, structure_trend

DAY1, DAY2 = 20374, 20375            # Tue 13 and Wed 14 Oct 2025 (days since 1970)


def make(rows: list[tuple[int, float, float, float, float]]) -> B.Bars:
    return B.from_rows([(t, o, h, lo, c, 100.0) for t, o, h, lo, c in rows])


def session(day: int, tz, start: str, end: str, price: float, special: dict[str, tuple] | None = None):
    """Flat one-minute bars from `start` to `end` local time, with some minutes replaced (o, h, l, c)."""
    special = special or {}
    h0, m0 = map(int, start.split(":"))
    h1, m1 = map(int, end.split(":"))
    out = []
    for mins in range(h0 * 60 + m0, h1 * 60 + m1):
        t = B.local_to_utc(day, mins * 60, tz)
        key = f"{mins // 60:02d}:{mins % 60:02d}"
        o, h, lo, c = special.get(key, (price, price, price, price))
        out.append((t, o, h, lo, c))
    return out


def spy_two_days() -> B.Bars:
    d1 = session(DAY1, B.NY, "09:30", "16:00", 100.0, {"10:00": (100, 100.5, 99.5, 100)})
    sp = {f"09:{m:02d}": (101.2, 101.4, 101.0, 101.2) for m in range(30, 45)}
    sp.update({f"09:{m:02d}": (101.5, 101.6, 101.45, 101.6) for m in range(45, 50)})      # breaks out above 101.4
    sp.update({"09:50": (101.6, 101.6, 101.38, 101.45)})                                   # back to the ORB high
    sp.update({f"09:{m:02d}": (101.5, 101.5, 101.45, 101.5) for m in range(51, 55)})      # ... and holds
    sp.update({f"09:{m:02d}": (101.5, 101.5, 101.5, 101.5) for m in range(55, 60)})
    sp.update({"10:05": (101.5, 101.8, 101.5, 101.8)})                                     # target 101.76
    d2 = session(DAY2, B.NY, "09:30", "16:00", 101.5, sp)
    return make(d1 + d2)


def test_opening_range_retest_long():
    s = OpeningRange("SPY", tf=5)
    days = s.days(spy_two_days())
    assert len(days) == 1 and days[0].ctx["pdh"] == 100.5 and days[0].ctx["pdl"] == 99.5
    trades = run(s, days, MARKETS["SPY"].venues["lighter"], 0.01)
    assert len(trades) == 1
    tr = trades[0]
    assert tr.side == 1 and tr.tag == "orb-long"
    assert tr.t_entry == B.local_to_utc(DAY2, 9 * 3600 + 55 * 60, B.NY)       # the bar after the 09:50 retest
    assert abs(tr.stop0 - 101.37) < 1e-9 and tr.reason == "target"


def test_opening_range_needs_the_levels():
    b = spy_two_days()
    shifted = B.Bars(b.t, b.o, b.h, b.l, b.c, b.v)
    first = b.t < B.local_to_utc(DAY2, 0, B.NY)
    shifted.h = np.where(first & (b.h > 100.4), 102.0, b.h)                       # PDH 102: no longer above it
    s = OpeningRange("SPY", tf=5)
    assert run(s, s.days(shifted), MARKETS["SPY"].venues["lighter"], 0.01) == []


def test_ema_pullback_long_above_pdh():
    """Day 2 trades above PDH, the EMAs turn up, and a dip to EMA13 that closes above it is bought."""
    rows = session(DAY1, B.NY, "09:30", "16:00", 100.0, {"10:00": (100, 100.5, 99.5, 100)})
    sp = {}
    for m in range(9 * 60 + 30, 16 * 60):
        p = 101.0 + (m - 570) * 0.01                      # a steady climb above PDH 100.5
        sp[f"{m // 60:02d}:{m % 60:02d}"] = (p, p + 0.005, p - 0.005, p + 0.005)
    sp["10:30"] = (101.6, 101.6, 101.2, 101.605)          # a dip through EMA13 that closes back above it
    rows += session(DAY2, B.NY, "09:30", "16:00", 101.0, sp)
    s = EmaPullback("QQQ", tf=1)
    trades = run(s, s.days(make(rows)), MARKETS["QQQ"].venues["lighter"], 0.01)
    assert trades and trades[0].side == 1
    assert trades[0].t_entry == B.local_to_utc(DAY2, 10 * 3600 + 31 * 60, B.NY)
    assert abs(trades[0].stop0 - 101.19) < 1e-9


def test_structure_trend_breaks():
    h = np.array([1, 2, 3, 2, 1, 1.5, 2.5, 3.5, 3, 2, 1.0])
    lo = h - 0.5
    c = h - 0.2
    tr = structure_trend(h, lo, c, n=2)
    assert tr[7] == 1          # 3.3 closes above the swing high at 3 (confirmed at bar 4)


def gold_day() -> B.Bars:
    """Previous day flat at 2000. Today: Asian range 2000-2010; at 07:30 London it runs under 2000 to 1995, turns,
    and breaks the last swing high at 2008 by 08:30 with a gap left on the way up."""
    prev = session(DAY1, B.LONDON, "00:00", "23:59", 2000.0)
    sp = {}
    for m in range(0, 7 * 60):                              # Asia: a gentle range
        p = 2005 + 5 * np.sin(m / 30)
        sp[f"{m // 60:02d}:{m % 60:02d}"] = (p, p + 0.2, p - 0.2, p)
    path = [(7, 0, 2006), (7, 15, 2008.5), (7, 20, 2004), (7, 30, 1997), (7, 35, 1995), (7, 40, 1998),
            (7, 45, 2001), (7, 50, 2004), (7, 55, 2006), (8, 0, 2007), (8, 5, 2009), (8, 10, 2010), (8, 15, 2011)]
    for (h0, m0, p0), (h1, m1, p1) in zip(path, path[1:], strict=False):
        a, z = h0 * 60 + m0, h1 * 60 + m1
        for m in range(a, z):
            p = p0 + (p1 - p0) * (m - a) / (z - a)
            q = p0 + (p1 - p0) * (m + 1 - a) / (z - a)
            sp[f"{m // 60:02d}:{m % 60:02d}"] = (p, max(p, q) + 0.1, min(p, q) - 0.1, q)
    for m in range(8 * 60 + 15, 9 * 60):                     # drifts back down into the gap
        p = 2011 - (m - 495) * 0.25
        sp[f"{m // 60:02d}:{m % 60:02d}"] = (p, p + 0.1, p - 0.3, p - 0.25)
    today = session(DAY2, B.LONDON, "00:00", "20:00", 2000.0, sp)
    return make(prev + today)


def test_london_sweep_finds_sweep_choch_and_gap():
    s = LondonSweep(tf=5, bias="none")
    days = s.days(gold_day())
    assert len(days) == 1
    d = days[0]
    assert d.ctx["asia_l"] < 2000.2 and d.ctx["asia_h"] > 2009.5
    trades = run(s, days, MARKETS["XAU"].venues["lighter"], 0.01)
    assert s.dir == 1 and s.ext < 1995.5
    assert len(trades) == 1
    tr = trades[0]
    assert tr.side == 1 and tr.stop0 < s.ext and tr.entry > s.ext
    assert abs(tr.target - (tr.entry + 3 * (tr.entry - tr.stop0))) < 1e-6


def test_a_half_built_signal_bar_is_not_closed():
    """Live, the newest minute may sit inside a 5-minute bar: that bar must not count as closed yet."""
    b = spy_two_days()
    cut = b.between(0, B.local_to_utc(DAY2, 9 * 3600 + 47 * 60, B.NY))       # up to 09:46 (09:45 bar half built)
    d = OpeningRange("SPY", tf=5).days(cut)[-1]
    assert not d.ctx["closes"][-1] and d.ctx["closes"][-3]                    # 09:44 closed the 09:40 bar
    cut = b.between(0, B.local_to_utc(DAY2, 9 * 3600 + 50 * 60, B.NY))       # up to 09:49: now it is closed
    assert OpeningRange("SPY", tf=5).days(cut)[-1].ctx["closes"][-1]


def test_a_venues_weekend_and_holiday_sessions_are_not_trading_days():
    import datetime as dt

    from daytrade.strategies import stock_days

    num = lambda s: (dt.date.fromisoformat(s) - dt.date(1970, 1, 1)).days     # noqa: E731
    rows = []
    for day in range(num("2026-10-02"), num("2026-10-07")):   # Fri 2 Oct 2026 .. Tue 6 Oct; Sat and Sun trade too
        for k in range(390):
            t = B.local_to_utc(day, 9 * 3600 + 1800 + 60 * k, B.NY)
            px = 100.0 + day - num("2026-10-02")
            rows.append((t, px, px, px, px, 1.0))
    for k in range(390):                                       # Fri 3 Jul 2026, Independence Day observed
        rows.append((B.local_to_utc(num("2026-07-03"), 9 * 3600 + 1800 + 60 * k, B.NY), 50.0, 50.0, 50.0, 50.0, 1.0))
    days = stock_days(B.from_rows(rows), 5)
    assert [B.day_str(d.key) for d in days] == ["2026-10-05", "2026-10-06"]   # the first Friday has no previous
    assert days[0].ctx["pclose"] == 100.0                      # Monday's previous session is Friday's
