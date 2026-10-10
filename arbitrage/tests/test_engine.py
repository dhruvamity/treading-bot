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
# the older behaviour most of these tests are about: both legs rest maker orders, uneven money does not stop it,
# and the leverage keeps the stop three daily moves away
BOTH = Settings(hedge_taker=False, uneven_wait=False, enter_timeout_s=180.0, stop_sigmas=3.0, max_leverage=20.0,
                stop_early=0.0,
                hold_off_hours=True)


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
        self.s = settings or BOTH
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
    r = Rig(replace(BOTH, max_cross_bp=3.0, enter_timeout_s=60))
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
    r = Rig(replace(BOTH, enter_timeout_s=30))
    await r.step(n=2)
    await r.step(dt=31)
    await r.step(n=2)
    assert r.e.st.phase == "flat" and not r.open_orders(r.arcus) and not r.open_orders(r.lighter)
    assert r.arcus.pos.get(SYM, 0) == 0 == r.lighter.pos.get(SYM, 0) and "not_opened" in r.store.kinds()


async def test_a_maker_order_is_replaced_before_it_would_expire_on_a_venue_where_orders_expire() -> None:
    r = Rig(replace(BOTH, enter_timeout_s=900))
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
    r = Rig(replace(BOTH, max_notional_usd=50.0))
    await r.step()
    assert r.e.st.size * 106.41 == pytest.approx(50.0, abs=0.02)



async def test_a_closed_position_that_left_the_money_uneven_says_at_once_how_much_to_move() -> None:
    r = Rig(replace(BOTH, rebalance_share=0.45))
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


# ------------------------------------------------------------------------------ the owner's rules of 10 Oct 2026
async def test_lighters_leg_rests_nothing_and_takes_what_arcus_has_filled() -> None:
    r = Rig(Settings(uneven_wait=False))                   # hedge_taker is on by default
    await r.step(n=2)
    st = r.e.st
    assert st.phase == "entering" and (st.long_venue, st.short_venue) == ("lighter", "arcus")
    (sell,) = r.open_orders(r.arcus)
    assert not r.open_orders(r.lighter) and not [x for x in r.lighter.sent if x[0] == "maker"]
    r.arcus.fill(sell.id, sell.size / 2)                   # half of Arcus's order is taken
    await r.step()                                         # counted
    await r.step()                                         # Lighter takes the same amount, at once
    kind, _, side, size, *_ = r.lighter.sent[-1]
    assert (kind, side) == ("taker", BUY) and size == pytest.approx(sell.size / 2, abs=1e-4)
    assert abs(r.lighter.pos[SYM] + r.arcus.pos[SYM]) < 2e-4            # equal within Lighter's size step
    assert "hedge" in r.store.kinds() and "cross" not in r.store.kinds()
    r.arcus.fill(sell.id)                                  # the rest
    await r.step(n=4)
    assert r.e.st.phase == "open" and abs(r.lighter.pos[SYM] + r.arcus.pos[SYM]) < 2e-4
    assert not [x for x in r.lighter.sent if x[0] == "maker"]

    r.s = replace(r.s, max_hold_h=1.0)                     # closing works the same way round
    await r.step(dt=2 * 3600)
    assert r.e.st.phase == "exiting" and not r.e.st.urgent
    await r.step(n=2)
    (buy,) = r.open_orders(r.arcus)
    assert buy.side == BUY and not r.open_orders(r.lighter)
    r.arcus.fill(buy.id)
    await r.step(n=4)
    assert r.e.st.phase == "flat" and r.lighter.sent[-1][0] == "taker" and r.lighter.sent[-1][-1]   # reduce-only
    assert abs(r.lighter.pos[SYM]) < 2e-4 and abs(r.arcus.pos[SYM]) < 2e-4


async def open_hedged(r: Rig) -> float:
    """Short Arcus, long Lighter, open with the venues' stops placed; returns the stop's distance."""
    await r.step(n=2)
    (sell,) = r.open_orders(r.arcus)
    r.arcus.fill(sell.id)
    await r.step(n=5)
    assert r.e.st.phase == "open" and r.arcus.stops and r.lighter.stops, r.store.events
    return r.e.st.stop_dist


def move_to(r: Rig, share: float, d: float) -> None:
    px = 106.41 * (1 + share * d)
    for v in (r.arcus, r.lighter):
        v.set_top(SYM, px - 0.01, px + 0.01)


async def test_near_the_stop_it_closes_with_limit_orders_and_pays_no_taker_fee() -> None:
    r = Rig(Settings(uneven_wait=False))                   # stop_early is 0.8 by default
    d = await open_hedged(r)
    move_to(r, 0.7, d)
    await r.step()
    assert r.e.st.phase == "open"                          # 70% of the way: nothing yet
    move_to(r, 0.85, d)
    await r.step()
    assert r.e.st.phase == "exiting" and not r.e.st.urgent and "near the stop" in r.e.st.why
    await r.step(n=2)
    (buy,) = r.open_orders(r.arcus)                        # Arcus's short is bought back by a resting order ...
    assert buy.side == BUY and r.arcus.sent[-1][0] == "maker" and r.arcus.sent[-1][-1]
    assert not r.open_orders(r.lighter)                    # ... and Lighter waits for it
    r.arcus.fill(buy.id)
    await r.step(n=4)
    assert r.e.st.phase == "flat" and abs(r.arcus.pos[SYM]) < 2e-4 and abs(r.lighter.pos[SYM]) < 2e-4
    assert not [x for x in r.arcus.sent if x[0] == "taker"]            # no taker order on the venue that charges
    assert r.lighter.sent[-1][0] == "taker" and r.lighter.sent[-1][-1]


async def test_the_stop_reached_while_closing_with_limit_orders_ends_it_with_taker_orders() -> None:
    r = Rig(Settings(uneven_wait=False))
    d = await open_hedged(r)
    move_to(r, 0.85, d)
    await r.step(n=3)
    assert r.e.st.phase == "exiting" and not r.e.st.urgent and r.open_orders(r.arcus)
    move_to(r, 1.01, d)                                    # nobody sold to the resting order and the price went on
    await r.step()
    assert r.e.st.urgent and "reached the stop" in r.store.events[-1][1]
    await r.step(n=4)
    assert r.e.st.phase == "flat" and abs(r.arcus.pos[SYM]) < 2e-4 and abs(r.lighter.pos[SYM]) < 2e-4
    assert r.arcus.sent[-1][0] == "taker" and not r.open_orders(r.arcus)

    r2 = Rig(Settings(uneven_wait=False, stop_early=0.0, max_hold_h=1.0))     # any close with limit orders is watched
    d = await open_hedged(r2)
    await r2.step(dt=2 * 3600)
    assert r2.e.st.phase == "exiting" and not r2.e.st.urgent
    await r2.step()
    move_to(r2, -1.01, d)
    await r2.step()
    assert r2.e.st.urgent
    await r2.step(n=4)
    assert r2.e.st.phase == "flat"

    r3 = Rig(Settings(uneven_wait=False, stop_early=0.0))  # switched off: nothing happens short of the stop
    d = await open_hedged(r3)
    move_to(r3, 0.95, d)
    await r3.step(n=2)
    assert r3.e.st.phase == "open"


async def test_two_venues_that_both_charge_takers_both_rest_maker_orders() -> None:
    r = Rig(Settings(uneven_wait=False))
    r.lighter.spec = replace(r.lighter.spec, taker_bp=2.8)             # a premium Lighter account
    await r.step(n=2)
    assert len(r.open_orders(r.arcus)) == 1 and len(r.open_orders(r.lighter)) == 1


async def test_uneven_money_stops_new_positions_and_repeats_the_amount_until_it_is_moved() -> None:
    r = Rig(Settings())                                    # uneven_wait is on by default
    r.lighter.collateral = 40.0                            # Arcus 120, Lighter 40: 25% of the money
    await r.step()
    assert r.e.st.phase == "flat" and r.e.st.waiting and r.planner.asked == 0
    (text,) = [t for k, t in r.store.events if k == "rebalance"]
    assert text.startswith("MOVE $40.00 from arcus to lighter") and "Nothing is opened until it has arrived" in text
    await r.step(dt=eng.SCAN_EVERY_S + 1, n=3)             # a quarter of an hour: not said again yet
    assert r.store.kinds().count("rebalance") == 1
    await r.step(dt=eng.SCAN_EVERY_S + 1, n=4)             # past uneven_remind_min (30)
    assert r.store.kinds().count("rebalance") == 2 and r.e.st.phase == "flat"
    r.arcus.collateral, r.lighter.collateral = 80.0, 80.0  # the owner has moved it
    await r.step(dt=eng.SCAN_EVERY_S + 1)
    assert "even again" in r.store.events[-2][1] and r.e.st.phase == "entering" and not r.e.st.waiting


def test_cycling_opens_whatever_the_funding_pays_and_closes_on_the_clock() -> None:
    from arbitrage.rank import exit_reason
    a, b = legs()
    thin = [4.4e-6] * 168                                  # Arcus pays 0.4e-6 an hour more: 0.35% a year
    money = {"arcus": 120.0, "lighter": 120.0}
    usual = plan(a, b, thin, [4e-6] * 168, 0.0167, money, Settings())
    assert not usual.go and any("floor" in x or "changed sides" in x for x in usual.reasons)
    cyc = plan(a, b, thin, [4e-6] * 168, 0.0167, money, Settings(cycle_h=3))
    assert cyc.go and cyc.short_venue == "arcus"           # the side the next payment points to
    back = plan(replace(a, next_rate_h=1e-6), b, thin, [4e-6] * 168, 0.0167, money, Settings(cycle_h=3))
    assert back.go and back.short_venue == "lighter"       # ... and the other way round when it turns
    # `profunding_side`: the side is given, whatever the venues' own rates say
    told = plan(a, b, thin, [4e-6] * 168, 0.0167, money, Settings(cycle_h=3), short_venue="lighter")
    assert told.go and (told.short_venue, told.long_venue) == ("lighter", "arcus")
    assert told.prices["lighter"]["side"] == -1.0 and told.prices["arcus"]["side"] == 1.0   # the stops follow the side
    paying = [1.7e-5] * 168                                # Arcus clearly pays more: the usual rule would short it
    assert plan(a, b, paying, [4e-6] * 168, 0.0167, money, Settings(), short_venue="arcus").go
    against = plan(a, b, paying, [4e-6] * 168, 0.0167, money, Settings(), short_venue="lighter")
    assert not against.go and against.short_venue == "lighter"      # without cycling it is not opened against them
    s = Settings(cycle_h=3)
    assert exit_reason(2.9, -1e-5, -1e-5, s) == ""         # not the funding: the clock
    assert "cycle" in exit_reason(3.0, 1e-5, 1e-5, s)
    assert plan(replace(a, category="CRYPTO"), b, thin, [4e-6] * 168, 0.0167, money, s).go is False   # still no crypto


async def test_only_the_listed_markets_are_looked_at(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from arbitrage.exec import run

    (tmp_path / "settings.json").write_text('{"only": ["spy", "QQQ"], "skip": ["QQQ"]}')
    assert run.allowed(tmp_path) == {"SPY", "QQQ"} and run.skipped(tmp_path) == {"QQQ"}
    assert run.allowed(tmp_path / "nothing") == set()
    asked: dict[str, Any] = {}
    a, b = legs()
    money = {"arcus": 120.0, "lighter": 120.0}
    plans = [plan(replace(a, symbol=sym), replace(b, symbol=sym), [1.7e-5] * 168, [4e-6] * 168, 0.0167, money,
                  Settings()) for sym in ("NVDA", "QQQ", "SPY")]

    async def scan(cfg: Any, **kw: Any) -> Any:
        asked.update(kw)
        return SimpleNamespace(plans=plans)

    monkeypatch.setattr(run.scanner, "run", scan)
    p = run.LivePlanner(lambda: None, lambda: run.skipped(tmp_path), lambda: run.allowed(tmp_path))   # type: ignore[arg-type,return-value]
    best = await p.best(money)
    assert best is not None and best.symbol == "SPY" and asked["symbols"] == ["QQQ", "SPY"]   # NVDA pays as much
    best = await run.LivePlanner(lambda: None).best(money)                                    # type: ignore[arg-type,return-value]
    assert best is not None and best.symbol == "NVDA" and asked["symbols"] is None            # no list: the first
