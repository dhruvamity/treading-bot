"""The start-up position check says what differed, in words: a /closeall while the bot was stopped is not a mismatch
(2026-09-27: /stop, then /closeall taker, then /run raised "POSITION MISMATCH [('SPY', -0.355, 0)]")."""

from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from arcus.common.time import now_us
from arcus.core.alerts import Alerter, Level
from arcus.core.runner import BotRunner
from arcus.core.state import CLOSEALL_KEY, StateStore, closeall_us, note_closeall, side_size
from arcus.telegram.control import Control
from arcus.venues.base import Fill, Position, Side, Venue
from tests.unit.test_telegram import _app

A = Venue.ARCUS


def short_spy(db: Path, minutes_ago: float = 5) -> StateStore:
    """The bot's record after its last run: short 0.355309 SPY."""
    st = StateStore(db)
    st.on_fill(Fill(A, "SPY", "c1", Side.SELL, D("655.10"), D("0.3553090"), D(0), True, False,
                    now_us() - int(minutes_ago * 60e6), "t1"))
    return st


def pos(size: str) -> Position:
    return Position(A, "SPY", D(size), D("655"), D("655"), D(0), "cross", None)


async def check(st: StateStore, venue_positions: list[Position], start: bool) -> list[tuple[Level, str, str]]:
    runner: Any = SimpleNamespace(state=st, alerter=Alerter())
    rep = st.reconcile(A, [], venue_positions)
    await BotRunner._position_alerts(runner, A, rep, start)
    return runner.alerter.sent


def test_sizes_read_as_words() -> None:
    assert side_size(D("-0.3553090")) == "short 0.355309"
    assert side_size(D("0.10")) == "long 0.1" and side_size(D(0)) == "flat" and side_size(D("-0E-7")) == "flat"
    assert side_size(D("12E+1")) == "long 120"


async def test_a_closeall_while_stopped_is_only_logged(tmp_path: Path) -> None:
    st = short_spy(tmp_path / "live.sqlite")
    assert note_closeall(tmp_path / "live.sqlite", "arcus")
    sent = await check(st, [], start=True)
    assert sent == [(Level.INFO, "reconcile_closeall",
                     "ℹ️ CLOSED BY /CLOSEALL\nSPY: bot's record short 0.355309 · Arcus flat\nStarting flat")]
    assert st.position(A, "SPY") == 0                        # the venue's 0 is used, as before


async def test_a_position_closed_elsewhere_is_said_plainly(tmp_path: Path) -> None:
    st = short_spy(tmp_path / "live.sqlite")
    (level, key, text), = await check(st, [], start=True)
    assert level is Level.WARN and key == "reconcile_stopped"
    assert text.startswith("⚠️ CLOSED WHILE STOPPED\nSPY: bot's record short 0.355309 · Arcus flat")
    assert "Not by /closeall" in text and "MISMATCH" not in text


async def test_an_older_closeall_does_not_explain_a_later_position(tmp_path: Path) -> None:
    db = tmp_path / "live.sqlite"
    StateStore(db).close()
    note_closeall(db, "arcus")
    st = short_spy(db, minutes_ago=-1)                       # the bot traded after that /closeall
    assert [k for _, k, _ in await check(st, [], start=True)] == ["reconcile_stopped"]


async def test_a_real_mismatch_names_both_sides(tmp_path: Path) -> None:
    st = short_spy(tmp_path / "live.sqlite")
    note_closeall(tmp_path / "live.sqlite", "arcus")
    (level, key, text), = await check(st, [pos("0.1")], start=True)   # Arcus holds a position the bot does not know
    assert (level, key) == (Level.WARN, "reconcile")
    assert text == "⚠️ POSITION MISMATCH\nSPY: bot's record short 0.355309 · Arcus long 0.1\n\nArcus's position is used"
    st2 = short_spy(tmp_path / "b.sqlite")
    (_, key, text), = await check(st2, [], start=False)      # flat mid-run: not a stopped-bot case
    assert key == "reconcile" and "bot's record short 0.355309 · Arcus flat" in text


def test_the_closeall_record_is_read_per_venue(tmp_path: Path) -> None:
    assert not note_closeall(tmp_path / "never.sqlite", "arcus")        # that mode never ran: nothing to record
    assert not (tmp_path / "never.sqlite").exists()
    st = StateStore(tmp_path / "live.sqlite")
    assert note_closeall(tmp_path / "live.sqlite", "arcus")
    raw = st.kv_get(CLOSEALL_KEY)
    assert closeall_us(raw, A) is not None and abs(closeall_us(raw, A) - now_us()) < 60e6  # type: ignore[operator]
    assert closeall_us(None, A) is None and closeall_us("{", A) is None and closeall_us('{"venue":"x"}', A) is None


@pytest.mark.parametrize("orders", [1, 0])
async def test_telegram_closeall_records_it_for_the_next_start(tmp_path: Path, monkeypatch: Any, orders: int) -> None:
    import arcus.cli

    ctl = Control(_app(tmp_path), root=tmp_path)
    StateStore(ctl.db_path("live")).close()

    async def flatten(venue: str, account: Any, mainnet: bool, taker: bool) -> dict[str, Any]:
        assert (venue, mainnet, taker) == ("arcus", True, True)
        return {"venue": venue, "positions": orders, "orders": orders, "open": 0, "taker": taker}

    monkeypatch.setattr(arcus.cli, "venue_flatten", flatten)
    await ctl.flatten("live", "arcus", True)
    assert (closeall_us(ctl._kv_get("live", CLOSEALL_KEY), A) is not None) is bool(orders)
