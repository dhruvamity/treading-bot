"""WebSocket resilience: an unavailable market's empty snapshot, a handler error, and Arcus `degraded` frames must never
drop a connection or leave the bot trading off a stale book (seen live 2026-09-25: KBONK-USD went OFFLINE and its
empty `l2OrderbookUpdates` snapshot put the scout's connection into a reconnect loop)."""

from __future__ import annotations

import asyncio
import types
from typing import Any

from bot.core.book import ArcusBookSync, LighterBookSync, SyncResult
from bot.venues.arcus import ws as arcus_ws
from bot.venues.arcus.adapter import ArcusAdapter
from bot.venues.arcus.ws import ArcusWS
from bot.venues.ws_base import ReconnectingWS
from tests.unit.test_clients_offline import FakeServer, server  # noqa: F401  (fixture)

SNAP = {"bids": [["100", "1"]], "asks": [["101", "2"]], "lastSequenceId": 7, "timestamp": 1}


def test_an_empty_snapshot_leaves_the_book_empty_and_not_ready() -> None:
    s = ArcusBookSync()
    assert s.on_snapshot(SNAP) is SyncResult.APPLIED and s.book.best_bid() is not None
    assert s.on_snapshot({}) is SyncResult.NOT_READY               # the market went offline
    assert s.book.best_bid() is None and s.book.best_ask() is None
    assert s.on_delta({"lastSequenceId": 8, "bids": [["99", "1"]]}) is SyncResult.NOT_READY
    assert s.on_snapshot(SNAP) is SyncResult.APPLIED               # trading again
    lt = LighterBookSync()
    assert lt.on_snapshot({}) is SyncResult.NOT_READY and lt.on_delta({"nonce": 5}) is SyncResult.NOT_READY


async def test_arcus_ws_survives_the_frames_an_offline_market_sends() -> None:
    aw = ArcusWS("wss://unused")
    books: list[SyncResult] = []
    errors: list[dict[str, Any]] = []
    aw.on("book", lambda base, sync, res, *a: books.append(res))
    aw.on("error", lambda m, r: errors.append(m))
    await aw._on_message({"type": "subscribed", "channel": "l2OrderbookUpdates", "id": "KBONK-USD", "contents": {}}, 1)
    for _ in range(3):
        await aw._on_message({"type": "error", "message": "Market 'KBONK-USD' is not available"}, 2)
    assert books == [SyncResult.NOT_READY] and len(errors) == 3
    assert list(aw._errors_logged) == ["Market 'KBONK-USD' is not available"]   # logged once, not three times


async def test_a_handler_error_skips_the_frame_and_keeps_the_connection(server: FakeServer) -> None:  # noqa: F811
    got: list[int] = []

    def handler(m: dict[str, Any], recv: int) -> None:
        if m.get("bad"):
            raise KeyError("lastSequenceId")
        got.append(m["v"])

    server.ws_script = [{"v": 1}, {"bad": True}, {"v": 2}, {"bad": True}, {"v": 3}]
    ws = ReconnectingWS("t", server.url.replace("http", "ws") + "/ws", handler)
    ws.start()
    assert await ws.wait_connected(5)
    await asyncio.sleep(0.3)
    assert got == [1, 2, 3] and ws.handler_errors == 2 and ws.reconnects == 0
    await ws.stop()


class _FakeWS:
    def __init__(self) -> None:
        self.resubs: list[tuple[str, dict[str, Any] | None]] = []

    async def resubscribe(self, key: str, unsub: dict[str, Any] | None = None) -> None:
        self.resubs.append((key, unsub))

    def touch(self, key: str, ts_us: int) -> None:
        pass

    async def stop(self) -> None:
        pass


async def test_a_degraded_book_is_dropped_and_resubscribed_without_a_storm(monkeypatch: Any) -> None:
    monkeypatch.setattr(arcus_ws, "DEGRADED_RESUB_S", 0.2)
    aw = ArcusWS("wss://unused")
    fake = _FakeWS()
    aw.ws = fake  # type: ignore[assignment]
    seen: list[str] = []
    aw.on("degraded", lambda ch, sid, m, r: seen.append(f"{ch}:{sid}"))
    await aw._on_message({"type": "subscribed", "channel": "l2OrderbookUpdates", "id": "BTC-USD", "contents": SNAP}, 1)
    assert aw.books["BTC-USD"].book.best_bid() is not None
    deg = {"type": "degraded", "channel": "l2OrderbookUpdates", "id": "BTC-USD", "reason": "snapshot_stale"}
    await aw._on_message(deg, 2)
    assert aw.books["BTC-USD"].book.best_bid() is None             # never quote off a stale book
    assert fake.resubs == [("l2u:BTC-USD", {"type": "unsubscribe", "channel": "l2OrderbookUpdates", "id": "BTC-USD"})]
    for _ in range(5):                                              # a burst: one delayed resubscribe, not five
        await aw._on_message(deg, 3)
    assert len(fake.resubs) == 1
    await asyncio.sleep(0.3)
    assert len(fake.resubs) == 2 and seen == ["l2OrderbookUpdates:BTC-USD"] * 6
    await aw.stop()


def test_degraded_frames_map_to_the_subscription_they_came_from() -> None:
    k = ArcusWS.stream_key
    assert k("bbo", "QQQ-USD") == ("bbo:QQQ-USD", {"type": "unsubscribe", "channel": "bbo", "id": "QQQ-USD"})
    assert k("markets", "") == ("markets", {"type": "unsubscribe", "channel": "markets"})
    assert k("orders", "0xABcd", 2) == ("orders:0xabcd:2", {"type": "unsubscribe", "channel": "orders",
                                                             "id": "0xABcd", "accountIndex": 2})
    assert k("somethingNew", "x") is None


def test_a_degraded_account_stream_asks_for_a_reconciliation() -> None:
    ad = types.SimpleNamespace(resync_requested=False)
    ArcusAdapter._on_degraded(ad, "bbo", "BTC-USD", {}, 0)  # type: ignore[arg-type]
    assert ad.resync_requested is False
    ArcusAdapter._on_degraded(ad, "orders", "0xabc", {}, 0)  # type: ignore[arg-type]
    assert ad.resync_requested is True
