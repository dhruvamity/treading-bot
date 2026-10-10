"""`arbitrage livetest --what engine` against two simulated venues (no network, no key): both directions, both
kinds of exit, what it reports, that it touches nothing on an account that holds something, and that it ends flat."""

from __future__ import annotations

from typing import Any

from arbitrage.exec import legtest as lt
from arbitrage.exec.drill import Drill
from arbitrage.exec.venue import SimVenue, Spec

SYM = "SPY"


class Ground:
    """What a venue itself says (the test's stand-in for the REST reads of legtest.LegTest)."""

    def __init__(self, v: SimVenue) -> None:
        self.v = v

    def step_size(self) -> float:
        return self.v.spec.step

    async def account(self) -> tuple[float, float]:
        return float(await self.v.free_collateral() or 0.0), self.v.pos.get(SYM, 0.0)

    async def listed(self) -> list[dict[str, Any]]:
        rows = [{"id": o.id} for o in self.v.orders.values() if o.open]
        return rows + ([{"id": "stop"}, {"id": "take"}] if SYM in self.v.stops else [])


def make(*, boom: Any = None) -> tuple[Drill, dict[str, SimVenue], list[str], dict[str, float]]:
    arcus = SimVenue("arcus", Spec(0.01, 0.0001, 0.0001, 5.0, 2.25), 50.0)
    lighter = SimVenue("lighter", Spec(0.01, 0.001, 0.001, 10.0, 0.0), 50.0)
    venues = {"arcus": arcus, "lighter": lighter}
    for v in venues.values():
        v.set_top(SYM, 100.00, 100.02)
    clock = {"t": 1_791_600_000.0}
    said: list[str] = []

    async def sleep(s: float) -> None:
        clock["t"] += s
        for v in venues.values():            # every resting maker order is hit
            for oid, o in list(v.orders.items()):
                if o.open:
                    v.fill(oid)
        if boom:
            boom(venues)

    d = Drill(venues, {n: Ground(v) for n, v in venues.items()}, SYM, say=said.append, hold_s=10.0,  # type: ignore[arg-type]
              sleep=sleep, clock=lambda: clock["t"])
    return d, venues, said, clock


async def test_both_directions_and_both_exits_pass_and_it_ends_flat() -> None:
    d, venues, _said, _clock = make()
    rep = await d.run()
    r = {a: b for a, b, _ in rep.steps}
    assert not rep.failed, rep.failed
    for name in ("entry (long arcus / short lighter)", "stops (long arcus / short lighter)",
                 "hold (long arcus / short lighter)", "exit maker first (long arcus / short lighter)",
                 "entry (long lighter / short arcus)", "exit with taker orders (long lighter / short arcus)", "end"):
        assert r[name] == lt.PASS, (name, rep.steps)
    assert rep.clean and all(v.pos.get(SYM, 0) == 0 for v in venues.values())
    sizes = {s[2] for v in venues.values() for s in v.sent if s[0] == "maker"}
    assert d.size * 100 < 20, "one leg is a minimum order, not more"        # $12 on Lighter's $10 minimum
    assert sizes and "Ended flat with no orders." in rep.text() and "two-leg engine" in rep.text()
    assert any(s[0] == "taker" and s[5] for v in venues.values() for s in v.sent), "the --now exit used taker orders"


async def test_it_sends_nothing_when_an_account_already_holds_something() -> None:
    d, venues, _said, _clock = make()
    venues["lighter"].pos[SYM] = 0.5
    rep = await d.run()
    assert "Nothing was sent" in rep.aborted and rep.clean
    assert not any(v.sent for v in venues.values()) and venues["lighter"].pos[SYM] == 0.5


async def test_a_venue_that_refuses_the_stops_is_a_failure_and_the_position_is_still_closed() -> None:
    d, venues, _said, _clock = make()
    venues["lighter"].fail_stops = True
    rep = await d.run()
    stops = [s for s in rep.steps if s[0].startswith("stops")]
    assert stops and all(s[1] == lt.FAIL for s in stops)
    assert rep.clean and all(v.pos.get(SYM, 0) == 0 for v in venues.values())


async def test_it_stops_and_closes_when_the_accounts_lose_more_than_the_limit() -> None:
    hit = {"n": 0}

    def boom(venues: dict[str, SimVenue]) -> None:
        if venues["lighter"].pos.get(SYM, 0) and not hit["n"]:
            hit["n"] = 1
            venues["lighter"].collateral -= 5.0

    d, venues, _said, _clock = make(boom=boom)
    rep = await d.run()
    assert "more than the $1.00" in rep.aborted
    assert rep.clean and all(v.pos.get(SYM, 0) == 0 for v in venues.values())


def test_the_livetest_command_is_wired_and_refuses_without_the_switch_or_a_terminal(
        monkeypatch: Any, capsys: Any) -> None:
    import io

    from arbitrage import cli

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setenv("ARB_NO_SPAWN", "1")
    code = None
    try:
        cli.main(["livetest", "SPY", "--what", "engine"])
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out
    assert code == 1 and ("LIVE is off" in out or "not started" in out), out
