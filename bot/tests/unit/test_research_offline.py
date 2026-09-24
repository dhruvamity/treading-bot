"""Research pipeline offline: recorder output from the captured LIVE frames -> DQ report -> microstructure
diagnostics -> backtest harness -> metrics -> GO/NO-GO; history loaders against a fake REST server; carry study on
synthetic funding panels."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from bot.common.config import load_arcus_config, load_lighter_config
from bot.research.recorder.recorder import Recorder

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests" / "fixtures" / "live"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("rec")
    rec = Recorder(["BTC", "SPY"], load_arcus_config(ROOT / "config/venues/arcus.yaml"),
                   load_lighter_config(ROOT / "config/venues/lighter_rh.yaml"), data_dir=root)
    rec.arcus_display = {"BTC": "BTC-USD", "SPY": "SPY-USD"}
    rec.lighter_ids = {"BTC": 1, "SPY": 26}
    rec.lighter_ws.base_by_id.update({1: "BTC", 26: "SPY"})
    rec._wire_arcus()
    rec._wire_lighter()

    async def feed() -> None:
        frames = []
        for name, ws in (("arcus", rec.arcus_ws), ("lighter", rec.lighter_ws)):
            frames += [(fr["recv_ts_us"], ws, fr["raw"])
                       for fr in json.loads((FIX / f"{name}_ws_frames.json").read_text())]
        for ts, ws, raw in sorted(frames, key=lambda x: x[0]):
            await ws._on_message(raw, ts)
            if ts % 7 == 0:
                for disp, sync in rec.arcus_ws.books.items():
                    rec._snapshot_row("arcus", disp.split("-")[0], sync.book, ts)
                for mid, sync in rec.lighter_ws.books.items():
                    rec._snapshot_row("lighter_rh", rec.lighter_ws.base_by_id[mid], sync.book, ts)

    asyncio.run(feed())
    rec.writer.flush()
    return root


def test_dq_and_diagnostics_reports(recorded: Path, tmp_path: Path) -> None:
    from bot.research.diagnostics.microstructure import diagnose, diagnostics_report
    from bot.research.eval.dq import dq_report
    from bot.research.loaders.read import read_table

    bbo = read_table(recorded, "bbo")
    day = bbo["date"][0] if "date" in bbo.columns else None
    assert day
    p = dq_report(recorded, str(day), tmp_path)
    txt = p.read_text()
    assert "| bbo | arcus | BTC |" in txt and "Issues flagged" in txt
    d = diagnose(recorded, "BTC")
    assert d["arcus_minutes"] and d["lighter_rh_minutes"]
    assert 0 < d["arcus_top_taker_share"] <= 1  # type: ignore[operator]
    assert d["arcus_taker_hhi"] > 0  # type: ignore[operator]  (basis needs >30 s of overlap; the two captures barely overlap)
    md = diagnostics_report(recorded, ["BTC", "SPY"], tmp_path)
    assert md.exists() and (md.parent / "diagnostics.parquet").exists()
    assert "| BTC | arcus |" in md.read_text()


def test_backtest_harness_and_gonogo(recorded: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.common.config import load_session
    from bot.research.eval import gonogo
    from bot.research.eval.backtest import run_backtest
    from tests.helpers import fixture_markets

    sess = load_session(ROOT / "config/sessions/arcus_btc_mm.yaml")
    res = run_backtest([sess], recorded, start=None, end=None, markets=fixture_markets())
    assert "error" not in res, res
    assert res["events"] > 100 and res["fill_mode"] == "pessimistic" and "breakdown" in res
    for k in ("net_usd", "fills", "max_drawdown_usd"):
        assert k in res, sorted(res)
    empty = run_backtest([sess], tmp_path / "nothing", start=None, end=None, markets=fixture_markets())
    assert "error" in empty

    async def fake_markets() -> Any:
        return fixture_markets()

    monkeypatch.setattr(gonogo, "current_markets", fake_markets)
    p = gonogo.gonogo_report(recorded, tmp_path, sessions=[sess, load_session(ROOT / "config/sessions/dn_spy.yaml")])
    txt = p.read_text()
    assert "INSUFFICIENT DATA" in txt and "arcus_btc_mm" in txt
    lo, hi = gonogo.bootstrap_30d([1.0, -0.5, 0.2, 0.4] * 5, seed=1, n=200)
    assert lo <= hi


def _write_panel(root: Path) -> None:
    from bot.research.recorder.writer import ParquetWriter

    rng = np.random.default_rng(3)
    w = ParquetWriter(root)
    t0 = 1_780_000_000_000_000
    for base, a_mu, l_mu in (("SPY", 0.00002, 0.000002), ("BTC", 0.00001, 0.00001)):
        for i in range(24 * 40):
            ts = t0 + i * 3_600_000_000
            for venue, mu in (("arcus", a_mu), ("lighter_rh", l_mu)):
                w.add("funding_paid", {"funding_ts_us": ts, "venue": venue, "market": base,
                                       "rate_h": float(mu + rng.normal(0, 4e-6)), "rate_raw": "", "value_per_unit": None,
                                       "pay_price": None, "pay_price_type": "oracle", "index_source": None},
                      ts_us=ts, venue=venue, market=base)
    w.flush()


def test_carry_study_on_synthetic_panel(tmp_path: Path) -> None:
    from bot.research.diagnostics.carry import best_rule, carry_study, study_market

    _write_panel(tmp_path / "d")
    r = study_market(tmp_path / "d", "SPY")
    assert r is not None and r["hours"] == 24 * 40
    assert r["static_pair"]["direction"].lower().startswith("short arcus") or "short" in r["static_pair"]["direction"].lower()
    best_rule(r, 10.0)
    out = carry_study(tmp_path / "d", ["SPY", "BTC", "NVDA"], tmp_path / "rep")
    txt = out.read_text()
    assert "SPY" in txt and "BTC" in txt and (out.parent / "summary.parquet").exists()


def test_history_loaders_with_fake_clients(tmp_path: Path) -> None:
    from bot.research.loaders import history as hist
    from bot.research.loaders.read import funding_panel, read_table
    from bot.research.recorder.writer import ParquetWriter

    arcus_rows = json.loads((FIX / "arcus_funding_spy.json").read_text())["fundingRates"]
    lighter_rows = json.loads((FIX / "lighter_fundings_spy.json").read_text())["fundings"]

    class FA:
        async def funding_rates(self, market: str, **kw: Any) -> Any:
            return [r for r in arcus_rows if r["time"] <= kw["to_us"]]

    class FL:
        async def fundings(self, market_id: int, **kw: Any) -> Any:
            return [f for f in lighter_rows if kw["start_s"] <= f["timestamp"] < kw["end_s"]]

        async def _call(self, method: str, path: str, **kw: Any) -> Any:
            p = kw["params"]
            return {"c": [{"t": f["timestamp"] * 1000, "o": 670.0} for f in lighter_rows
                          if p["start_timestamp"] <= f["timestamp"] < p["end_timestamp"]]}

    w = ParquetWriter(tmp_path)
    ts = [int(r["time"]) for r in arcus_rows]
    sa = asyncio.run(hist.load_arcus_funding(FA(), w, "SPY", "SPY-USD", start_us=min(ts) - 1,  # type: ignore[arg-type]
                                             end_us=max(ts) + 1))
    ls = [int(f["timestamp"]) for f in lighter_rows]
    sl = asyncio.run(hist.load_lighter_funding(FL(), w, "SPY", 26, start_s=min(ls), end_s=max(ls) + 1))  # type: ignore[arg-type]
    w.flush()
    assert sa.rows == len(set(ts)) and sl.rows == len(set(ls))
    df = read_table(tmp_path, "funding_paid")
    assert set(df["venue"].unique().to_list()) == {"arcus", "lighter_rh"}
    lrow = df.filter(pl.col("venue") == "lighter_rh").row(0, named=True)
    assert lrow["index_source"] == "mark_candle_open" and lrow["pay_price"] == 670.0
    fp = funding_panel(tmp_path, "SPY")
    assert not fp.is_empty() and {"r_a", "r_l"} <= set(fp.columns)
