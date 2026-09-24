"""Book rebuild: boundary gap, mid-stream gap, duplicate prices, Lighter nonce chain, and replay of LIVE frames."""

from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

from bot.core.book import ArcusBookSync, L2Book, LighterBookSync, SyncResult

FIX = Path(__file__).parents[1] / "fixtures" / "live"


def snap(seq: int) -> dict:
    return {"bids": [["100.0", "1"], ["99.9", "2"]], "asks": [["100.1", "1"], ["100.2", "3"]], "lastSequenceId": seq,
            "timestamp": 1}


def test_arcus_boundary_gap_tolerated_then_mid_stream_gap_detected() -> None:
    s = ArcusBookSync()
    s.on_snapshot(snap(100))
    assert s.on_delta({"bids": [], "asks": [], "lastSequenceId": 99}) is SyncResult.STALE
    assert s.on_delta({"bids": [["100.0", "0"]], "asks": [], "lastSequenceId": 104}) is SyncResult.APPLIED
    assert s.boundary_gaps == 1
    assert s.on_delta({"bids": [], "asks": [["100.1", "5"]], "lastSequenceId": 105}) is SyncResult.APPLIED
    assert s.on_delta({"bids": [], "asks": [], "lastSequenceId": 107}) is SyncResult.GAP
    assert s.book.best_bid() == (D("99.9"), D("2"))
    assert s.book.best_ask() == (D("100.1"), D("5"))


def test_duplicate_prices_last_write_wins() -> None:
    s = ArcusBookSync()
    s.on_snapshot(snap(1))
    s.on_delta({"bids": [["99.8", "1"], ["99.8", "0"], ["99.8", "7"]], "asks": [], "lastSequenceId": 2})
    assert s.book.size_at(True, D("99.8")) == D("7")


def test_lighter_nonce_chain() -> None:
    s = LighterBookSync()
    assert s.on_delta({"bids": [], "asks": [], "nonce": 5, "begin_nonce": 4}) is SyncResult.NOT_READY
    s.on_snapshot({"bids": [{"price": "10", "size": "1"}], "asks": [{"price": "11", "size": "1"}], "nonce": 50})
    assert s.on_delta({"bids": [{"price": "10", "size": "0"}], "asks": [], "nonce": 60, "begin_nonce": 50}) is SyncResult.APPLIED
    assert s.book.best_bid() is None
    assert s.on_delta({"bids": [], "asks": [], "nonce": 70, "begin_nonce": 61}) is SyncResult.GAP


def test_book_reads() -> None:
    b = L2Book()
    b.load([(D("100"), D("2")), (D("99"), D("5"))], [(D("101"), D("1")), (D("102"), D("4"))], 1)
    assert b.mid() == D("100.5") and b.spread() == D("1")
    assert b.microprice() == (D("100") * 1 + D("101") * 2) / 3
    assert b.levels(True, 1) == [(D("100"), D("2"))]
    assert b.depth_to_fill(False, D("300")) == D("102")
    seen = []
    b.listener = lambda *a: seen.append(a)
    b.set_level(True, D("100"), D("1"))
    assert seen == [(True, D("100"), D("2"), D("1"))]


def test_replay_live_arcus_frames_no_midstream_gap() -> None:
    frames = json.loads((FIX / "arcus_ws_frames.json").read_text())
    syncs: dict[str, ArcusBookSync] = {}
    applied = 0
    for f in frames:
        r = f["raw"]
        if r.get("channel") != "l2OrderbookUpdates":
            continue
        s = syncs.setdefault(r["id"], ArcusBookSync())
        if r["type"] == "subscribed":
            s.on_snapshot(r["contents"])
        else:
            res = s.on_delta(r["contents"])
            assert res is not SyncResult.GAP
            applied += res is SyncResult.APPLIED
    assert applied > 100
    for s in syncs.values():
        assert not s.book.crossed()


def test_replay_live_lighter_frames_no_gap() -> None:
    frames = json.loads((FIX / "lighter_ws_frames.json").read_text())
    syncs: dict[str, LighterBookSync] = {}
    for f in frames:
        r = f["raw"]
        ch = str(r.get("channel", ""))
        if not ch.startswith("order_book:"):
            continue
        s = syncs.setdefault(ch, LighterBookSync())
        if str(r["type"]).startswith("subscribed"):
            s.on_snapshot(r["order_book"])
        else:
            assert s.on_delta(r["order_book"]) is SyncResult.APPLIED
    assert syncs and all(not s.book.crossed() for s in syncs.values())
