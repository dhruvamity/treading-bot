"""`bot diagnose`: a run's orders, acknowledgements, blocks and fills against the recorded market."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from bot.core.diagnose import diagnose
from bot.core.state import StateStore
from bot.scout.tape import TapeStore, day_start_us

S = 1_000_000
T0 = day_start_us("2026-09-25") + 20 * 3600 * S   # 20:00 UTC


def _run(tmp_path: Path) -> Path:
    """Ten minutes of a live QQQ run: buys resting 6 ticks behind the best bid, one order never acknowledged, one
    rejected, a safety pause, one fill."""
    db = tmp_path / "live.sqlite"
    StateStore(db).close()
    con = sqlite3.connect(db)
    rows, events = [], []
    for i in range(10):
        t = T0 + i * 60 * S
        cid = f"b{i}"
        status = "REJECTED" if i == 3 else "CANCELED"
        rows.append((cid, "arcus", "QQQ", "buy", "744.94", "0.3", "GTT", 0, "b0", status, f"v{i}", "0", None,
                     "UNDERCOLLATERALIZED" if i == 3 else None, "strategy", "pilot", t, t + 50 * S))
        if i not in (3, 5):          # 5: never acknowledged
            events.append((t + 40_000, "arcus", "update", cid, "pilot", json.dumps({"status": "OPEN"})))
        events.append((t + 50 * S, "arcus", "update", cid, "pilot", json.dumps({"status": status})))
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO events (ts_us, venue, kind, client_id, session, payload) VALUES (?,?,?,?,?,?)",
                    events)
    con.execute("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("t1", "arcus", "QQQ", "b1", "buy", "744.94", "0.3", "0", 1, 0, T0 + 90 * S, "b0", "pilot"))
    con.commit()
    con.close()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "decisions.jsonl").write_text(json.dumps({"ts": T0 + 400 * S, "event": "risk:pause_quotes",
                                                      "market": "QQQ",
                                                      "reason": "spread 1.2 bps > 3.0x median 0.1"}) + "\n")
    st = TapeStore(tmp_path / "tape")
    ts = np.arange(T0 - 60 * S, T0 + 600 * S, S, dtype=np.int64)
    st.write_part("QQQ-USD", "bbo", "rec1", {"ts": ts, "bid": np.full(len(ts), 745.0), "ask": np.full(len(ts), 745.01),
                                             "bid_sz": np.ones(len(ts)), "ask_sz": np.ones(len(ts))})
    tts = T0 + np.array([30, 45, 100, 200], dtype=np.int64) * S          # taker sells at 744.90: through 744.94
    st.write_part("QQQ-USD", "trades", "rec1", {"ts": tts, "px": np.array([744.9, 744.9, 745.0, 745.01]),
                                                "sz": np.full(4, 0.5), "buy": np.array([False, False, True, True]),
                                                "seq": np.arange(1, 5), "tid": np.arange(1, 5)})
    (tmp_path / "markets.json").write_text(json.dumps({"markets": [{"marketDisplayName": "QQQ-USD",
                                                                    "tickSize": "0.01"}]}))
    return db


def test_diagnose_reports_acks_rejects_placement_blocks_and_missed_flow(tmp_path: Path) -> None:
    db = _run(tmp_path)
    text = diagnose(db=db, logs=tmp_path / "logs", tape_root=tmp_path / "tape", markets_json=tmp_path / "markets.json",
                    start_us=T0, end_us=T0 + 600 * S)
    assert "QQQ-USD · LIVE" in text and "10 sent (10 buy, 0 sell) · acknowledged 8 (median 40 ms)" in text
    assert "Rejected  UNDERCOLLATERALIZED ×1" in text and "1 orders were never acknowledged" in text
    assert "median 6.0 ticks behind" in text and "at or inside the best price 0%" in text
    assert "a buy rested 6m39s of 10m00s (67%)" in text   # 8 acknowledged orders x 50 s (less the ack), not the 2 others
    assert "risk:pause_quotes ×1" in text and "spread 1.2 bps" in text
    assert "Fills     1 · $223" in text
    assert "4 taker trades" in text and "2 went through a price you rested at" in text
    assert "(sell side" not in text and "2 ($745) traded while you had no order on that side" in text


def test_diagnose_counts_orders_refused_by_the_bots_own_checks(tmp_path: Path) -> None:
    """2026-09-25 QQQ: ~4,900 sells refused by the pre-trade margin check never reached the state database, so
    diagnose showed nothing for the two hours the bot sat without quotes."""
    db = _run(tmp_path)
    lines = [{"ts": T0 + (100 + i) * S, "event": "reject_pretrade", "market": "QQQ",       # older logs: tag only
              "reason": "free_collateral: needs $20.00, free $12.00", "data": {"tag": "a0"}} for i in range(120)]
    lines += [{"ts": T0 + (300 + i) * S, "event": "reject_pretrade", "market": "QQQ",
               "reason": "free_collateral: needs $20.50, free $12.00", "data": {"tag": "a0", "side": "sell"}}
              for i in range(60)]
    lines += [{"ts": T0 + 500 * S, "event": "reject_pretrade", "market": "QQQ",
               "reason": "oi_cap: market OI $9000 + order would exceed cap $9000", "data": {"tag": "b0"}},
              {"ts": T0 + 510 * S, "event": "reject_pretrade", "market": "SPY",                 # another market
               "reason": "free_collateral: needs $1.00, free $0.00", "data": {"tag": "b0"}}]
    with (tmp_path / "logs" / "decisions.jsonl").open("a") as f:
        f.write("".join(json.dumps(d) + "\n" for d in lines))
    text = diagnose(db=db, logs=tmp_path / "logs", tape_root=tmp_path / "tape", markets_json=tmp_path / "markets.json",
                    start_us=T0, end_us=T0 + 600 * S, base="QQQ")
    assert "Refused   181 orders by the bot's own checks (never sent to the venue)" in text
    assert "free_collateral ×180 (sell 180) · 09-25 20:01:40 → 09-25 20:05:59, refusing for 3m08s" in text
    assert "last: needs $20.50, free $12.00" in text and "SPY" not in text   # a 10 s break counts as 10 s
    assert "oi_cap ×1 (buy 1)" in text


def test_diagnose_replays_the_runs_setup_beside_the_run(tmp_path: Path) -> None:
    """--replay: the scout's backtest of the run's own setup on the same window, with the sizes the engine traded,
    under each fill model, beside what the run did (the gap between the backtest's assumptions and the run)."""
    from dataclasses import asdict

    from bot.core.diagnose import find_setup
    from bot.scout.sim import Risk

    db = _run(tmp_path)
    (tmp_path / "markets.json").write_text(json.dumps({"markets": [
        {"marketDisplayName": "QQQ-USD", "status": "ONLINE", "tickSize": "0.01", "stepSize": "0.001",
         "minOrderNotional": "5", "minOrderSize": "0.001"}]}))
    with (tmp_path / "logs" / "decisions.jsonl").open("a") as f:
        f.write(json.dumps({"ts": T0, "event": "resize", "market": "QQQ", "reason": "sized for $100",
                            "data": {"capital": 100, "order": 223.5, "cap": 447, "cap_off": 447, "pos_stop": 1,
                                     "daily_stop": 2, "kill": 10}}) + "\n")
    events = tmp_path / "pilot_events.jsonl"
    setup = {"market": "QQQ-USD", "setting": "touch 1bp", "leverage": 25.0, "risk": asdict(Risk.for_capital(28, 25))}
    events.write_text(json.dumps({"ts": T0 / S - 60, "kind": "deployed", "text": "x", "setup": setup}) + "\n")
    assert find_setup(events, "QQQ-USD", T0 + 600 * S) == setup and find_setup(events, "BTC-USD", T0 + 600 * S) is None
    text = diagnose(db=db, logs=tmp_path / "logs", tape_root=tmp_path / "tape", markets_json=tmp_path / "markets.json",
                    start_us=T0, end_us=T0 + 600 * S, setup=setup)
    assert "Replay    touch 1bp @ 25x: order $224, cap $447" in text and "(the engine's sizes)" in text
    assert "the run      volume $      223  fills    1" in text
    assert "backtest through" in text and "backtest queue (scan)" in text and "backtest front" in text
