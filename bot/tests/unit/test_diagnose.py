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
