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
    for part in ("1. After the entry rule says go", "2. The whole rule replayed", "3. Funding over the next 72 h",
                 "4. The stop's distance", "5. Closing once a position has moved", "6. What Arcus paid over Lighter"):
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
