"""Paper farm: the Tread-mode policies, the analysis outputs, the synthetic tapes and the run loop."""

from __future__ import annotations

import asyncio
import gzip
import json
import subprocess
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bot.farm import analyze, service
from bot.farm.menu import BY_NAME, FARM_MENU
from bot.farm.policies import DGridPolicy, RsiSkewPolicy, TMidPolicy, efficiency_ratio, tightest_pair
from bot.farm.synth import PROFILES, REGIMES, generate
from bot.scout.sim import BUY, SELL, AnchorPolicy, Book, MarketInfo, RGridPolicy, Risk
from bot.scout.tape import TapeStore, day_start_us

S = 1_000_000
T0 = day_start_us("2026-09-26")   # a Saturday
MI = MarketInfo(tick=0.01, step=0.001, min_notional=5.0)
RISK = Risk(capital_usd=100, order_usd=50, cap_usd=100)


def book(bid: float, ask: float, pos: float = 0.0, closes: list[float] | None = None, sigma_1m: float = 0.0,
         t: int = T0) -> Book:
    return Book(t, bid, ask, (bid + ask) / 2, pos, None, sigma_1m, sigma_1m * 60 ** 0.5,
                deque(closes or [], maxlen=240))


def test_tightest_pair_is_strictly_around_the_mid() -> None:
    assert tightest_pair(100.005, 0.01) == pytest.approx((100.00, 100.01))   # one-tick book: the touch
    assert tightest_pair(100.01, 0.01) == pytest.approx((100.00, 100.02))    # mid on a tick: one tick each side
    b, a = tightest_pair(100.015, 0.01)
    assert b < 100.015 < a and a - b == pytest.approx(0.01)


@pytest.mark.parametrize(("bid", "ask", "want"), [(100.00, 100.01, (100.00, 100.01)),
                                                   (100.00, 100.02, (100.00, 100.02)),
                                                   (100.00, 100.04, (100.01, 100.03))])
def test_mid0_quotes_the_touch_or_inside_it(bid: float, ask: float, want: tuple[float, float]) -> None:
    q, taker = TMidPolicy(BY_NAME["mid0"].cfg, RISK, MI).quotes(book(bid, ask))
    assert taker == 0.0
    px = {s: p for s, p, _, _ in q}
    assert (px[BUY], px[SELL]) == pytest.approx(want)
    assert px[BUY] < px[SELL]


def test_mid0_skew_steps_back_the_side_that_adds() -> None:
    long = book(100.00, 100.10, pos=0.4)                     # 0.4 x 100 = $40 of a $100 cap: u = 0.4
    plain = {s: p for s, p, _, _ in TMidPolicy(BY_NAME["mid0"].cfg, RISK, MI).quotes(long)[0]}
    skew = {s: p for s, p, _, _ in TMidPolicy(BY_NAME["mid0 skew"].cfg, RISK, MI).quotes(long)[0]}
    assert skew[SELL] == pytest.approx(plain[SELL])
    assert skew[BUY] == pytest.approx(plain[BUY] - 0.01)     # round(2 x 1 x 0.4) = 1 tick back


def test_rsi_skew_sells_closer_when_rsi_is_high() -> None:
    up = [100 + 0.05 * i for i in range(40)]
    q, _ = RsiSkewPolicy(BY_NAME["rsi+5"].cfg, RISK, MI).quotes(book(101.99, 102.01, closes=up))
    px = {s: p for s, p, _, _ in q}
    mid = 102.0
    assert mid - px[BUY] > px[SELL] - mid


def test_dgrid_picks_grid_in_chop_and_rgrid_in_a_trend() -> None:
    chop = [100 + (0.05 if i % 2 else -0.05) for i in range(40)]
    trend = [100 + 0.05 * i for i in range(40)]
    assert efficiency_ratio(chop[-31:]) < 0.1 and efficiency_ratio(trend[-31:]) == pytest.approx(1.0)
    p = DGridPolicy(BY_NAME["dgrid"].cfg, RISK, MI)
    p.quotes(book(99.99, 100.01, closes=chop, sigma_1m=4e-4))
    assert isinstance(p.sub, AnchorPolicy) and p.d == pytest.approx(2.0)   # half of 4 bps
    p2 = DGridPolicy(BY_NAME["dgrid"].cfg, RISK, MI)
    p2.quotes(book(101.99, 102.01, closes=trend, sigma_1m=1e-4))
    assert isinstance(p2.sub, RGridPolicy) and p2.d == pytest.approx(1.0)  # the 1 bp floor


def test_menu_names_and_modes() -> None:
    assert len(BY_NAME) == len(FARM_MENU) >= 25
    assert {e.cfg.mode for e in FARM_MENU} <= {"tmid", "vmid", "mid", "anchor", "rgrid", "dgrid", "rsiskew"}
    assert all(e.sources for e in FARM_MENU)


def test_risk_label_thresholds() -> None:
    assert analyze.risk_label(50, 1, False, 1) == "R1"
    assert analyze.risk_label(250, 5, False, 5) == "R2"
    assert analyze.risk_label(800, 15, False, 20) == "R3"
    assert analyze.risk_label(2000, 5, False, 5) == "R4"
    assert analyze.risk_label(-500, 1, True, 0) == "R4"
    assert analyze.risk_label(None, 0, False, 0) == "U"


def _synthetic_market(root: Path, hours: float = 2.0, market: str = "SYN-USD") -> dict[str, Any]:
    tape, meta = generate(PROFILES["index"], REGIMES["mixed"], hours=hours, start_us=T0, seed=7, market=market)
    st = TapeStore(root)
    st.write_part(market, "bbo", "syn", tape.bbo)
    st.write_part(market, "trades", "syn", tape.trades)
    return meta


def test_synthetic_tape_is_deterministic_and_well_formed() -> None:
    a, _ = generate(PROFILES["major"], REGIMES["chop"], hours=0.5, start_us=T0, seed=3)
    b, _ = generate(PROFILES["major"], REGIMES["chop"], hours=0.5, start_us=T0, seed=3)
    assert np.array_equal(a.bbo["bid"], b.bbo["bid"]) and np.array_equal(a.trades["px"], b.trades["px"])
    assert (a.bbo["ask"] > a.bbo["bid"]).all()
    assert np.all(np.diff(a.bbo["ts"]) >= 0)
    assert len(np.unique(a.trades["seq"])) < len(a.trades["seq"])   # some takers sweep several levels


def test_run_market_writes_candles_series_and_fills(tmp_path: Path) -> None:
    meta = _synthetic_market(tmp_path / "tape")
    names = ["mid0", "mid+1", "grid+3 r0.5", "dgrid", "rsi+5"]
    out = analyze.run_market({"tape_root": str(tmp_path / "tape"), "market": "SYN-USD", "start": T0 + 1800 * S,
                              "end": T0 + 7200 * S, "meta": meta, "out": str(tmp_path / "out"), "capital": 100,
                              "alive_market": "SYN-USD", "settings": names, "leverages": [5.0, 10.0]})
    rows = out["rows"]
    assert len(rows) == 3 * len(names)   # 5x, 10x and the maximum (25x)
    assert {r["risk"] for r in rows} <= {"R1", "R2", "R3", "R4", "U"}
    assert any(r["volume_usd"] > 0 for r in rows)
    r = next(r for r in rows if r["setting"] == "mid0" and r["leverage"] == 10)
    assert r["turnover_per_h"] == pytest.approx(r["volume_usd"] / r["used"] / r["hours"], rel=0.01)
    with gzip.open(tmp_path / "out" / "candles" / "SYN-USD.csv.gz", "rt") as f:
        lines = f.read().splitlines()
    assert lines[0].startswith("t,open,high,low,close") and len(lines) == 1 + 90   # 1.5 h of minutes
    z = np.load(tmp_path / "out" / "paper" / "SYN-USD.npz")
    assert z["minutes"].shape[0] == len(rows) and z["minutes"].shape[2] == len(analyze.MINUTE_COLS) - 1
    assert z["minute_t"].shape == z["minutes"].shape[:2]
    assert len(z["fill_t"]) == sum(r["maker_fills"] + r["taker_fills"] for r in rows) == len(z["fill_key"])
    md = analyze.leaderboard(rows, "t", {"k": "v"})
    assert "| setting |" in md and "mid0" in md


def test_day_loss_is_capped_by_the_daily_stop() -> None:
    assert analyze.day_loss(-5.0, 100.0, 12.0, 0) == pytest.approx(10.0)    # 5% in 12 h: 10% a day
    assert analyze.day_loss(-10.5, 100.0, 12.0, 1) == pytest.approx(10.5)   # stopped for the day: 10.5% that day
    assert analyze.day_loss(-21.0, 100.0, 36.0, 2) == pytest.approx(10.5)   # two UTC days, two stops
    assert analyze.day_loss(3.0, 100.0, 12.0, 0) == 0.0


def test_leverages_add_the_market_maximum() -> None:
    meta = {"marketDisplayName": "SPY-USD", "initialMarginFraction": "0.02", "offHoursInitialMarginFraction": "0.03"}
    assert analyze.leverages_for(meta) == [(5.0, 5.0), (10.0, 10.0), (20.0, 20.0), (50.0, 33.33)]
    btc = {"marketDisplayName": "BTC-USD", "initialMarginFraction": "0.025"}
    assert [x for x, _ in analyze.leverages_for(btc)] == [5.0, 10.0, 20.0]   # the owner's 20x cap
    alt = {"marketDisplayName": "ALT-USD", "initialMarginFraction": "0.1"}
    assert [x for x, _ in analyze.leverages_for(alt)] == [5.0, 10.0]


def test_finished_tape_skips_the_current_hour(tmp_path: Path) -> None:
    now = 1_790_000_000.0
    hour = int(now // 3600)
    for h in (hour - 2, hour - 1, hour):
        p = tmp_path / "BTC-USD" / "2026-09-22" / f"bbo-rec010203-{h}.npz"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (tmp_path / "BTC-USD" / "2026-09-22" / f"bbo-rec010203-{hour - 1}.npz.tmp.npz").write_bytes(b"x")
    got = [p.name for p in service.finished_tape(tmp_path, now)]
    assert got == [f"bbo-rec010203-{hour - 2}.npz", f"bbo-rec010203-{hour - 1}.npz"]


def test_git_commit_push_takes_paths_relative_to_the_working_directory(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    (repo / "bot").mkdir(parents=True)
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    (repo / "research" / "runs" / "r").mkdir(parents=True)
    (repo / "research" / "runs" / "r" / "LEADERBOARD.md").write_text("x\n")
    monkeypatch.chdir(repo / "bot")                       # the farm runs from bot/ with --out ../research/runs
    out = service.git_commit_push(service.repo_root(Path("../research/runs/r")) or repo,
                                  [Path("../research/runs/r/LEADERBOARD.md")], "t", push=False)
    assert out == "committed"


def test_farm_loop_records_analyses_and_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The run loop with a stand-in recorder: it analyses on schedule and commits the run folder to git."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    run_dir = repo / "research" / "runs" / "test"
    calls: list[tuple[int, int]] = []

    class StubRecorder:
        def __init__(self, root: Path, **_: Any) -> None:
            self.root = root
            self._stop = asyncio.Event()

        async def run(self, duration_s: float | None = None) -> None:
            (self.root / "tape").mkdir(parents=True, exist_ok=True)
            (self.root / "markets.json").write_text(json.dumps({"markets": []}))
            await self._stop.wait()

        def flush(self) -> int:
            return 0

        def stop(self) -> None:
            self._stop.set()

    def fake_analyze(run_dir: Path, tape: Path, mj: Path, start: int, end: int, **_: Any) -> list[dict[str, Any]]:
        calls.append((start, end))
        (run_dir / "LEADERBOARD.md").write_text(f"# window {start}-{end}\n")
        return []

    import bot.scout.record as record
    monkeypatch.setattr(record, "ScoutRecorder", StubRecorder)
    monkeypatch.setattr(service, "analyze_window", fake_analyze)
    monkeypatch.setattr(service, "WARMUP_S", 0)
    farm = service.Farm(run_dir, hours=2.5 / 3600, every_min=1 / 60, capital=100, workers=1, max_markets=5,
                        rest_url="", ws_url="", git=True, push=False)
    asyncio.run(farm.run())
    assert len(calls) >= 2 and calls[-1][1] - calls[0][0] <= int(2.6 * S)
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "Paper farm test" in log
    assert json.loads((run_dir / "run.json").read_text())["capital"] == 100
