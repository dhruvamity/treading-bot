"""The executor: one funding arbitrage position at a time, the same size long on one venue and short on the other.

    flat -> entering -> open -> exiting -> flat

- **entering / exiting:** each leg works one post-only order at the best price and follows it (`requote_s`). When
  one leg has been ahead of the other for `chase_s`, the difference is crossed with a taker order on the leg that is
  behind, provided that costs no more than `max_cross_bp` (half its spread plus its taker fee). If it would cost
  more, the leg keeps following as maker; after `enter_timeout_s` it crosses anyway: equal legs come before cost.
- **open:** both venues hold their own stop and take profit for the whole position (`set_stops`), and the bot checks
  the same distances itself. If either leg shrinks or disappears (its stop fired, a liquidation, a manual close) the
  other leg is closed at once. Every few minutes `rank.exit_reason` decides whether to keep it; the settings are read
  every loop, so `arbitrage set max_hold_h 48` applies to the position already open.
- **an urgent exit** (a stop, a vanished leg, `arbitrage close --now`) uses taker orders on both legs, again and again,
  until both are flat.

Amounts are counted from the bot's own orders. The venues' positions are read to check them, and a difference only
counts once it has been read twice, `CONFIRM_S` apart: a position read just after a fill can be seconds old (Arcus,
2026-09-28), and acting on it would create the very imbalance it reports.

Everything the bot knows is in `State`, written to state/position.json after every step: a restart picks up where
it was.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from arbitrage.exec.venue import BUY, SELL, Spec, Top, TradeVenue, VenueDown
from arbitrage.rank import Plan, Settings, exit_reason

URGENT_BP = 50.0          # an urgent order may pay up to this from the mid
STOP_TRIES = 5            # set_stops refusals before the position is closed instead
RULE_EVERY_S = 300.0      # how often an open position is checked against the holding rules
SCAN_EVERY_S = 300.0      # how often a flat bot looks for a position to open
ORDER_LOST_S = 15.0       # an order the venue does not know after this long never arrived
CONFIRM_S = 10.0          # a position that differs from the bot's count must still differ this much later
REBALANCE_BELOW = 0.4     # flat, with one venue under this share of the money: tell the owner to move some
HINT_EVERY_S = 6 * 3600.0


@dataclass
class LegState:
    venue: str
    side: int                    # the side of the orders being worked: +1 buy, -1 sell
    target: float                # how much those orders should fill in all
    reduce: bool = False
    done: float = 0.0            # filled by orders that have ended
    value: float = 0.0           # sum of price x size of those fills
    order_id: str = ""           # the one order at work
    order_px: float = 0.0
    order_at: float = 0.0
    order_filled: float = 0.0
    order_taker: bool = False
    cancelling: bool = False
    trim_id: str = ""            # a taker order cutting this leg back to the other one's size

    @property
    def filled(self) -> float:
        return self.done + self.order_filled

    @property
    def left(self) -> float:
        return max(0.0, self.target - self.filled)

    @property
    def avg_px(self) -> float:
        return self.value / self.done if self.done > 0 else 0.0

    @property
    def busy(self) -> bool:
        return bool(self.order_id or self.trim_id)


@dataclass
class State:
    phase: str = "flat"
    symbol: str = ""
    size: float = 0.0                                  # the position held (open) or aimed at (entering)
    long_venue: str = ""
    short_venue: str = ""
    legs: dict[str, LegState] = field(default_factory=dict)
    since: float = 0.0                                 # when this phase began
    opened_at: float = 0.0
    entry: dict[str, float] = field(default_factory=dict)
    stop_dist: float = 0.0
    plan_stop: float = 0.0                             # the dynamic stop the plan gave
    liq_dist: float = 0.0
    leverage: float = 0.0
    stops_ok: dict[str, bool] = field(default_factory=dict)
    stop_fails: int = 0
    cleared: bool = False                              # exiting: the venues' stops are cancelled, positions read
    behind_since: float = 0.0                          # since when one leg has been ahead of the other
    odd: list[float] = field(default_factory=list)     # [what the venues' positions showed, since when]
    why: str = ""                                      # why it is closing
    urgent: bool = False
    paused: bool = False
    pause_after: bool = False                          # once flat again, open nothing until `arbitrage resume`
    last_hint: float = 0.0                             # when the owner was last told to move money
    plan: dict[str, Any] = field(default_factory=dict)
    money0: dict[str, float] = field(default_factory=dict)
    last_rules: float = 0.0
    last_scan: float = 0.0

    @staticmethod
    def load(d: dict[str, Any]) -> State:
        legs = {k: LegState(**v) for k, v in (d.get("legs") or {}).items()}
        return State(**{**d, "legs": legs})


class Planner(Protocol):
    async def best(self, collateral: dict[str, float]) -> Plan | None: ...
    async def edges(self, symbol: str, short_venue: str) -> tuple[float, float] | None: ...


class Store(Protocol):
    def save(self, st: State) -> None: ...
    def commands(self) -> dict[str, Any]: ...
    def event(self, kind: str, text: str, **data: Any) -> None: ...


class Engine:
    def __init__(self, venues: dict[str, TradeVenue], planner: Planner, settings: Callable[[], Settings], store: Store,
                 state: State | None = None, clock: Callable[[], float] = time.time,
                 require_native_stops: bool = True) -> None:
        self.venues = venues
        self.planner = planner
        self.settings = settings
        self.store = store
        self.st = state or State()
        self.clock = clock
        self.require_native_stops = require_native_stops
        self.specs: dict[str, Spec] = {}

    # ------------------------------------------------------------------------------------------ helpers
    def _say(self, kind: str, text: str, **data: Any) -> None:
        self.store.event(kind, text, **data)

    async def _specs(self, symbol: str) -> None:
        if not self.specs:
            for name, v in self.venues.items():
                self.specs[name] = await v.start(symbol)

    def _dust(self, venue: str, px: float, reduce: bool) -> float:
        """The smallest amount this venue can still trade: a remainder under it is left alone."""
        sp = self.specs[venue]
        if reduce:
            return max(sp.step, sp.min_size)
        return max(sp.step, sp.min_size, sp.min_notional / px if px > 0 else 0.0)

    @property
    def _tol(self) -> float:
        return max(sp.step for sp in self.specs.values()) * 1.5

    async def _tops(self) -> dict[str, Top] | None:
        out = {}
        for name, v in self.venues.items():
            t = await v.top(self.st.symbol)
            if t is None or t.bid <= 0 or t.ask < t.bid:
                return None
            out[name] = t
        return out

    async def _positions(self) -> dict[str, float] | None:
        out = {}
        for name, v in self.venues.items():
            p = await v.position(self.st.symbol)
            if p is None:
                return None
            out[name] = p
        return out

    def _confirmed(self, value: float) -> bool:
        """True once `value` (something the venues' positions show that the bot's count does not) has been read
        again at least CONFIRM_S after it was first seen."""
        now = self.clock()
        if not self.st.odd or abs(self.st.odd[0] - value) > 1e-12:
            self.st.odd = [value, now]
            return False
        return now - self.st.odd[1] >= CONFIRM_S

    async def _refresh(self, leg: LegState) -> None:
        """Bring a leg's counts up to date with its orders."""
        v = self.venues[leg.venue]
        now = self.clock()
        for attr in ("order_id", "trim_id"):
            oid = getattr(leg, attr)
            if not oid:
                continue
            o = await v.order(self.st.symbol, oid)
            if o is None:
                if now - leg.order_at > ORDER_LOST_S:       # never arrived: nothing rests, nothing filled
                    setattr(leg, attr, "")
                    if attr == "order_id":
                        leg.order_filled, leg.cancelling, leg.order_taker = 0.0, False, False
                continue
            px = o.avg_px or o.price
            if attr == "trim_id":
                if not o.open:
                    avg = leg.avg_px
                    leg.done = max(0.0, leg.done - o.filled)
                    leg.value = avg * leg.done          # what is left keeps its average price
                    leg.target = leg.filled             # the leg stays at what it has now
                    leg.trim_id = ""
                continue
            leg.order_filled = o.filled
            if not o.open:
                leg.done += o.filled
                leg.value += o.filled * px
                leg.order_id, leg.order_filled, leg.cancelling, leg.order_taker = "", 0.0, False, False

    async def _cancel(self, leg: LegState) -> None:
        if leg.order_id and not leg.order_taker and not leg.cancelling:
            await self.venues[leg.venue].cancel(self.st.symbol, leg.order_id)
            leg.cancelling = True

    async def _taker(self, leg: LegState, size: float, top: Top, bound_bp: float, *, trim: bool = False) -> None:
        """A taker order on this leg's venue. trim: the opposite side, reduce-only, cutting the leg back."""
        side = -leg.side if trim else leg.side
        worst = top.mid * (1 + side * bound_bp / 1e4)
        size = self.specs[leg.venue].floor_size(size)
        if size <= 0:
            return
        oid = await self.venues[leg.venue].taker(self.st.symbol, side, size, worst, leg.reduce or trim)
        if trim:
            leg.trim_id = oid
        else:
            leg.order_id, leg.order_px, leg.order_filled, leg.order_taker = oid, worst, 0.0, True
        leg.order_at = self.clock()

    # ------------------------------------------------------------------------------------------ one step
    async def step(self) -> None:
        st = self.st
        cmd = self.store.commands()
        if "pause" in cmd:
            st.paused = bool(cmd["pause"])
            self._say("pause", "no new position will be opened" if st.paused else "looking for positions again")
        try:
            if st.phase == "flat":
                await self._flat()
            elif st.phase == "entering":
                if cmd.get("close"):
                    self._begin_exit("closed by command", urgent=True)
                else:
                    await self._work()
            elif st.phase == "open":
                await self._watch(cmd)
            elif st.phase == "exiting":
                if cmd.get("close") and cmd.get("now"):
                    st.urgent = True
                await self._work()
        except VenueDown as e:
            self._say("venue_down", str(e))      # nothing more this loop; the next one tries again
        self.store.save(self.st)

    # ------------------------------------------------------------------------------------------ flat
    async def _flat(self) -> None:
        st, now = self.st, self.clock()
        if st.paused or now - st.last_scan < SCAN_EVERY_S:
            return
        st.last_scan = now
        money = {}
        for name, v in self.venues.items():
            c = await v.free_collateral()
            if c is None:
                return
            money[name] = c
        total = sum(money.values())
        if total > 0 and min(money.values()) / total < REBALANCE_BELOW and now - st.last_hint > HINT_EVERY_S:
            # a closed position leaves its gain on one venue and its loss on the other; the smaller balance sets
            # the next position, and only the owner can move money between the venues
            low = min(money, key=lambda v: money[v])
            high = max(money, key=lambda v: money[v])
            st.last_hint = now
            self._say("rebalance", f"{low} has ${money[low]:,.2f}, {high} ${money[high]:,.2f}: the next position is "
                                   f"sized by the smaller. Moving ${(money[high] - money[low]) / 2:,.2f} from {high} "
                                   f"to {low} makes them equal")
        p = await self.planner.best(money)
        if p is None or not p.go:
            return
        self.specs.clear()
        await self._specs(p.symbol)
        for name, v in self.venues.items():       # the account must be empty in this market: nothing here is ours yet
            pos = await v.position(p.symbol)
            if pos is None:
                return
            if abs(pos) >= self.specs[name].step:
                self._say("refused", f"{p.symbol}: {name} already holds {pos:g}; not opening over a position that "
                                     "is not the bot's")
                return
        size = min(self.specs[v].floor_size(p.size) for v in self.venues)
        px = min(x["entry"] for x in p.prices.values()) if p.prices else 0.0
        if size <= 0 or any(not self.specs[v].tradeable(size, px) for v in self.venues):
            return
        for v in self.venues.values():
            await v.cancel_all(p.symbol)
            await v.set_leverage(p.symbol, p.leverage)
        self.st = st = State(paused=st.paused, last_scan=now, last_hint=st.last_hint)
        st.phase, st.since, st.size, st.symbol = "entering", now, size, p.symbol
        st.long_venue, st.short_venue = p.long_venue, p.short_venue
        st.legs = {p.long_venue: LegState(p.long_venue, BUY, size), p.short_venue: LegState(p.short_venue, SELL, size)}
        st.plan_stop = st.stop_dist = p.stop_dist
        st.liq_dist, st.leverage, st.money0 = p.liq_dist, p.leverage, money
        st.plan = {"edge_apr": p.edge_apr, "notional": p.notional, "income_day": p.income_day,
                   "breakeven_h": p.breakeven_h}
        self._say("entering", f"{p.symbol}: long {p.long_venue}, short {p.short_venue}, {size:g} a leg "
                              f"(${p.notional:,.0f}) at {p.leverage:.1f}x; pays about ${p.income_day:.2f} a day",
                  plan=st.plan)

    def _begin_exit(self, why: str, *, urgent: bool) -> None:
        st = self.st
        st.phase, st.since, st.why, st.urgent = "exiting", self.clock(), why, urgent
        st.behind_since, st.cleared, st.odd, st.stops_ok = 0.0, False, [], {}
        st.legs = {st.long_venue: LegState(st.long_venue, SELL, 0.0, reduce=True),
                   st.short_venue: LegState(st.short_venue, BUY, 0.0, reduce=True)}
        self._say("exiting", f"{st.symbol}: closing ({why})" + (", with taker orders" if urgent else ""))

    def _flat_now(self) -> None:
        if self.st.pause_after:
            self._say("pause", "paused: the last position was closed for a reason that will repeat; `arbitrage resume` "
                               "once it is sorted out")
        self.st = State(paused=self.st.paused or self.st.pause_after, last_scan=self.clock(),
                        last_hint=self.st.last_hint)
        self.specs.clear()

    # ------------------------------------------------------------------------------------------ entering, exiting
    async def _work(self) -> None:
        st, s, now = self.st, self.settings(), self.clock()
        await self._specs(st.symbol)
        entering = st.phase == "entering"
        if not entering and not st.cleared:
            # Everything the bot has out goes first (the venues' stops, and an entry's orders when the exit began
            # during one), so that nothing fires into the exit. What to close is then read from the venue.
            for v in self.venues.values():
                await v.cancel_all(st.symbol)
            pos = await self._positions()
            if pos is None:
                return
            for name, p in pos.items():
                st.legs[name] = LegState(name, SELL if p > 0 else BUY, abs(p), reduce=True)
            st.cleared = True
        tops = await self._tops()
        if tops is None:
            return
        legs = list(st.legs.values())
        for leg in legs:
            await self._refresh(leg)
        late = now - st.since >= s.enter_timeout_s
        dust = {leg.venue: self._dust(leg.venue, tops[leg.venue].mid, leg.reduce) for leg in legs}
        idle = not any(leg.busy for leg in legs)

        if st.urgent:                            # each leg out with taker orders, without waiting for the other
            for leg in legs:
                if leg.order_id and not leg.order_taker:
                    await self._cancel(leg)
                elif not leg.busy and leg.left >= dust[leg.venue]:
                    await self._taker(leg, leg.left, tops[leg.venue], URGENT_BP)
            if idle and all(leg.left < dust[leg.venue] for leg in legs):
                await self._finish()
            return

        # ---- one leg ahead of the other: after chase_s the one behind crosses the spread
        lead, lag = (legs[0], legs[1]) if legs[0].filled >= legs[1].filled else (legs[1], legs[0])
        gap = min(lead.filled - lag.filled, lag.left)
        if gap >= dust[lag.venue]:
            st.behind_since = st.behind_since or now
            cost = tops[lag.venue].spread_bp / 2 + self.specs[lag.venue].taker_bp
            if late or (now - st.behind_since >= s.chase_s and cost <= s.max_cross_bp):
                if lag.order_id and not lag.order_taker:        # its maker order goes first
                    await self._cancel(lag)
                elif not lag.busy:
                    self._say("cross", f"{st.symbol}: {lag.venue} is {gap:g} behind after "
                                       f"{now - st.behind_since:.0f} s: crossing there (about {cost:.1f} bp)")
                    await self._taker(lag, gap, tops[lag.venue], URGENT_BP if late else max(s.max_cross_bp, cost))
                return
        else:
            st.behind_since = 0.0

        if late:                                 # out of time: no new maker orders, what is out is cancelled
            for leg in legs:
                await self._cancel(leg)
            if idle:
                await self._finish()
            return

        # ---- the maker orders: one per leg, at the best price on its side
        for leg in legs:
            top, sp = tops[leg.venue], self.specs[leg.venue]
            want = top.bid if leg.side == BUY else top.ask
            if leg.order_id:
                if not leg.order_taker and abs(leg.order_px - want) > sp.tick / 2 and now - leg.order_at >= s.requote_s:
                    await self._cancel(leg)
            elif not leg.trim_id and leg.left >= dust[leg.venue]:
                v = self.venues[leg.venue]
                try:
                    leg.order_id = await v.maker(st.symbol, leg.side, sp.floor_size(leg.left), want, leg.reduce)
                except VenueDown:
                    # the order may have arrived all the same. Nothing else of the bot's rests in this market now,
                    # so everything there is cancelled; _finish checks the position before anything is counted on.
                    await v.cancel_all(st.symbol)
                    raise
                leg.order_px, leg.order_at, leg.order_filled, leg.order_taker = want, now, 0.0, False
        if not any(leg.busy for leg in legs) and all(leg.left < dust[leg.venue] for leg in legs):
            await self._finish()

    async def _finish(self) -> None:
        """Both legs' orders are done: check the venues' own positions, then open or go flat."""
        st, now = self.st, self.clock()
        pos = await self._positions()
        if pos is None:
            return
        long_, short = pos[st.long_venue], pos[st.short_venue]
        net = long_ + short
        tol = self._tol
        if st.phase == "entering":
            counted = min(leg.filled for leg in st.legs.values())
            if abs(net) > tol or abs(min(abs(long_), abs(short)) - counted) > tol:
                # the venues do not show what the bot counted: wait for the same reading twice, then make the legs
                # equal by cutting the larger one back
                if not self._confirmed(round(net, 12)):
                    return
                if abs(net) > tol:
                    big = st.long_venue if net > 0 else st.short_venue
                    top = await self.venues[big].top(st.symbol)
                    leg = st.legs[big]
                    if top is not None and not leg.busy:
                        self._say("unequal", f"{st.symbol}: {st.long_venue} {long_:g}, {st.short_venue} {short:g}: "
                                             f"cutting {abs(net):g} on {big}")
                        avg = leg.avg_px or leg.order_px
                        leg.target = leg.done = abs(pos[big])        # the venue's count, at the bot's average price
                        leg.value = avg * leg.done
                        await self._taker(leg, abs(net), top, URGENT_BP, trim=True)
                        st.odd = []
                    return
            st.odd = []
            size = min(abs(long_), abs(short))
            if size < tol:
                self._say("not_opened", f"{st.symbol}: nothing filled; staying flat")
                for v in self.venues.values():
                    await v.cancel_all(st.symbol)
                self._flat_now()
                return
            if long_ < 0 or short > 0:
                self._begin_exit("the entry ended with a leg on the wrong side", urgent=True)
                return
            st.phase, st.since, st.opened_at, st.size = "open", now, now, size
            for name, leg in st.legs.items():
                st.entry[name] = leg.avg_px or leg.order_px
            st.stops_ok, st.stop_fails, st.last_rules = {}, 0, now
            self._say("open", f"{st.symbol}: open, {size:g} a leg; entries "
                      + ", ".join(f"{name} {px:,.6g}" for name, px in st.entry.items()), entry=dict(st.entry))
            return
        if max(abs(long_), abs(short)) > tol:    # exiting and something is left: again, as takers
            if not self._confirmed(round(abs(long_) + abs(short), 12)):
                return
            for name, p in pos.items():
                st.legs[name] = LegState(name, SELL if p > 0 else BUY, abs(p), reduce=True)
            st.urgent, st.odd = True, []
            return
        money = {}
        for name, v in self.venues.items():
            await v.cancel_all(st.symbol)
            c = await v.free_collateral()
            money[name] = c if c is not None else st.money0.get(name, 0.0)
        parts = {v: money[v] - st.money0.get(v, money[v]) for v in money}
        hours = (now - st.opened_at) / 3600 if st.opened_at else 0.0
        self._say("closed", f"{st.symbol}: closed ({st.why}); result ${sum(parts.values()):+.2f} ("
                  + ", ".join(f"{v} ${x:+.2f}" for v, x in parts.items()) + f") after {hours:.1f} h",
                  result=sum(parts.values()), parts=parts, hours=hours, money=money, symbol=st.symbol)
        self._flat_now()

    # ------------------------------------------------------------------------------------------ open
    def stop_prices(self, venue: str) -> tuple[float, float]:
        """(stop, take profit) for this venue's leg."""
        st = self.st
        side = 1.0 if venue == st.long_venue else -1.0
        e = st.entry[venue]
        return e * (1 - side * st.stop_dist), e * (1 + side * st.stop_dist)

    async def _watch(self, cmd: dict[str, Any]) -> None:
        st, s, now = self.st, self.settings(), self.clock()
        await self._specs(st.symbol)
        if cmd.get("close"):
            self._begin_exit("closed by command", urgent=bool(cmd.get("now")))
            return
        # the owner's stop setting applies to the open position too
        want = min(s.stop_pct / 100, 0.8 * st.liq_dist) if s.stop_pct > 0 else st.plan_stop
        if abs(want - st.stop_dist) > 1e-9:
            st.stop_dist = want
            for v in self.venues.values():
                await v.cancel_all(st.symbol)
            st.stops_ok = {}
            self._say("stop_moved", f"{st.symbol}: stop and take profit now {want * 100:.2f}% from the entry")
        pos = await self._positions()
        if pos is None:
            return
        short_of = [n for n in self.venues if abs(pos[n]) < st.size - self._tol]
        if short_of:
            if self._confirmed(round(sum(abs(pos[n]) for n in self.venues), 12)):
                n = short_of[0]
                self._begin_exit(f"{n} holds {pos[n]:g}, not {st.size:g}: its stop fired or it was closed there",
                                 urgent=True)
            return
        st.odd = []
        tops = await self._tops()
        if tops is None:
            return
        for name, t in tops.items():
            move = t.mid / st.entry[name] - 1
            if abs(move) >= st.stop_dist:
                self._begin_exit(f"stop: {name} moved {move * 100:+.2f}% from the entry", urgent=True)
                return
        for name, v in self.venues.items():
            if not st.stops_ok.get(name):
                stop, take = self.stop_prices(name)
                ok = await v.set_stops(st.symbol, pos[name], stop, take)
                st.stops_ok[name] = ok
                if ok:
                    self._say("stops", f"{st.symbol}: {name} stop {stop:,.6g}, take profit {take:,.6g}")
                else:
                    st.stop_fails += 1
                    if self.require_native_stops and st.stop_fails >= STOP_TRIES:
                        # closing and opening again would only pay the fills a second time: stay out until the
                        # owner has looked (`arbitrage resume`)
                        st.pause_after = True
                        self._begin_exit(f"{name} would not take the stop orders", urgent=False)
                        return
        held_h = (now - st.opened_at) / 3600
        if s.max_hold_h > 0 and held_h >= s.max_hold_h:
            # the time limit needs no market data: checked every loop, so `arbitrage set max_hold_h` acts at once and a
            # venue that cannot be read does not keep the position past it
            self._begin_exit(exit_reason(held_h, 0.0, 0.0, s), urgent=False)
            return
        if now - st.last_rules >= RULE_EVERY_S:
            st.last_rules = now
            e = await self.planner.edges(st.symbol, st.short_venue)
            if e is not None:
                why = exit_reason(held_h, e[0], e[1], s)
                if why:
                    self._begin_exit(why, urgent=False)

    def snapshot(self) -> dict[str, Any]:
        return asdict(self.st)
