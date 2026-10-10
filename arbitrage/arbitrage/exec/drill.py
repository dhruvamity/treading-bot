"""`arbitrage livetest --what engine`: the executor itself (exec/engine.py) on both real venues at the smallest size.

The leg tests (legtest.py) prove each venue's adapter on its own. This drill proves what only the two together can
show: the engine entering a position with a maker order on each venue, crossing the one that falls behind, placing the
stop and take-profit on BOTH venues, holding, and leaving again, in both directions (long Arcus / short Lighter, then
the reverse), once with maker exit orders and once with taker orders at once (`arbitrage close --now`). After each
step it asks the venues themselves (their REST reads), not the engine, what they hold.

The owner runs it (ARB_LIVE=1 and LIVE typed). It refuses when either account holds a position or an order on the
market, before it sends anything; it stops and closes everything when the two accounts together fall `max_loss`
below where they began; it always ends flat with no orders. The cost is the spread and Arcus's taker fee on four
minimum-size round trips: cents. The plan it hands the engine is forced (a position the scanner would not open), so
it says nothing about whether the scanner's choices pay; that is what paper runs and a small live run are for.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from arbitrage.exec.engine import Engine, State
from arbitrage.exec.legtest import FAIL, PASS, Abort, Report
from arbitrage.exec.venue import BUY, SELL, TradeVenue
from arbitrage.rank import Plan, Settings

LEVERAGE = 2.0
STOP_DIST = 0.03             # the venues' stop and take profit sit 3% from the entry: they never fire in this drill
ENTER_LIMIT_S = 420.0        # the engine's own limit is 180 s (enter_timeout_s); this is the drill's, a margin beyond
EXIT_LIMIT_S = 420.0
STOPS_LIMIT_S = 90.0
SIZE_X = 1.2                 # over the venues' minimum, as the market-making bots do


class MemStore:
    """The engine's Store, in memory: the drill leaves no position file behind, and no command file."""

    def __init__(self, say: Callable[[str], None], clock: Callable[[], float]) -> None:
        self.say, self.clock = say, clock
        self.st: State | None = None
        self.cmd: dict[str, Any] = {}
        self.events: list[tuple[float, str, str]] = []

    def save(self, st: State) -> None:
        self.st = st

    def commands(self) -> dict[str, Any]:
        out, self.cmd = self.cmd, {}
        return out

    def send(self, **cmd: Any) -> None:
        self.cmd.update(cmd)

    def event(self, kind: str, text: str, **data: Any) -> None:
        self.events.append((self.clock(), kind, text))
        self.say(f"       engine: {text}")


class Forced:
    """A planner that offers one position, once."""

    def __init__(self, plan: Plan) -> None:
        self.plan, self.used = plan, False

    async def best(self, collateral: dict[str, float]) -> Plan | None:
        if self.used:
            return None
        self.used = True
        return self.plan

    async def edges(self, symbol: str, short_venue: str) -> tuple[float, float] | None:
        return None


class Drill:
    def __init__(self, venues: dict[str, TradeVenue], ground: dict[str, Any], symbol: str,
                 say: Callable[[str], None] = print, *, max_loss: float = 1.0, hold_s: float = 30.0,
                 settings: Settings | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time) -> None:
        self.venues = venues             # {"arcus": ArcusTrade, "lighter": LighterTrade}: what the engine uses
        self.ground = ground             # {"arcus": ArcusLegTest, "lighter": LegTest}: reads the venue itself
        self.symbol = symbol
        self.say = say
        self.max_loss = max_loss
        self.hold_s = hold_s
        self.settings = settings or Settings()
        self.sleep, self.clock = sleep, clock
        self.rep = Report(symbol, started=clock(), title="the two-leg engine")
        self.armed = False
        self.size = 0.0
        self.px = 0.0

    # ---------------------------------------------------------------- reading the venues themselves
    def note(self, name: str, result: str, detail: str) -> None:
        self.rep.steps.append((name, result, detail))
        self.say(f"  {result:<4} {name}: {detail}")

    async def truth(self) -> dict[str, tuple[float, float, int]]:
        """{venue: (equity, position, open orders)} as each venue itself says."""
        out = {}
        for name, g in self.ground.items():
            eq, pos = await g.account()
            out[name] = (eq, pos, len(await g.listed()))
        return out

    async def equity(self) -> float:
        return sum(v[0] for v in (await self.truth()).values())

    async def guard(self) -> None:
        if self.rep.equity_start is None:
            return
        down = self.rep.equity_start - await self.equity()
        if down > self.max_loss:
            raise Abort(f"the two accounts are down ${down:.2f}, more than the ${self.max_loss:.2f} this test may lose")

    # ---------------------------------------------------------------- connect
    async def step_connect(self) -> None:
        specs = {n: await v.start(self.symbol) for n, v in self.venues.items()}
        tops = {n: await v.top(self.symbol) for n, v in self.venues.items()}
        if any(t is None for t in tops.values()):
            raise Abort("no order book on one of the venues")
        t = await self.truth()
        busy = [f"{n} holds {p:g} and {o} order(s)" for n, (_e, p, o) in t.items() if p or o]
        if busy:
            raise Abort(f"{'; '.join(busy)} on {self.symbol}: the drill would cancel and close them. "
                        "Nothing was sent")
        self.rep.equity_start = sum(e for e, _p, _o in t.values())
        self.rep.account = int(getattr(self.venues.get("lighter"), "account", 0) or 0)
        self.armed = True
        ask = max(float(x.ask) for x in tops.values() if x is not None)
        self.px = sum(float(x.bid + x.ask) / 2 for x in tops.values() if x is not None) / len(tops)
        need = max(max(sp.min_size, sp.min_notional / ask) * SIZE_X for sp in specs.values())
        big = max(sp.step for sp in specs.values())
        self.size = math.ceil(need / big - 1e-9) * big
        for sp in specs.values():
            k = self.size / sp.step
            if abs(k - round(k)) > 1e-6:
                raise Abort(f"{self.size:g} is not a whole number of steps on both venues ({sp.step:g})")
        short = [f"{n} has ${e:.2f}" for n, (e, _p, _o) in t.items() if e < self.size * self.px / LEVERAGE * 1.5]
        self.note("connect", PASS if not short else FAIL,
                  f"{', '.join(f'{n} ${e:.2f}' for n, (e, _p, _o) in t.items())}; {self.symbol} "
                  f"{', '.join(f'{n} {x.bid:g}/{x.ask:g}' for n, x in tops.items() if x is not None)}; one leg is "
                  f"{self.size:g} (${self.size * self.px:.2f}) at {LEVERAGE:g}x; both accounts flat with no orders"
                  + (f"; NOT ENOUGH MARGIN: {', '.join(short)}" if short else ""))
        if short:
            raise Abort("an account has too little money for even this order: put a few dollars more on it")

    # ---------------------------------------------------------------- one position, in and out
    def plan(self, long_venue: str, short_venue: str) -> Plan:
        prices = {n: {"entry": self.px} for n in self.venues}
        return Plan(symbol=self.symbol, short_venue=short_venue, long_venue=long_venue, edge_h=0.0001,
                    edge_next_h=0.0001, edge_24h=0.0001, edge_7d=0.0001, leverage=LEVERAGE,
                    notional=self.size * self.px, size=self.size, stop_dist=STOP_DIST, liq_dist=0.3,
                    income_day=0.0, round_trip_usd=0.0, breakeven_h=0.0, next_payment_usd=0.0, next_payment_at=0,
                    prices=prices)

    async def drive(self, eng: Engine, until: Callable[[], bool], limit_s: float, every: float = 1.0) -> bool:
        """Step the engine until `until()` or the limit; the loss guard runs every 10 s."""
        end, last = self.clock() + limit_s, 0.0
        while True:
            await eng.step()
            if until():
                return True
            if self.clock() >= end:
                return False
            if self.clock() - last >= 10:
                last = self.clock()
                await self.guard()
            await self.sleep(every)

    async def position_round(self, long_venue: str, short_venue: str, now_exit: bool) -> None:
        tag = f"long {long_venue} / short {short_venue}"
        store = MemStore(self.say, self.clock)
        eng = Engine(self.venues, Forced(self.plan(long_venue, short_venue)), lambda: self.settings, store,
                     clock=self.clock, require_native_stops=True)
        # ---- in: maker orders, the one behind crosses, equal legs
        opened = await self.drive(eng, lambda: eng.st.phase in ("open", "flat"), ENTER_LIMIT_S)
        if not opened or eng.st.phase != "open":
            why = next((t for _c, k, t in reversed(store.events) if k in ("not_opened", "refused", "exiting")), "")
            self.note(f"entry ({tag})", FAIL, f"the engine did not get to an open position "
                      f"(phase {eng.st.phase}){': ' + why if why else ''}")
            await self.leave(eng, store, urgent=True)
            return
        t = await self.truth()
        la, sh = t[long_venue][1], t[short_venue][1]
        crossed = sum(1 for _c, k, _t in store.events if k == "cross")
        equal = abs(la + sh) < 1e-6 * max(1.0, abs(la)) + self.venues_step() / 2
        how = "; the second leg was crossed" if crossed else "; both legs filled as maker"
        self.note(f"entry ({tag})", PASS if la > 0 > sh and equal else FAIL,
                  f"the venues themselves hold {long_venue} {la:+g} and {short_venue} {sh:+g} (the engine counted "
                  f"{eng.st.size:g} a leg){how}")
        # ---- the venues' own stop and take profit, on both
        placed = await self.drive(
            eng, lambda: all(eng.st.stops_ok.get(n) for n in self.venues) or eng.st.phase != "open", STOPS_LIMIT_S)
        ok = placed and eng.st.phase == "open" and all(eng.st.stops_ok.get(n) for n in self.venues)
        listed = {n: len(await g.listed()) for n, g in self.ground.items()}
        self.note(f"stops ({tag})", PASS if ok else FAIL,
                  ("both venues took the stop and take profit" if ok else
                   f"a venue refused them (the engine then closes the position: phase {eng.st.phase})")
                  + "; open orders each venue lists: " + ", ".join(f"{n} {c}" for n, c in listed.items()))
        if not ok:
            await self.leave(eng, store, urgent=False)
            return
        # ---- hold: nothing must close it
        end = self.clock() + self.hold_s
        while self.clock() < end and eng.st.phase == "open":
            await eng.step()
            await self.sleep(2.0)
        t = await self.truth()
        held = eng.st.phase == "open" and all(abs(p) > 0 for _e, p, _o in t.values())
        self.note(f"hold ({tag})", PASS if held else FAIL,
                  f"{self.hold_s:.0f} s later the position is still open on both venues" if held else
                  f"the engine closed it by itself: {eng.st.why or eng.st.phase}")
        await self.leave(eng, store, urgent=now_exit, tag=tag)

    def venues_step(self) -> float:
        return max((float(g.step_size()) for g in self.ground.values()), default=0.0)

    async def leave(self, eng: Engine, store: MemStore, *, urgent: bool, tag: str = "") -> None:
        """Close (maker first, or taker orders at once with `close --now`) until the engine is flat; ask the venues."""
        if eng.st.phase in ("open", "entering"):
            store.send(close=True, now=urgent)
        flat = await self.drive(eng, lambda: eng.st.phase == "flat", EXIT_LIMIT_S)
        t = await self.truth()
        left = {n: (p, o) for n, (_e, p, o) in t.items() if p or o}
        closed = next((x for _c, k, x in reversed(store.events) if k == "closed"), "")
        self.note(f"exit {'with taker orders' if urgent else 'maker first'}" + (f" ({tag})" if tag else ""),
                  PASS if flat and not left else FAIL,
                  (closed or "the engine says flat") if flat and not left else
                  "NOT FLAT after the limit: the venues hold "
                  + str(left or f"nothing, but the engine is in {eng.st.phase}"))

    # ---------------------------------------------------------------- the run
    async def cleanup(self) -> None:
        if not self.armed:
            self.rep.clean, self.rep.equity_end = True, self.rep.equity_start
            return
        for v in self.venues.values():
            with contextlib.suppress(Exception):
                await v.cancel_all(self.symbol)
        for _ in range(3):
            left = {}
            with contextlib.suppress(Exception):
                for n, g in self.ground.items():
                    _e, p = await g.account()
                    p = p or float(await self.venues[n].position(self.symbol) or 0.0)
                    if p:
                        left[n] = p
            if not left:
                break
            for n, p in left.items():
                with contextlib.suppress(Exception):
                    top = await self.venues[n].top(self.symbol)
                    if top is not None:
                        side = SELL if p > 0 else BUY
                        worst = top.bid * 0.99 if side == SELL else top.ask * 1.01
                        await self.venues[n].taker(self.symbol, side, abs(p), worst, True)
            await self.sleep(3.0)
        orders, left = [], {"?": 1.0}
        with contextlib.suppress(Exception):
            await self.sleep(2.0)
            t = await self.truth()
            self.rep.equity_end = sum(e for e, _p, _o in t.values())
            left = {n: p for n, (_e, p, _o) in t.items() if p}
            orders = [n for n, (_e, _p, o) in t.items() if o]
        self.rep.clean = not left and not orders
        self.note("end", PASS if self.rep.clean else FAIL, "flat on both venues, no orders" if self.rep.clean else
                  f"positions {left}, orders on {orders}: close them in the Arcus and Lighter apps now")

    async def run(self) -> Report:
        try:
            await self.step_connect()
            for long_venue, short_venue, now_exit in (("arcus", "lighter", False), ("lighter", "arcus", True)):
                await self.guard()
                try:
                    await self.position_round(long_venue, short_venue, now_exit)
                except Abort:
                    raise
                except Exception as e:      # one round failing is a finding, not a reason to skip the other
                    self.note(f"round (long {long_venue})", FAIL, f"{type(e).__name__}: {str(e)[:200]}")
                    await self.cleanup_between()
        except Abort as e:
            self.rep.aborted = str(e)
            self.say(f"  STOPPING: {e}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.rep.aborted = "interrupted"
            self.say("  STOPPING: interrupted")
        except Exception as e:
            self.rep.aborted = f"{type(e).__name__}: {str(e)[:200]}"
            self.say(f"  STOPPING: {self.rep.aborted}")
        finally:
            await self.cleanup()
            for v in self.venues.values():
                with contextlib.suppress(Exception):
                    await v.stop()
        return self.rep

    async def cleanup_between(self) -> None:
        """After a round that raised: everything cancelled and closed before the next one starts."""
        armed, steps = self.armed, len(self.rep.steps)
        await self.cleanup()
        del self.rep.steps[steps:]
        self.armed = armed


def plan_text(symbol: str, max_loss: float) -> str:
    return "\n".join((
        f"LIVE TEST of the arbitrage executor on {symbol}: real orders on BOTH venues, real money, the smallest size.",
        "  The engine opens one position (long Arcus, short Lighter), checks that both venues hold it and carry the",
        "  stop and take-profit, holds it half a minute, and closes it with maker orders; then the same the other way",
        "  round, closed with taker orders at once. After each step the venues themselves are asked what they hold.",
        f"  It stops and closes everything if the two accounts together fall ${max_loss:.2f} below where they began.",
        "  Expected cost: the spread and Arcus's taker fee on four minimum-size round trips: cents.",
        "  It ends flat with no orders. Both accounts need no order or position on this market, and a few dollars of",
        "  margin. Do not trade this market by hand while it runs."))
