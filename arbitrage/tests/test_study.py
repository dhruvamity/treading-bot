"""`arbitrage study` and `arbitrage history --update` on made-up history (no network)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from arbitrage import backtest as bt
from arbitrage import history, study
from arbitrage.rank import Settings
from tests.test_arbitrage import legs

T0 = 1_790_000_000 // 3600 * 3600


def series(symbol: str, category: str, arcus: float = 2e-5, lighter: float = 4e-6, hours: int = 600) -> bt.Series:
    a, b = legs()
    ts = [T0 + i * 3600 for i in range(hours)]
    px = {t: (100.5, 99.5, 100.0) for t in ts}
    two = {"arcus": replace(a, symbol=symbol, category=category), "lighter": replace(b, symbol=symbol)}
    return bt.Series(symbol, two,
                     {"arcus": dict.fromkeys(ts, arcus), "lighter": dict.fromkeys(ts, lighter)},
                     {"arcus": dict(px), "lighter": dict(px)}, ts)


def test_only_stocks_indices_and_commodities_are_studied() -> None:
    s = {"SPY": series("SPY", "INDICES"), "BTC": series("BTC", "CRYPTO"), "SLV": series("SLV", "COMMODITIES")}
    assert sorted(study.rwa(s)) == ["SLV", "SPY"]


def test_one_payment_never_pays_for_the_fills_and_a_week_does() -> None:
    rows = {r["hours"]: r for r in study.forward({"SPY": series("SPY", "INDICES")}, Settings())}
    assert rows[1]["mean_bp"] == pytest.approx(0.16, abs=1e-6)          # (2e-5 - 4e-6) an hour, in bp
    assert rows[1]["net_8"] < 0 and rows[1]["won_4"] == 0               # one hour: under any fill cost
    assert rows[168]["mean_bp"] == pytest.approx(0.16 * 168, rel=1e-6) and rows[168]["net_16"] > 0
    assert rows[24]["net_4"] < 0 < rows[48]["net_4"]                    # where it starts to pay at 1 bp a fill


def test_the_report_has_every_part_and_runs_on_a_small_history() -> None:
    text = study.report({"SPY": series("SPY", "INDICES"), "SLV": series("SLV", "COMMODITIES")}, 240.0, Settings())
    for part in ("1. The BEST market of each hour", "the best THREE of each hour", "EVERY market that passed",
                 "3. The whole rule replayed", "4. Funding over the next 72 h", "5. Leverage: the best market",
                 "6. Closing once a position has moved", "7. What Arcus paid over Lighter"):
        assert part in text
    assert "exactly 1 h, then pick again" in text and "the venues' maximum leverage" in text


def test_an_update_keeps_the_old_hours_and_adds_the_new(tmp_path: Path) -> None:
    p = tmp_path / "lighter_SPY_funding.csv"
    history.write(p, ["ts", "rate_per_hour"], [(T0, 1e-6), (T0 + 3600, 2e-6)])
    old = history.read_rows(p)
    assert [r[0] for r in old] == [T0, T0 + 3600]
    both = history.merged(old, [(T0 + 3600, 9e-6), (T0 + 7200, 3e-6)])
    assert [r[0] for r in both] == [T0, T0 + 3600, T0 + 7200] and float(both[1][1]) == 9e-6    # the newer row wins
    assert history.read_rows(tmp_path / "nothing.csv") == []


# ------------------------------------------------------------------------------------------------ the best of each hour
def moving(symbol: str, category: str, arcus: float, lighter: float = 4e-6, hours: int = 600,
           swing: float = 0.005, drop_at: int = -1) -> bt.Series:
    """A market whose price swings each hour (so the stop has a distance), and falls 30% at hour `drop_at`."""
    s = series(symbol, category, arcus, lighter, hours)
    for i, t in enumerate(s.hours):
        c = 100.0 * (1 + swing * (i % 2)) * (0.7 if 0 <= drop_at <= i else 1.0)
        bar = (c * 1.001, c * 0.999, c) if i != drop_at else (100.5, c * 0.999, c)
        s.px["arcus"][t] = s.px["lighter"][t] = bar
    return s


def test_each_hour_is_ranked_as_the_bot_would_choose() -> None:
    two = {"SPY": moving("SPY", "INDICES", 2e-5), "SLV": moving("SLV", "COMMODITIES", 4e-5)}
    picks, total = study.ranked(two, 240.0, Settings())
    assert total == 600 and len(picks) > 500                       # every hour once 24 h of history are there
    assert {hour[0].symbol for hour in picks} == {"SLV"}            # the same leverage: the wider difference pays more
    assert [x.symbol for x in picks[0]] == ["SLV", "SPY"] and [x.rank for x in picks[0]] == [0, 1]
    assert picks[0][0].sign == 1 and picks[0][0].entry[0] > 0       # short Arcus (it pays more), priced on both venues
    best = {r["hours"]: r for r in study.top_forward(two, picks, 1)}
    three = {r["hours"]: r for r in study.top_forward(two, picks, 3)}
    assert best[24]["mean_bp"] == pytest.approx(0.36 * 24, rel=1e-6)            # SLV alone: 3.6e-5 an hour
    assert three[24]["mean_bp"] == pytest.approx((0.36 + 0.16) / 2 * 24, rel=1e-6)
    assert len(study.starts(picks)) == 1                            # the best never changed: one opportunity


def test_a_life_ends_when_the_difference_is_gone_or_at_the_stop() -> None:
    s = moving("SPY", "INDICES", 2e-5)
    for t in s.hours[300:]:
        s.rate["arcus"][t] = 0.0                                    # from hour 300 Arcus pays less than Lighter
    picks, _ = study.ranked({"SPY": s}, 240.0, Settings())
    first = picks[0][0]
    st = replace(Settings(), min_hold_h=0.0)
    hours, bp, hit = study.life(s, first, st)
    assert not hit and first.k + hours == 319       # closed once the last 24 h together pay nothing: 20 h at -0.04
    assert bp == pytest.approx((299 - first.k) * 0.16 - 20 * 0.04, rel=1e-6)
    lv = study.lives({"SPY": s}, picks, Settings())
    assert lv["n"] == 1 and lv["median"] == hours and lv["over_168"] == 100.0
    fall = moving("SPY", "INDICES", 2e-5, drop_at=100)
    picks, _ = study.ranked({"SPY": fall}, 240.0, Settings())
    x = picks[0][0]
    hours, _, hit = study.life(fall, x, st, stop=x.stop)
    assert hit and x.k + hours == 101                               # the bar that fell is the hour ending there


def test_more_leverage_means_a_nearer_stop_and_more_of_them() -> None:
    one = {"SPY": moving("SPY", "INDICES", 2e-5, swing=0.03)}
    rows = {r["name"]: r for r in study.top_leverage(one, 240.0)}
    low, high = rows["2x on every market that allows it"], rows["the venues' maximum leverage (the bot now)"]
    # the venues allow 10x while the stock market is open and 6.7x while it is closed: the hours are a mix of both
    assert low["leverage"] == pytest.approx(2.0) and 1 / 0.15 < high["leverage"] < 10.0
    assert low["stop_pct"] > 15 > high["stop_pct"] and high["stop_pct"] < 6
    assert low["stopped_pct"] == 0 and high["stopped_pct"] > 0      # a 3% swing reaches the stop only at the highest
    assert low["apr_1"] > low["apr_2"] > low["apr_4"]
    old = Settings(stop_sigmas=3.0, max_leverage=20.0, hold_off_hours=True)        # the rule until 10 Oct 2026
    rows = {r["name"]: r for r in study.top_leverage(one, 240.0, old)}
    assert "stop 3 daily moves away (the bot now)" in rows
    assert rows["the venues' maximum leverage"]["leverage"] == pytest.approx(1 / 0.15)     # always the off-hours margin
    assert rows["the venues' maximum leverage"]["funding_apr"] == pytest.approx(
        rows["2x on every market that allows it"]["funding_apr"] * (1 / 0.15) / 2, rel=0.05)


def test_profunding_rows_and_the_check_against_the_venues(tmp_path: Path) -> None:
    body = {"rates": [{"timestamp": "2026-10-03T11:00:00+00:00", "rate": 5e-06},
                      {"timestamp": "2026-10-03T12:00:00", "rate": 6e-06}]}
    rows = history.pf_rows(body)
    assert [r[1] for r in rows] == [5e-06, 6e-06] and rows[1][0] - rows[0][0] == 3600 and rows[0][0] % 3600 == 0
    assert history.pf_rows({}) == []
    assert history.profunding(tmp_path, "", say=lambda _: None) == 0             # no key: nothing asked
    for v, rate in (("arcus", 5e-06), ("lighter", 4e-06)):
        history.write(tmp_path / f"{v}_SPY_funding.csv", ["ts", "rate_per_hour"], [(r[0], rate) for r in rows])
    assert "no files" in study.sources(tmp_path, ["SPY"])[-1]
    history.write(tmp_path / "profunding_arcus_SPY_funding.csv", ["ts", "rate_per_hour"], rows)
    text = "\n".join(study.sources(tmp_path, ["SPY"]))
    assert "arcus    funding: 1 markets, 2 hourly payments" in text and "0 hours missing" in text
    assert "ProFunding's arcus rates: 1 markets, 2 hours in both" in text and "hour by hour 50% are within" in text


def test_the_best_markets_volume_at_the_time_is_read_from_the_price_files(tmp_path: Path) -> None:
    s = moving("SPY", "INDICES", 2e-5)
    picks, _ = study.ranked({"SPY": s}, 240.0, Settings())
    head = ["ts", "open", "high", "low", "close"]
    for v in ("arcus", "lighter"):
        history.write(tmp_path / f"{v}_SPY_px.csv", head, [(t, 1, 1, 1, 1) for t in s.hours])
    assert "no volume column" in study.traded(tmp_path, picks)                   # files from before it was added
    history.write(tmp_path / "arcus_SPY_px.csv", [*head, "volume"], [(t, 1, 1, 1, 1, 10_000) for t in s.hours])
    history.write(tmp_path / "lighter_SPY_px.csv", [*head, "volume"], [(t, 1, 1, 1, 1, 1_000) for t in s.hours])
    text = study.traded(tmp_path, picks)
    assert "Arcus $240,000 in the middle hour" in text and "Lighter $24,000 and" in text     # 24 h x the hourly figure


def test_the_replay_checks_the_volume_of_the_time_when_the_files_have_it(tmp_path: Path) -> None:
    s = moving("SPY", "INDICES", 2e-5)
    assert len(study.ranked({"SPY": s}, 240.0, Settings())[0]) > 500             # no volume known: today's is used
    busy = {v: dict.fromkeys(s.hours, 10_000.0) for v in bt.VENUES}             # $240,000 a day on each venue
    quiet = {"arcus": busy["arcus"], "lighter": {t: (10_000.0 if i >= 300 else 100.0) for i, t in enumerate(s.hours)}}
    assert len(study.ranked({"SPY": replace(s, vol=busy)}, 240.0, Settings())[0]) > 500
    picks, _ = study.ranked({"SPY": replace(s, vol=quiet)}, 240.0, Settings())
    assert 250 < len(picks) < 300 and picks[0][0].k > 300       # thin on Lighter until hour 300: not opened before
    text = study.report({"SPY": replace(s, vol=busy)}, 240.0, Settings())
    assert "8. The volume floor" in text and "$100,000 a day (the bot now)" in text
    assert "8. The volume floor" not in study.report({"SPY": s}, 240.0, Settings())
