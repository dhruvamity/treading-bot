"""The executor on two simulated venues: entries, one leg ahead of the other, stops, the holding rules, outages and
restarts. No network, no real venue."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

import pytest

from arbitrage.exec import engine as eng
from arbitrage.exec.engine import Engine, State
from arbitrage.exec.venue import BUY, SELL, SimVenue, Spec, VenueDown
from arbitrage.rank import Plan, Settings, plan
from tests.test_arbitrage import legs

SYM = "BABA"


class Clock:
    def __init__(self) -> None:
        self.t = 1_791_000_000.0

    def __call__(self) -> float:
        return self.t


class Store:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []
        self.cmd: dict[str, Any] = {}
        self.events: list[tuple[str, str]] = []

    def save(self, st: State) -> None:
        self.saved.append(asdict(st))

    def commands(self) -> dict[str, Any]:
        c, self.cmd = self.cmd, {}
        return c

    def event(self, kind: str, text: str, **data: Any) -> None:
        self.events.append((kind, text))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


class Planner:
    def __init__(self) -> None:
        self.go = True
        self.edge = (1.2e-5, 1.2e-5)          # next payment, last 24 h: paying
        self.asked = 0
        self.settings: Any = Settings

    async def best(self, collateral: dict[str, float]) -> Plan | None:
        self.asked += 1
        if not self.go:
            return None
        a, b = legs()
        return plan(a, b, [1.7e-5] * 168, [4e-6] * 168, 0.0167, collateral, self.settings())

    async def edges(self, symbol: str, short_venue: str) -> tuple[float, float] | None:
        return self.edge


class Rig:
    def __init__(self, settings: Settings | None = None, **kw: Any) -> None:
        self.clock = Clock()
        self.arcus = SimVenue("arcus", Spec(0.01, 1e-7, 0.01, 5.0, taker_bp=2.25), 120.0)
        self.lighter = SimVenue("lighter", Spec(0.01, 1e-4, 0.04, 10.0), 120.0)
        for v in (self.arcus, self.lighter):
            v.set_top(SYM, 106.40, 106.42)
        self.store, self.planner = Store(), Planner()
        self.s = settings or Settings()
        self.planner.settings = lambda: self.s
        self.e = Engine({"arcus": self.arcus, "lighter": self.lighter}, self.planner, lambda: self.s, self.store,
                        clock=self.clock, **kw)

    async def step(self, dt: float = 1.0, n: int = 1) -> None:
        for _ in range(n):
            self.clock.t += dt
            await self.e.step()

    def open_orders(self, v: SimVenue) -> list[Any]:
        return [o for o in v.orders.values() if o.open]

    async def to_open(self) -> None:
        await self.step()                                  # plan, leverage, legs
        await self.step()                                  # the two maker orders
        for v in (self.arcus, self.lighter):
            for o in self.open_orders(v):
                v.fill(o.id)
        await self.step()                                  # counted, positions checked: open
        assert self.e.st.phase == "open", self.store.events
        await self.step()                                  # the venues' stops


async def test_an_entry_posts_both_legs_as_maker_and_opens_with_stops_on_both_venues() -> None:
    r = Rig()
    await r.step()
    st = r.e.st
    assert (st.phase, st.long_venue, st.short_venue) == ("entering", "lighter", "arcus")     # short the higher rate
    assert r.arcus.leverage[SYM] == r.lighter.leverage[SYM] == pytest.approx(st.leverage)
    await r.step()
    (buy,), (sell,) = r.open_orders(r.lighter), r.open_orders(r.arcus)
    assert (buy.side, buy.price, sell.side, sell.price) == (BUY, 106.40, SELL, 106.42)       # resting at the best price
    assert buy.size == sell.size == st.size and st.size == pytest.approx(round(st.size, 4))  # equal, on Lighter's step
    assert all(kind == "maker" and not ro for kind, *_x, ro in r.arcus.sent + r.lighter.sent)
    r.lighter.fill(buy.id)
    r.arcus.fill(sell.id)
    await r.step()
    assert r.e.st.phase == "open" and r.e.st.entry == {"lighter": 106.40, "arcus": 106.42}
    assert r.arcus.pos[SYM] == -r.lighter.pos[SYM] == -st.size                               # delta neutral
    await r.step()
    d = r.e.st.stop_dist
    assert r.lighter.stops[SYM] == pytest.approx((st.size, 106.40 * (1 - d), 106.40 * (1 + d)))
    assert r.arcus.stops[SYM] == pytest.approx((-st.size, 106.42 * (1 + d), 106.42 * (1 - d)))
    assert "stops" in r.store.kinds() and r.store.saved[-1]["phase"] == "open"


async def test_a_leg_left_behind_follows_the_price_then_crosses_the_spread() -> None:
    r = Rig()
    await r.step(n=2)
    size = r.e.st.size
    r.lighter.fill(r.open_orders(r.lighter)[0].id)         # Lighter is in, Arcus is not
    r.arcus.set_top(SYM, 106.45, 106.47)                   # ... and the price moves away from the Arcus order
    await r.step(dt=4)                                     # past requote_s: the stale order is cancelled
    assert not r.open_orders(r.arcus)
    await r.step()                                         # and posted again at the new best ask, still as maker
    (o,) = r.open_orders(r.arcus)
    assert o.price == 106.47 and [k for k, *_ in r.arcus.sent] == ["maker", "maker"]
    await r.step(dt=20)                                    # chase_s is over: its maker order goes first ...
    assert not r.open_orders(r.arcus)
    await r.step()                                         # ... then it crosses for what is missing
    kind, _sym, side, qty, worst, ro = r.arcus.sent[-1]
    assert (kind, side, qty, ro) == ("taker", SELL, pytest.approx(size), False) and worst < 106.45
    await r.step()
    assert r.e.st.phase == "open" and r.arcus.pos[SYM] == pytest.approx(-size)
    assert r.e.st.entry["arcus"] == pytest.approx(106.45)  # sold at the bid
    assert "cross" in r.store.kinds()


async def test_a_cross_that_costs_too_much_waits_and_is_done_anyway_at_the_timeout() -> None:
    r = Rig(Settings(max_cross_bp=3.0, enter_timeout_s=60))
    await r.step(n=2)
    r.lighter.fill(r.open_orders(r.lighter)[0].id)
    r.arcus.set_top(SYM, 106.30, 106.50)                   # 18.8 bp wide: half of it plus 2.25 bp is far over 3 bp
    await r.step(dt=25)
    await r.step(n=3)
    assert all(k == "maker" for k, *_ in r.arcus.sent) and r.e.st.phase == "entering"        # still only following
    await r.step(dt=40)                                    # the timeout: equal legs come before cost
    await r.step(n=3)
    assert r.arcus.sent[-1][0] == "taker" and r.e.st.phase == "open"
    assert r.arcus.pos[SYM] == pytest.approx(-r.lighter.pos[SYM])


async def test_an_entry_nobody_fills_ends_flat_with_nothing_left_on_the_book() -> None:
    r = Rig(Settings(enter_timeout_s=30))
    await r.step(n=2)
    await r.step(dt=31)
    await r.step(n=2)
    assert r.e.st.phase == "flat" and not r.open_orders(r.arcus) and not r.open_orders(r.lighter)
    assert r.arcus.pos.get(SYM, 0) == 0 == r.lighter.pos.get(SYM, 0) and "not_opened" in r.store.kinds()


async def test_a_maker_order_is_replaced_before_it_would_expire_on_a_venue_where_orders_expire() -> None:
    r = Rig(Settings(enter_timeout_s=900))
    r.lighter.maker_life_s = 210.0             # Lighter: an order drops off by itself 5.5 minutes after it is placed
    await r.step(n=2)
    (a0,), (l0,) = r.open_orders(r.arcus), r.open_orders(r.lighter)
    await r.step(dt=100)
    assert r.open_orders(r.lighter) == [l0]                               # the price has not moved: it rests
    await r.step(dt=111)                                                  # 211 s old
    await r.step(n=2)
    (l1,) = r.open_orders(r.lighter)
    assert l1.id != l0.id and l1.price == l0.price and r.open_orders(r.arcus) == [a0]    # Arcus cancels by itself


async def test_a_venues_own_stop_firing_closes_the_other_leg_at_once() -> None:
    r = Rig()
    await r.to_open()
    r.lighter.trigger_stop(SYM)                            # Lighter closed its leg; Arcus is now a naked short
    await r.step()
    assert r.e.st.phase == "open"                          # one reading is not enough (a stale position looks the same)
    await r.step(dt=eng.CONFIRM_S)
    assert r.e.st.phase == "exiting" and r.e.st.urgent
    await r.step(n=3)
    kind, _sym, side, _qty, _worst, ro = r.arcus.sent[-1]
    assert (kind, side, ro) == ("taker", BUY, True)        # bought back, reduce-only
    assert r.e.st.phase == "flat" and r.arcus.pos[SYM] == 0 and SYM not in r.arcus.stops
    assert any(k == "closed" and "its stop fired" in t for k, t in r.store.events)


async def test_the_bots_own_stop_closes_both_legs_with_taker_orders() -> None:
    r = Rig()
    await r.to_open()
    d = r.e.st.stop_dist
    px = 106.41 * (1 + d) * 1.001
    for v in (r.arcus, r.lighter):
        v.set_top(SYM, px - 0.01, px + 0.01)
    await r.step()
    assert r.e.st.phase == "exiting" and r.e.st.urgent and "stop:" in r.e.st.why
    await r.step(n=3)
    assert r.e.st.phase == "flat" and r.arcus.pos[SYM] == 0 == r.lighter.pos[SYM]
    assert r.arcus.sent[-1][0] == r.lighter.sent[-1][0] == "taker" and not r.arcus.stops and not r.lighter.stops


async def test_holding_rules_close_it_as_maker_and_new_settings_apply_to_the_open_position() -> None:
    r = Rig()
    await r.to_open()
    r.planner.edge = (-1e-6, -1e-6)                        # the difference turned against it ...
    await r.step(dt=3600)
    assert r.e.st.phase == "open"                          # ... but min_hold_h (24) is not over
    r.s = replace(r.s, min_hold_h=0.0)                     # `arbitrage set min_hold_h 0`
    await r.step(dt=eng.RULE_EVERY_S)
    assert r.e.st.phase == "exiting" and not r.e.st.urgent and "difference is gone" in r.e.st.why
    await r.step(n=2)
    (sell,), (buy,) = r.open_orders(r.lighter), r.open_orders(r.arcus)
    assert (sell.side, buy.side) == (SELL, BUY)
    assert r.lighter.sent[-1][-1] and r.arcus.sent[-1][-1]             # both reduce-only
    assert not r.lighter.stops and not r.arcus.stops       # the venues' stops were cancelled first
    r.lighter.fill(sell.id)
    r.arcus.fill(buy.id)
    await r.step(n=2)
    assert r.e.st.phase == "flat" and "closed" in r.store.kinds()

    r2 = Rig()
    await r2.to_open()
    r2.s = replace(r2.s, max_hold_h=2.0)                   # `arbitrage set max_hold_h 2` while it is open
    await r2.step(dt=3 * 3600)
    assert r2.e.st.phase == "exiting" and "limit set is 2 h" in r2.e.st.why


async def test_a_new_time_limit_acts_in_the_next_loop_even_when_the_funding_cannot_be_read() -> None:
    r = Rig()
    await r.to_open()
    await r.step(dt=3 * 3600)
    assert r.e.st.phase == "open"

    async def no_edges(symbol: str, short_venue: str) -> None:      # the scan is failing
        return None
    r.planner.edges = no_edges                             # type: ignore[method-assign,assignment]
    r.s = replace(r.s, max_hold_h=2.0)                     # `arbitrage set max_hold_h 2`, 3 h into the position
    await r.step(dt=1.0)                                   # one loop later, not RULE_EVERY_S later
    assert r.e.st.phase == "exiting" and not r.e.st.urgent and "limit set is 2 h" in r.e.st.why


async def test_the_owners_stop_replaces_the_dynamic_one_on_both_venues() -> None:
    r = Rig()
    await r.to_open()
    dynamic = r.e.st.stop_dist
    r.s = replace(r.s, stop_pct=1.0)                       # `arbitrage set stop_pct 1`
    await r.step(n=2)
    assert r.e.st.stop_dist == pytest.approx(0.01) != dynamic and "stop_moved" in r.store.kinds()
    assert r.lighter.stops[SYM][1] == pytest.approx(106.40 * 0.99)
    assert r.arcus.stops[SYM][1] == pytest.approx(106.42 * 1.01)
    r.s = replace(r.s, stop_pct=40.0)                      # wider than liquidation allows: capped at 80% of the way
    await r.step(n=2)
    assert r.e.st.stop_dist == pytest.approx(0.8 * r.e.st.liq_dist)
    r.s = replace(r.s, stop_pct=0.0)                       # back to dynamic
    await r.step(n=2)
    assert r.e.st.stop_dist == pytest.approx(dynamic)


async def test_commands_close_pause_and_resume() -> None:
    r = Rig()
    r.store.cmd = {"pause": True}
    await r.step(dt=400, n=2)
    assert r.e.st.phase == "flat" and r.planner.asked == 0                    # paused: it does not even look
    r.store.cmd = {"pause": False}
    await r.to_open()
    r.store.cmd = {"close": True}
    await r.step()
    assert r.e.st.phase == "exiting" and not r.e.st.urgent and r.e.st.why == "closed by command"
    r.store.cmd = {"close": True, "now": True}             # `arbitrage close --now`: stop waiting for makers
    await r.step(n=4)
    assert r.e.st.phase == "flat" and r.arcus.sent[-1][0] == "taker"


async def test_a_venue_that_stops_answering_changes_nothing_until_it_is_back() -> None:
    r = Rig()
    await r.to_open()
    sent = len(r.arcus.sent) + len(r.lighter.sent)
    r.arcus.down = True
    await r.step(n=3)
    assert r.e.st.phase == "open" and len(r.arcus.sent) + len(r.lighter.sent) == sent     # the hedge is kept
    assert "venue_down" in r.store.kinds()
    r.arcus.down = False
    await r.step()
    assert r.e.st.phase == "open"
    assert isinstance(VenueDown("x"), RuntimeError)


async def test_a_restart_picks_up_the_open_position() -> None:
    r = Rig()
    await r.to_open()
    again = Engine({"arcus": r.arcus, "lighter": r.lighter}, r.planner, lambda: r.s, r.store,
                   state=State.load(r.store.saved[-1]), clock=r.clock)
    assert again.st.phase == "open" and again.st.legs["arcus"].venue == "arcus"
    r.lighter.trigger_stop(SYM)
    for _ in range(2):
        r.clock.t += eng.CONFIRM_S
        await again.step()
    assert again.st.phase == "exiting"


async def test_it_does_not_open_over_a_position_that_is_not_its_own() -> None:
    r = Rig()
    r.arcus.pos[SYM] = 0.5
    await r.step()
    assert r.e.st.phase == "flat" and "refused" in r.store.kinds() and not r.arcus.sent


async def test_unequal_positions_are_made_equal_only_after_a_second_look() -> None:
    r = Rig()
    await r.step(n=2)
    for v in (r.arcus, r.lighter):
        v.fill(r.open_orders(v)[0].id)
    size = r.e.st.size
    r.arcus.pos[SYM] = -(size + 0.5)                       # Arcus shows more than the bot's orders filled
    await r.step()
    assert r.e.st.phase == "entering" and r.arcus.sent[-1][0] == "maker"      # seen once: nothing done yet
    await r.step(dt=eng.CONFIRM_S)
    kind, _sym, side, qty, _w, ro = r.arcus.sent[-1]
    assert (kind, side, qty, ro) == ("taker", BUY, pytest.approx(0.5), True) and "unequal" in r.store.kinds()
    await r.step()
    await r.step(dt=eng.CONFIRM_S)
    await r.step()
    assert r.e.st.phase == "open" and r.arcus.pos[SYM] == pytest.approx(-r.lighter.pos[SYM])
    assert r.e.st.entry["arcus"] == pytest.approx(106.42)  # the entry price is not bent by the correction


async def test_a_venue_that_refuses_the_stops_gets_the_position_closed() -> None:
    r = Rig()
    r.lighter.fail_stops = True
    await r.step(n=2)
    for v in (r.arcus, r.lighter):
        v.fill(r.open_orders(v)[0].id)
    await r.step(n=1 + eng.STOP_TRIES)
    assert r.e.st.phase == "exiting" and "would not take the stop orders" in r.e.st.why
    loose = Rig(require_native_stops=False)                # paper and tests may run on the bot's own stop alone
    loose.lighter.fail_stops = True
    await loose.to_open()
    await loose.step(n=10)
    assert loose.e.st.phase == "open"


async def test_a_paper_venue_keeps_its_position_and_money_across_a_restart() -> None:
    r = Rig()
    await r.to_open()
    saved = r.lighter.dump()
    fresh = SimVenue("lighter", r.lighter.spec, 120.0)
    assert await fresh.position(SYM) == 0.0
    fresh.restore(saved)
    fresh.set_top(SYM, 106.40, 106.42)
    assert await fresh.position(SYM) == r.lighter.pos[SYM] and fresh.stops[SYM] == r.lighter.stops[SYM]
    assert await fresh.free_collateral() == pytest.approx(await r.lighter.free_collateral())


async def test_stops_refused_pause_the_bot_and_unequal_money_is_said() -> None:
    r = Rig()
    r.lighter.fail_stops = True
    await r.step(n=2)
    for v in (r.arcus, r.lighter):
        v.fill(r.open_orders(v)[0].id)
    await r.step(n=1 + eng.STOP_TRIES)                     # the venue will not take the stops: close ...
    await r.step(n=2)
    for v in (r.arcus, r.lighter):
        for o in r.open_orders(v):
            v.fill(o.id)
    await r.step(n=2)
    assert r.e.st.phase == "flat" and r.e.st.paused       # ... and stay out: it would only happen again
    asked = r.planner.asked
    await r.step(dt=400, n=2)
    assert r.planner.asked == asked

    r2 = Rig()
    r2.arcus.collateral, r2.lighter.collateral = 30.0, 210.0
    await r2.step()
    hint = [t for k, t in r2.store.events if k == "rebalance"]
    assert hint and hint[0].startswith("MOVE $90.00 from lighter to arcus") and "each has $120.00" in hint[0]
    assert r2.e.st.phase == "entering" and r2.e.st.plan["notional"] < 30 * 10    # sized by the smaller balance


async def test_a_cap_on_the_position_size_for_a_first_small_run() -> None:
    r = Rig(Settings(max_notional_usd=50.0))
    await r.step()
    assert r.e.st.size * 106.41 == pytest.approx(50.0, abs=0.02)



async def test_a_closed_position_that_left_the_money_uneven_says_at_once_how_much_to_move() -> None:
    r = Rig(Settings(rebalance_share=0.45))
    await r.to_open()
    for v in (r.arcus, r.lighter):
        v.set_top(SYM, 109.60, 109.62)                     # +3%: the long venue gains what the short one loses
    r.store.cmd = {"close": True, "now": True}
    await r.step(n=4)
    assert r.e.st.phase == "flat", r.store.events
    kinds = r.store.kinds()
    assert kinds.index("rebalance") == kinds.index("closed") + 1          # the same step, not five minutes later
    text = next(t for k, t in r.store.events if k == "rebalance")
    a, b = await r.arcus.free_collateral(), await r.lighter.free_collateral()
    assert a is not None and b is not None
    assert text.startswith(f"MOVE ${abs(a - b) / 2:,.2f} from lighter to arcus")    # the long leg (Lighter) gained
    assert f"${(a + b) / 2:,.2f}" in text                                 # what each holds after the transfer


async def test_a_position_that_moved_too_much_money_is_closed_after_the_funding_payment() -> None:
    r = Rig()
    await r.to_open()
    st = r.e.st
    move = st.entry["arcus"] * st.stop_dist * 0.9                         # not as far as the stop
    swing = st.size * move
    r.s = replace(r.s, drift_close_share=0.5 - swing / 240 * 0.9)         # ... but past the drift limit
    for v in (r.arcus, r.lighter):
        v.set_top(SYM, 106.40 + move, 106.42 + move)
    r.clock.t = (r.clock.t // 3600) * 3600 + 1800                         # half past: the next payment is still due
    await r.step(dt=0)
    assert r.e.st.phase == "open"
    r.clock.t = (r.clock.t // 3600 + 1) * 3600 + 5                        # just after it
    await r.step(dt=0)
    assert r.e.st.phase == "exiting" and "closing after the funding payment" in r.e.st.why and not r.e.st.urgent
    assert "has moved from" in r.e.st.why

    off = Rig()                                                           # the default: the stop does it, not this
    await off.to_open()
    for v in (off.arcus, off.lighter):
        v.set_top(SYM, 106.40 + move, 106.42 + move)
    off.clock.t = (off.clock.t // 3600 + 1) * 3600 + 5
    await off.step(dt=0)
    assert off.e.st.phase == "open"


def test_a_crypto_market_is_never_planned_unless_the_owner_allows_it() -> None:
    a, b = legs()
    money = {"arcus": 120.0, "lighter": 120.0}
    hist = ([1.7e-5] * 168, [4e-6] * 168)
    stock = plan(replace(a, category="EQUITIES"), b, *hist, 0.0167, money, Settings())
    crypto = plan(replace(a, category="CRYPTO"), b, *hist, 0.0167, money, Settings())
    allowed = plan(replace(a, category="CRYPTO"), b, *hist, 0.0167, money, Settings(rwa_only=False))
    assert stock.go and allowed.go
    assert not crypto.go and "crypto" in crypto.reasons[0]
