"""Book rebuild: boundary gap, mid-stream gap, duplicate prices, and replay of LIVE frames."""

from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

from bot.core.book import ArcusBookSync, L2Book, SyncResult

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



# ------------------------------------------------------------------------------------------------ phantom levels
def test_a_delete_lost_in_the_boundary_gap_never_leaves_the_book_crossed() -> None:
    """Real frames, BTC-USD, 2026-09-25: the first delta came 56 sequences after the snapshot; the best bid (84028)
    was removed inside that gap, so an ask resting at 84028 later made the book read crossed half the time."""
    import json
    from pathlib import Path

    from bot.core.book import ArcusBookSync

    frames = json.loads((Path(__file__).parents[1] / "fixtures" / "live" / "arcus_btc_boundary_gap.json").read_text())
    s = ArcusBookSync()
    checked = 0
    for m in frames:
        c = m.get("contents") or {}
        if m["channel"] == "l2OrderbookUpdates":
            s.on_snapshot(c) if m["type"] == "subscribed" else s.on_delta(c)
            b, a = s.book.best_bid(), s.book.best_ask()
            assert not (b and a and b[0] >= a[0]), "crossed book"
        elif m["type"] == "channel_data" and c.get("lastSequenceId") == s.last_seq:
            s.on_bbo(c)
            b, a = s.book.best_bid(), s.book.best_ask()
            assert b is not None and a is not None
            assert (str(b[0]), str(a[0])) == (c["bestBid"]["price"], c["bestAsk"]["price"])   # the same top of book
            checked += 1
    assert s.boundary_gaps == 1 and s.phantoms >= 1 and checked > 50


def test_phantom_levels_are_dropped_by_a_resting_order_or_the_bbo() -> None:
    from bot.core.book import ArcusBookSync

    s = ArcusBookSync()
    s.on_snapshot({"bids": [["100", "1"], ["99.5", "1"], ["99", "1"]], "asks": [["101", "1"]], "lastSequenceId": 10})
    s.on_delta({"bids": [], "asks": [["100", "2"]], "lastSequenceId": 20})   # boundary gap; 100's delete was lost
    assert s.book.best_bid() == (D("99.5"), D("1")) and s.book.best_ask() == (D("100"), D("2"))
    s.on_bbo({"bestBid": {"price": "99"}, "bestAsk": {"price": "100"}, "lastSequenceId": 20})   # 99.5 is gone too
    assert s.book.best_bid() == (D("99"), D("1")) and s.phantoms == 2
    s.on_bbo({"bestBid": {"price": "98"}, "bestAsk": {"price": "100"}, "lastSequenceId": 19})   # another state:
    assert s.book.best_bid() == (D("99"), D("1"))                                               # nothing changes
