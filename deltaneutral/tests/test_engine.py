"""The bot's cycle on venues in memory (the arbitrage's SimVenue): both venues chase with limit orders and stay level,
the stops sit at the same prices on both, a stop or the guard closes everything, the transfer is asked for."""

import asyncio
import datetime as dt

from arbitrage.exec.venue import SimVenue, Spec

from deltaneutral import engine as E
from deltaneutral import plan as P
from deltaneutral.plan import ARCUS, LIGHTER

RULES = {ARCUS: P.Rules(imf=0.1, imf_off=0.15, mmf=0.0667), LIGHTER: P.Rules(imf=0.1, imf_off=0.1, mmf=0.06)}
WED_14 = dt.datetime(2026, 10, 14, 14, 0, tzinfo=dt.UTC).timestamp()     # a Wednesday, 10:00 New York


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class World:
    def __init__(self, tmp_path, *, off_hours: bool = False, **settings) -> None:
        spec = Spec(tick=0.01, step=0.001, min_size=0.001, min_notional=5.0)
        self.v = {ARCUS: SimVenue(ARCUS, spec, 100.0), LIGHTER: SimVenue(LIGHTER, spec, 100.0)}
        self.quote(100.0, 50.0)
        self.off_hours = off_hours
        self.clock = Clock(WED_14)
        self.moved: list[tuple[float, str, str]] = []
        self.settings = E.Settings(x="AAA", y="BBB", **settings)
        self.store = E.Store(tmp_path, "paper", echo=None)
        self.eng = E.Engine(self.v, self.read, lambda: self.settings, self.store, clock=self.clock,
                            mover=lambda a, s, t: self.moved.append((a, s, t)))

    def quote(self, a: float, b: float, half: float = 0.01) -> None:
        for v in self.v.values():
            v.set_top("AAA", a - half, a + half)
            v.set_top("BBB", b - half, b + half)

    async def read(self):
        eq = {k: await v.free_collateral() for k, v in self.v.items()}
        return eq, {v: {"AAA": RULES[v], "BBB": RULES[v]} for v in (ARCUS, LIGHTER)}, self.off_hours

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            asyncio.run(self.eng.step())
            self.clock.t += 3

    def fill(self, venue: str | None = None) -> None:
        for name, v in self.v.items():
            if venue in (None, name):
                for oid, o in list(v.orders.items()):
                    if o.open:
                        v.fill(oid)

    def until(self, phase: str, fill: bool = True, n: int = 40) -> None:
        for _ in range(n):
            if self.eng.st.phase == phase:
                return
            self.step()
            if fill:
                self.fill()
        raise AssertionError(f"never reached {phase}: {self.eng.st.phase}")

    def events(self) -> list[str]:
        return [e["kind"] for e in self.store.tail(self.store.events, 200)]

    def level(self) -> bool:
        return all(abs(self.v[ARCUS].pos.get(s, 0) + self.v[LIGHTER].pos.get(s, 0)) < 1e-9 for s in ("AAA", "BBB"))


def test_both_venues_chase_with_limit_orders_close_and_ask_for_the_transfer(tmp_path):
    w = World(tmp_path)
    w.step()                                             # 10:00 New York: the open
    st = w.eng.st
    assert st.phase == "opening" and st.way in (1, -1)
    assert abs(st.target["AAA"] * 100 - 450 * st.way) < 1 and abs(st.target["BBB"] * 50 + 450 * st.way) < 1
    w.step()
    kinds = {(name, s[0]) for name, v in w.v.items() for s in v.sent}
    assert kinds == {(ARCUS, "maker"), (LIGHTER, "maker")}      # nobody takes: every leg rests a limit order
    w.until("open")
    assert w.level() and "opened" in w.events()
    assert not any(s[0] == "taker" for v in w.v.values() for s in v.sent)
    w.quote(105.0, 50.0)                                 # X moves: the money goes uneven
    w.clock.t = st.close_at
    w.step()
    assert st.phase == "closing" and not any(w.v[v].stops for v in w.v)     # the stops go first
    w.until("flat")
    assert all(abs(p) < 1e-12 for v in w.v.values() for p in v.pos.values())
    assert all(s[5] for v in w.v.values() for s in v.sent[-2:])               # the exit was reduce-only
    assert not any(s[0] == "taker" for v in w.v.values() for s in v.sent)    # limit orders all the way
    ev = w.store.tail(w.store.events, 200)
    tr = next(e for e in ev if e["kind"] == "transfer")
    assert tr["amount"] > 20 and "MOVE $" in tr["text"]
    assert w.moved and w.moved[0][0] == tr["amount"]
    row = w.store.tail(w.store.cycles, 1)[0]
    assert row["cycle"] == 1 and row["volume"] > 3000


def test_a_resting_order_sits_one_tick_inside_a_wide_spread_and_never_crosses():
    eng = E.Engine.__new__(E.Engine)
    eng.st, eng.specs = E.State(), {ARCUS: {"AAA": Spec(0.01, 0.001, 0.001, 5.0)}}
    s = E.Settings()

    def snap(bid: float, ask: float) -> E.Snapshot:
        return E.Snapshot({ARCUS: {"AAA": E.Top(bid, ask)}}, {}, {}, {}, False)

    assert abs(eng._price(ARCUS, "AAA", 1, snap(99.90, 100.10), s) - 99.91) < 1e-9
    assert abs(eng._price(ARCUS, "AAA", -1, snap(99.90, 100.10), s) - 100.09) < 1e-9
    assert eng._price(ARCUS, "AAA", 1, snap(99.99, 100.01), s) == 99.99      # one tick each side: join the best
    eng.st.orders["arcus:AAA"] = {"side": 1, "price": 99.91}
    assert eng._price(ARCUS, "AAA", 1, snap(99.91, 100.10), s) == 99.91      # ours is the best: it stays


def test_one_venue_never_gets_more_than_the_gap_ahead_and_a_lasting_gap_is_taken(tmp_path):
    w = World(tmp_path, max_gap_usd=150, catchup_s=60)
    w.step(2)
    for _ in range(4):                                   # only Arcus fills
        w.fill(ARCUS)
        w.step()
    ahead = abs(w.v[ARCUS].pos.get("AAA", 0)) * 100
    assert 140 <= ahead <= 160 and not w.v[LIGHTER].pos.get("AAA")         # Arcus stopped at $150 ahead
    assert not any(s[0] == "taker" for s in w.v[LIGHTER].sent)
    w.clock.t += 61                                      # the gap has lasted a minute: Lighter takes it
    w.step()
    assert any(s[0] == "taker" for s in w.v[LIGHTER].sent)
    assert abs(w.v[ARCUS].pos["AAA"] + w.v[LIGHTER].pos["AAA"]) < 1e-9


def test_the_stops_sit_at_the_same_prices_on_both_venues(tmp_path):
    w = World(tmp_path)
    w.until("open")
    w.step()
    st = w.eng.st
    assert set(st.stops) == {"AAA", "BBB"} and "protected" in w.events()
    lo, hi = st.stops["AAA"]
    # Arcus has less room: $100 less 6.67% of $900 = $40, which is 8.9% of its $450 leg; 75% of that is 6.7%
    d = 0.75 * 40 / 450
    assert abs(hi / 100 - 1 - d) < 0.002 and abs(1 - lo / 100 - d) < 0.002
    for sym, (lo, hi) in st.stops.items():
        a, li = w.v[ARCUS].stops[sym], w.v[LIGHTER].stops[sym]
        assert {a[1], a[2]} == {li[1], li[2]} == {lo, hi}                   # the same two prices
        assert a[1] == (lo if a[0] > 0 else hi) and li[1] == (lo if li[0] > 0 else hi)


def test_a_stop_firing_on_one_venue_closes_everything_at_once(tmp_path):
    w = World(tmp_path)
    w.until("open")
    w.step()
    w.v[LIGHTER].trigger_stop("AAA")                     # Lighter's stop on AAA fires
    w.step()
    st = w.eng.st
    assert st.phase == "closing" and st.urgent and "stopped_out" in w.events()
    w.until("flat", fill=False)                          # urgent: it takes, waits for nothing
    assert all(abs(p) < 1e-12 for v in w.v.values() for p in v.pos.values())
    assert w.eng.st.next_open > w.clock.t                # the next cycle waits for the next open time


def test_the_guard_closes_at_three_quarters_of_the_way_to_liquidation(tmp_path):
    w = World(tmp_path, execution="take", venue_stops=False)
    w.step(3)
    assert w.eng.st.phase == "open"
    w.step()
    way = w.eng.st.way
    # Arcus holds $450 of X: X moving 6% against it leaves it $14 of its $40 room (65% used), 8% leaves $6 (84%)
    w.quote(100 * (1 - 0.06 * way), 50.0)
    w.step()
    assert w.eng.st.phase == "open"
    w.quote(100 * (1 - 0.08 * way), 50.0)
    w.step()
    assert "guard" in w.events() and w.eng.st.urgent


def test_an_off_hours_open_is_smaller_and_cut_mode_shrinks_at_the_close(tmp_path):
    w = World(tmp_path, off_hours=True, execution="take")
    w.step(3)
    assert abs(abs(w.v[ARCUS].pos["AAA"]) * 100 - 300) < 1                # 90 / (15% + 15%)
    w2 = World(tmp_path / "b", execution="take", offhours="cut")
    w2.step(3)
    assert abs(abs(w2.v[ARCUS].pos["AAA"]) * 100 - 450) < 1
    w2.off_hours = True                                  # 20:00 New York
    w2.step(3)
    assert abs(abs(w2.v[ARCUS].pos["AAA"]) * 100 - 300) < 2 and w2.level()
    assert "resized" in w2.events()


def test_settings_are_checked(tmp_path):
    p = tmp_path / "settings.json"
    assert E.set_value(p, "open_ny", "9.5") == 9.5
    assert E.set_value(p, "offhours", "cut") == "cut"
    assert E.set_value(p, "execution", "arcus") == "arcus"
    assert E.set_value(p, "venue_stops", "off") is False
    loaded = E.Settings.load(p)
    assert loaded.offhours == "cut" and loaded.execution == "arcus" and loaded.venue_stops is False
    for name, bad in (("open_ny", "25"), ("way", "sideways"), ("stop_share", "1.5"), ("nonsense", "1")):
        try:
            E.set_value(p, name, bad)
        except ValueError:
            continue
        raise AssertionError(f"{name}={bad} was accepted")
    assert E.set_pair(p, "orcl", "crwv") == ("ORCL", "CRWV")


def test_a_skip_date_added_while_open_closes_the_cycle_and_holds_off(tmp_path):
    w = World(tmp_path, execution="take")
    w.step(3)
    assert w.eng.st.phase == "open"
    w.settings.skip = ["2026-10-15"]                     # Thursday's report: nothing held from Wednesday 16:00
    w.clock.t = WED_14 + 5.75 * 3600                     # Wednesday 15:45 New York
    w.step(4)
    assert w.eng.st.phase == "flat" and "a skip date" in w.store.tail(w.store.cycles, 1)[0]["why"]
    w.clock.t = WED_14 + 24 * 3600                       # Thursday 10:00: still inside the window
    w.step()
    assert w.eng.st.phase == "flat" and "skipped" in w.events()


def test_crumbs_under_a_venues_size_step_count_as_flat(tmp_path):
    """Arcus trades in finer sizes than Lighter: what Lighter cannot mirror is under one of its steps, and a cycle
    still ends flat (it once waited for an exact zero, then took the crumbs for a position found while flat)."""
    w = World(tmp_path, catchup_s=0)
    w.v[ARCUS].spec = Spec(tick=0.01, step=1e-7, min_size=1e-7, min_notional=5.0)
    w.v[LIGHTER].spec = Spec(tick=0.01, step=1e-4, min_size=1e-4, min_notional=5.0)
    w.step()
    w.step()
    for oid, o in list(w.v[ARCUS].orders.items()):      # Arcus fills an odd size, finer than Lighter's step
        if o.open:
            w.v[ARCUS].fill(oid, qty=o.size * 0.7777777)
    w.until("open")
    w.clock.t = w.eng.st.close_at
    w.until("flat")
    assert w.store.tail(w.store.cycles, 1)[0]["cycle"] == 1
    w.step()                                             # the 24 hours are up: the next cycle opens at once
    assert w.eng.st.phase == "opening" and w.eng.st.cycle == 2
    assert "found positions while flat" not in str(w.store.tail(w.store.events, 50))
