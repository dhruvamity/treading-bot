"""Mid-run, a position the venue's REST snapshot disagrees with is adopted only once the same difference has lasted
POSITION_CONFIRM_S: a snapshot taken while fills arrive can be older than the bot's own record (2026-09-28 07:40 and
2026-09-29 04:22 UTC: the bot took a stale SPY position and traded on it for 5 minutes)."""

from __future__ import annotations

import time
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bot.core.state as state_mod
from bot.core.alerts import Alerter
from bot.core.runner import BotRunner
from bot.core.state import POSITION_CONFIRM_S, StateStore
from bot.venues.base import Fill, Position, Side, Venue

A = Venue.ARCUS
WAIT = POSITION_CONFIRM_S + 1


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.t = 1_790_000_000_000_000
        self.n = 0
        monkeypatch.setattr(state_mod, "now_us", lambda: self.t)

    def wait(self, s: float) -> None:
        self.t += int(s * 1e6)

    def fill(self, st: StateStore, side: Side, size: str) -> None:
        self.n += 1
        st.on_fill(Fill(A, "SPY", f"c{self.n}", side, D("655"), D(size), D(0), True, False, self.t, f"t{self.n}"))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


def short(tmp_path: Path, clock: Clock, size: str) -> StateStore:
    st = StateStore(tmp_path / "live.sqlite")
    clock.fill(st, Side.SELL, size)
    return st


def spy(size: str) -> list[Position]:
    return [Position(A, "SPY", D(size), D("655"), D("655"), D(0), "cross", None)] if D(size) else []


def mid_run(st: StateStore, size: str) -> state_mod.ReconcileReport:
    return st.reconcile(A, [], spy(size), confirm_s=POSITION_CONFIRM_S)


def test_a_stale_snapshot_is_not_adopted(tmp_path: Path, clock: Clock) -> None:
    st = short(tmp_path, clock, "6.053")
    rep = mid_run(st, "-4.748")                              # read before the last fill reached REST
    assert rep.deferred == [("SPY", D("-6.053"), D("-4.748"))] and not rep.position_mismatch
    assert st.position(A, "SPY") == D("-6.053")              # the bot's own record stands
    clock.wait(WAIT)
    rep = mid_run(st, "-6.053")                              # REST caught up
    assert rep.clean and st.position(A, "SPY") == D("-6.053")
    clock.wait(WAIT)
    assert mid_run(st, "-5").deferred                        # a later difference starts its own wait


def test_a_lasting_difference_is_adopted_while_trading_goes_on(tmp_path: Path, clock: Clock) -> None:
    st = short(tmp_path, clock, "6.053")
    assert mid_run(st, "-4.748").deferred                    # the bot missed a 1.305 buy
    clock.wait(10)
    clock.fill(st, Side.SELL, "1")
    assert mid_run(st, "-5.748").deferred                    # the same 1.305, too soon
    clock.wait(6)
    clock.fill(st, Side.BUY, "0.5")
    rep = mid_run(st, "-5.248")                              # still 1.305, 16 s on: the venue is right
    assert rep.position_mismatch == [("SPY", D("-6.553"), D("-5.248"))] and not rep.deferred
    assert st.position(A, "SPY") == D("-5.248")


def test_a_changing_difference_waits_again(tmp_path: Path, clock: Clock) -> None:
    st = short(tmp_path, clock, "6.053")
    assert mid_run(st, "-4.748").deferred
    clock.wait(WAIT)
    assert mid_run(st, "-5.553").deferred                    # a different difference: a different stale read
    clock.wait(WAIT)
    rep = mid_run(st, "-5.553")
    assert rep.position_mismatch == [("SPY", D("-6.053"), D("-5.553"))]


def test_a_start_adopts_at_once(tmp_path: Path, clock: Clock) -> None:
    st = short(tmp_path, clock, "6.053")
    rep = st.reconcile(A, [], spy("-4.748"))
    assert rep.position_mismatch == [("SPY", D("-6.053"), D("-4.748"))] and not rep.deferred
    assert st.position(A, "SPY") == D("-4.748")


class FakeVenue:
    def __init__(self, size: str) -> None:
        self.size = size

    async def open_orders(self) -> list[Any]:
        return []

    async def positions(self) -> list[Position]:
        return spy(self.size)


async def test_the_runner_looks_again_soon_and_alerts_only_on_adopting(tmp_path: Path, clock: Clock) -> None:
    st = short(tmp_path, clock, "6.053")
    runner: Any = SimpleNamespace(state=st, alerter=Alerter(), adapters={A: FakeVenue("-4.748")}, _recheck_at=0.0)
    runner._position_alerts = lambda v, rep, start: BotRunner._position_alerts(runner, v, rep, start)
    await BotRunner.reconcile_once(runner)
    assert runner.alerter.sent == []
    assert runner._recheck_at > time.monotonic() + POSITION_CONFIRM_S - 1     # an early reconcile is due
    clock.wait(WAIT)
    await BotRunner.reconcile_once(runner)
    assert [k for _, k, _ in runner.alerter.sent] == ["reconcile"]
    assert st.position(A, "SPY") == D("-4.748")
