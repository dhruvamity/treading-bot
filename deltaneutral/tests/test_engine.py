"""The bot's cycle on venues in memory (the arbitrage's SimVenue): open, Lighter follows Arcus, close, transfer."""

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
        for v in self.v.values():
            v.set_top("AAA", 99.99, 100.01)
            v.set_top("BBB", 49.99, 50.01)
        self.off_hours = off_hours
        self.clock = Clock(WED_14)
        self.moved: list[tuple[float, str, str]] = []
        self.settings = E.Settings(x="AAA", y="BBB", **settings)
        self.store = E.Store(tmp_path, "paper", echo=None)
        self.eng = E.Engine(self.v, self.read, lambda: self.settings, self.store, clock=self.clock,
                            mover=lambda a, s, t: self.moved.append((a, s, t)))

    async def read(self):
        eq = {k: await v.free_collateral() for k, v in self.v.items()}
        return eq, {v: {"AAA": RULES[v], "BBB": RULES[v]} for v in (ARCUS, LIGHTER)}, self.off_hours

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            asyncio.run(self.eng.step())

    def fill_arcus(self) -> None:
        for oid, o in list(self.v[ARCUS].orders.items()):
            if o.open:
                self.v[ARCUS].fill(oid)

    def events(self) -> list[str]:
        return [e["kind"] for e in self.store.tail(self.store.events, 100)]


def test_a_cycle_opens_lighter_mirrors_arcus_closes_and_asks_for_the_transfer(tmp_path):
    w = World(tmp_path)
    w.step()                                             # 14:00: the open
    st = w.eng.st
    assert st.phase == "opening" and st.way in (1, -1)
    assert abs(st.target["AAA"] * 100 - 450 * st.way) < 1 and abs(st.target["BBB"] * 50 + 450 * st.way) < 1
    w.step()                                             # Arcus rests a limit order on each market
    assert {s[0] for s in w.v[ARCUS].sent} == {"maker"} and not w.v[LIGHTER].sent
    w.fill_arcus()
    w.step(2)                                            # Lighter follows what Arcus holds
    pa, pl = w.v[ARCUS].pos, w.v[LIGHTER].pos
    assert abs(pa["AAA"] + pl["AAA"]) < 1e-9 and abs(pa["BBB"] + pl["BBB"]) < 1e-9
    assert st.phase == "open" and "opened" in w.events()
    w.v[ARCUS].set_top("AAA", 104.99, 105.01)            # the venues' prices part a little: money goes uneven
    w.v[LIGHTER].set_top("AAA", 104.99, 105.01)
    w.v[ARCUS].set_top("BBB", 49.99, 50.01)
    w.v[LIGHTER].set_top("BBB", 49.99, 50.01)
    w.clock.t += 24 * 3600
    w.step()
    assert st.phase == "closing"
    w.step()
    w.fill_arcus()
    w.step(2)
    assert st.phase == "flat"
    assert all(abs(p) < 1e-12 for v in w.v.values() for p in v.pos.values())
    w.step()                                             # the next cycle opens at once: it is the next day's open
    assert st.phase == "opening" and st.cycle == 2
    ev = w.store.tail(w.store.events, 100)
    tr = next(e for e in ev if e["kind"] == "transfer")
    assert tr["amount"] > 20 and "MOVE $" in tr["text"]
    assert w.moved and w.moved[0][0] == tr["amount"]
    row = w.store.tail(w.store.cycles, 1)[0]
    assert row["cycle"] == 1 and row["volume"] > 3000


def test_lighter_follows_when_arcus_holds_less(tmp_path):
    w = World(tmp_path, arcus_maker=False)              # Arcus takes at once
    w.step(3)
    assert w.eng.st.phase == "open"
    half = w.v[ARCUS].pos["AAA"] / 2
    w.v[ARCUS].pos["AAA"] -= half                        # e.g. Arcus cut the position (a liquidation, by hand)
    w.eng.st.target["AAA"] = w.v[ARCUS].pos["AAA"]       # (the bot keeps what Arcus holds as its target)
    w.step()
    assert abs(w.v[LIGHTER].pos["AAA"] + w.v[ARCUS].pos["AAA"]) < 1e-9


def test_an_off_hours_open_is_smaller_and_cut_mode_shrinks_at_the_close(tmp_path):
    w = World(tmp_path, off_hours=True, arcus_maker=False)
    w.step(3)
    assert abs(abs(w.v[ARCUS].pos["AAA"]) * 100 - 300) < 1                # 90 / (15% + 15%)
    w2 = World(tmp_path / "b", arcus_maker=False, offhours="cut")
    w2.step(3)
    assert abs(abs(w2.v[ARCUS].pos["AAA"]) * 100 - 450) < 1
    w2.off_hours = True                                  # 20:00 New York
    w2.step(3)
    assert abs(abs(w2.v[ARCUS].pos["AAA"]) * 100 - 300) < 2
    assert abs(w2.v[LIGHTER].pos["AAA"] + w2.v[ARCUS].pos["AAA"]) < 1e-9
    assert "resized" in w2.events()


def test_the_guard_closes_everything_before_a_venue_is_liquidated(tmp_path):
    w = World(tmp_path, arcus_maker=False, guard=0.25)
    w.step(3)
    assert w.eng.st.phase == "open"
    way = w.eng.st.way
    for v in w.v.values():                               # X moves 9% against Arcus's leg
        mid = 100 * (1 - 0.09 * way)
        v.set_top("AAA", mid - 0.01, mid + 0.01)
    w.step(4)
    assert "guard" in w.events() and w.eng.st.phase == "flat"


def test_settings_are_checked(tmp_path):
    p = tmp_path / "settings.json"
    assert E.set_value(p, "open_ny", "9.5") == 9.5
    assert E.set_value(p, "offhours", "cut") == "cut"
    assert E.set_value(p, "arcus_maker", "off") is False
    assert E.Settings.load(p).offhours == "cut" and E.Settings.load(p).arcus_maker is False
    for name, bad in (("open_ny", "25"), ("way", "sideways"), ("nonsense", "1")):
        try:
            E.set_value(p, name, bad)
        except ValueError:
            continue
        raise AssertionError(f"{name}={bad} was accepted")
    assert E.set_pair(p, "orcl", "crwv") == ("ORCL", "CRWV")


def test_a_skip_date_added_while_open_closes_the_cycle_and_holds_off(tmp_path):
    w = World(tmp_path, arcus_maker=False)
    w.step(3)
    assert w.eng.st.phase == "open"
    w.settings.skip = ["2026-10-15"]                     # Thursday's report: nothing held from Wednesday 16:00
    w.clock.t += 5.75 * 3600                             # Wednesday 15:45 New York
    w.step(3)
    assert w.eng.st.phase == "flat" and "a skip date" in w.store.tail(w.store.cycles, 1)[0]["why"]
    w.clock.t += 18.25 * 3600                            # Thursday 10:00: still inside the window
    w.step()
    assert w.eng.st.phase == "flat" and "skipped" in w.events()
