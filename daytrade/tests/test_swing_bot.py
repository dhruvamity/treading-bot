"""The daily paper bot (swing_bot.py) on made-up prices: which days it reads, when its run starts, and that its
trades are the backtest's."""

import datetime as dt
import json

import numpy as np

from daytrade import bars as B
from daytrade import swing as S
from daytrade import swing_bot as SB
from daytrade import swing_rules as R

MON = (dt.date(2026, 10, 12) - dt.date(1970, 1, 1)).days        # a Monday


def minutes(day: int, price: float, upto: str = "15:58", step: float = 0.0) -> list[tuple]:
    h, m = (int(x) for x in upto.split(":"))
    n = (h * 60 + m) - (9 * 60 + 30) + 1
    out = []
    for k in range(n):
        t = B.local_to_utc(day, SB.OPEN_S + 60 * k, B.NY)
        p = price + step * k
        out.append((t, p, p + 0.5, p - 0.5, p, 1.0))
    return out


def test_a_day_counts_once_its_15_58_minute_has_closed_and_only_on_trading_days():
    rows = minutes(MON - 2, 90.0) + minutes(MON, 100.0, step=0.01) + minutes(MON + 1, 110.0, upto="12:00")
    b = B.from_rows(rows)
    end_mon = B.local_to_utc(MON, SB.DECIDE_S, B.NY)
    days = SB.venue_days(b, end_mon + 3600)
    assert [d[0] for d in days] == [MON]                     # Saturday is no session; Tuesday is not finished
    _day, o, h, lo, c = days[0]
    assert o == 100.0 and abs(c - (100.0 + 0.01 * 388)) < 1e-9 and abs(h - (c + 0.5)) < 1e-9 and lo == 99.5
    assert SB.venue_days(b, end_mon - 1) == []               # 15:58's minute still running


def fake_history(n: int, start_day: int) -> list[tuple]:
    """n weekdays before `start_day`: a steady rise (well above the 200-day average), then two down days."""
    days, d = [], start_day - 1
    while len(days) < n:
        if B.weekday(d) < 5:
            days.append(d)
        d -= 1
    days.reverse()
    px = [*list(np.linspace(60, 100, n - 2)), 99.0, 98.0]
    return [(x, p, p + 0.3, p - 0.3, p) for x, p in zip(days, px, strict=True)]


def test_the_paper_run_starts_the_next_day_after_15_59_and_replays_like_the_backtest(tmp_path, monkeypatch):
    hist = fake_history(260, MON)
    monkeypatch.setattr(SB, "yahoo_days", lambda sym: hist)
    lighter = B.from_rows(minutes(MON, 95.0) + minutes(MON + 1, 99.0) + minutes(MON + 2, 101.0))   # Monday: RSI(2) ~1
    monkeypatch.setattr(SB.B, "load", lambda name: lighter)
    m = SB.SwingMarket("SPY", [R.Rsi2("SPY")], tmp_path)
    after_close_sun = B.local_to_utc(MON - 1, 17 * 3600, B.NY)
    m.start_day(after_close_sun)                              # Sunday evening: the run starts on Monday
    assert json.loads((tmp_path / "paper_swing_start.json").read_text()) == {"SPY": "2026-10-12"}
    now = B.local_to_utc(MON + 2, SB.DECIDE_S + 60, B.NY)
    status, rows = m.update(now, fetch_prices=False)
    cb = SB.daily_bars("SPY", SB.venue_days(lighter, now))
    i0 = int(np.searchsorted(cb.day, MON))
    want, pos = S.simulate_live(R.Rsi2("SPY"), cb, m.venue, S.Funding(m.venue), start=i0)
    assert [r["entry"] for r in rows] == [round(x.entry, 4) for x in want]
    assert want and want[0].entry == 95.0 and want[0].t_in == B.local_to_utc(MON, SB.DECIDE_S, B.NY)
    assert want[0].exit == 99.0                                             # Tuesday closes over the 5-day average
    assert status["spy-rsi2[<10,out sma5,sma200]@lighter"]["state"].startswith("LONG") == (pos is not None)
    assert m.update(now + 60, fetch_prices=False) is m.cache                # nothing new: the same answer


# ---- gold, hourly --------------------------------------------------------------------------------------------------

def gold_hours_rows(first_day: int, n_days: int, breakout_days: set[int], px0: float = 2000.0) -> list[tuple]:
    """One row an hour (the paper bot makes hours from minutes; an hour's single row is its own hour), every day of
    the week like Lighter's gold: a slow rise (the 50-day trend up), flat from 00:00 to 07:00 London, and on
    `breakout_days` a jump above that range in the 07:00 hour."""
    rows, px = [], px0
    for d in range(first_day, first_day + n_days):
        for hr in range(24):
            t = B.local_to_utc(d, hr * 3600, B.LONDON)
            o = px
            if hr == 7 and d in breakout_days:
                px *= 1.004
            elif hr >= 8:
                px *= 1.0001
            rows.append((t, o, max(o, px) + 0.1, min(o, px) - 0.1, px, 1.0))
    return rows


def test_gold_hours_are_complete_hours_with_the_reference_before_lighter(tmp_path, monkeypatch):
    ref = B.from_rows(gold_hours_rows(MON - 80, 70, set(), px0=1000.0))        # stays under 1,200
    ven = B.from_rows(gold_hours_rows(MON - 10, 12, set(), px0=2100.0))
    monkeypatch.setattr(SB.B, "load", lambda name: ven if name == "lighter_XAU" else ref)
    m = SB.GoldMarket(state_dir=tmp_path)
    now = B.local_to_utc(MON, 5 * 3600 + 1800, B.LONDON)            # 05:30: the 05:00 hour is not complete
    h = m.hours(now, now // 3600 * 3600)
    assert int(h.t[-1]) == now // 3600 * 3600 - 3600                  # the last bar is 04:00-05:00
    first_ven = int(ven.t[0])
    assert (h.c[h.t >= first_ven] >= 2100.0).all() and (h.c[h.t < first_ven] < 2100.0).all()


def test_gold_paper_trades_weekdays_only_and_replays_like_the_backtest(tmp_path, monkeypatch):
    first = MON - 70
    days = range(first, MON + 7)
    wanted = {d for d in days if d >= MON - 1}                         # Sun 11 Oct .. Sun 18 Oct: weekend too
    ven = B.from_rows(gold_hours_rows(first, len(days), wanted))
    monkeypatch.setattr(SB.B, "load", lambda name: ven if name == "lighter_XAU" else B.empty())
    m = SB.GoldMarket(state_dir=tmp_path)
    m.start_hour(B.local_to_utc(MON - 1, 12 * 3600, B.LONDON))       # the run began Sunday noon
    now = B.local_to_utc(MON + 7, 0, B.LONDON)
    status, rows = m.update(now, fetch_prices=False)
    def london_day(utc: str) -> int:
        t = int(dt.datetime.fromisoformat(utc).replace(tzinfo=dt.UTC).timestamp())
        return int(B.local(np.array([t]), B.LONDON)[0]) // B.DAY

    assert sorted(london_day(r["entry_utc"]) for r in rows) == list(range(MON, MON + 5))   # no weekend entry
    assert next(iter(status.values()))["state"] == "flat"              # Friday's trade ended on Saturday
    cb = S.hourly(m.hours(now, m.start_hour(now)), "x")
    want, _ = S.simulate_live(R.AsianBreak(days="weekdays"), cb, m.venue, S.Funding(m.venue),
                              start=int(np.searchsorted(cb.t_o, m.start_hour(now))))
    assert [r["entry"] for r in rows] == [round(x.entry, 4) for x in want]
    assert all(r["side"] == "long" and r["stop_bp"] > 0 for r in rows)     # a stop: R is filled in too
    assert json.loads((tmp_path / "paper_swing_start.json").read_text())["XAU"].endswith(":00")


def test_a_new_server_fetches_the_gold_reference_once(tmp_path, monkeypatch):
    ven = B.from_rows(gold_hours_rows(MON - 10, 12, set()))
    got: list = []
    monkeypatch.setattr(SB.B, "load", lambda name: ven if name == "lighter_XAU" else B.empty())
    monkeypatch.setattr(SB.fetch, "binance", lambda *a: got.append(a))
    m = SB.GoldMarket(state_dir=tmp_path)
    m.ensure_reference()
    m.ensure_reference()
    assert got == [("XAUUSDT", "futures", "2025-11")]
