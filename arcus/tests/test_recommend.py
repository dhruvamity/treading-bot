"""`arcus recommend` and the as-of-tape scan: lines to paste, per list and market, from the scans on disk."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from arcus import recommend
from arcus.scout.scan import tape_end_us
from arcus.scout.tape import TapeStore, empty_bbo


def row(market: str, config: str, vol: float, pnl: float, **kw: object) -> dict[str, object]:
    return {"market": market, "config": config, "go": True, "reasons": [], "days": 5, "fills_day": 800.0,
            "volume_day": vol, "pnl_day": pnl, "recent_pnl": 0.0, "recent_volume": 0.0, "recent_checked": True,
            "leverage": 50.0, "used_usd": 100.0, "capital_usd": 100.0, "at_max": True, **kw}


def scans(tmp: Path) -> tuple[Path, Path]:
    arcus, lighter = tmp / "arcus", tmp / "lighter"
    (arcus / "data" / "scout").mkdir(parents=True)
    (lighter / "data" / "scout").mkdir(parents=True)
    now = time.time()
    (arcus / "data" / "scout" / "latest.json").write_text(json.dumps({
        "ts_us": int((now - 600) * 1e6), "capital": {"usd": 100}, "risk": {"daily_stop_usd": 2.0},
        "all": [row("SPY-USD", "touch 0bp @ 50x", 900_000.0, -0.05), row("QQQ-USD", "deep 1bp @ 25x", 400_000.0, -0.02, leverage=25.0),
                row("SPY-USD", "deep 2bp @ 50x", 100_000.0, -0.005)]}))
    (lighter / "data" / "scout" / "latest.json").write_text(json.dumps({
        "t": now, "as_of": now - 3600, "capital": 100, "stops": [2, 5, 25], "volume_cost": 0.1,
        "lists": {"most": [{"market": "SPY", "setup": "Smart +0.5", "leverage": 50.0, "volume_d": 3.7e6,
                            "cost_1k": -0.004, "fills_d": 4600.0, "why": []}], "cheapest": [], "max": []},
        "table": [{"market": "QQQ", "setup": "Mid +1", "leverage": 50.0, "volume_d": 8e5, "cost_1k": 0.02,
                   "fills_d": 1300.0, "x_capital": 8000.0, "why": []}]}))
    return arcus, lighter


def test_it_prints_the_lines_to_paste_for_each_bot(tmp_path: Path) -> None:
    arcus, lighter = scans(tmp_path)
    text = recommend.report(arcus, [], ["volume"], 3, 0.20, lighter)
    assert "/run SPY mid 0 50x paper" in text and "/run SPY mid 0 50x live sl=2" in text
    assert "/l_run SPY smart +0.5 50x paper" in text and "cost $0.000" in text      # a gain is not shown as a cost
    assert "ARCUS" in text and "LIGHTER" in text and "as of" in text


def test_named_markets_get_their_own_best_setup_on_each_bot(tmp_path: Path) -> None:
    arcus, lighter = scans(tmp_path)
    text = recommend.report(arcus, ["qqq"], ["volume"], 3, 0.20, lighter)
    assert "/run QQQ mid +1 25x paper" in text and "/l_run QQQ mid +1 50x paper" in text and "SPY" not in text


def test_no_scan_says_what_to_do(tmp_path: Path) -> None:
    (tmp_path / "arcus").mkdir()
    assert "arcus recommend --scan" in recommend.report(tmp_path / "arcus", [], ["volume"], 3, 0.2, tmp_path / "x")


def test_the_scan_can_be_made_as_of_the_end_of_the_tape(tmp_path: Path) -> None:
    root = tmp_path / "scout"
    assert tape_end_us(root) is None
    t0 = 1_790_000_000_000_000
    bbo = empty_bbo()
    bbo["ts"] = np.array([t0, t0 + 5_000_000, t0 + 9_000_000], dtype=np.int64)
    for k in bbo:
        if k != "ts":
            bbo[k] = np.array([1.0, 1.0, 1.0])
    TapeStore(root / "tape").write_part("BTC-USD", "bbo", "p1", bbo)
    assert tape_end_us(root) == t0 + 9_000_000
