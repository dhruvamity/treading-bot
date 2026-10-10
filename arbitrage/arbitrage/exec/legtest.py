"""`arbitrage livetest`: the Lighter leg of the executor (exec/lighter.py), once, on the real venue, at the smallest
order Lighter takes.

Until this runs, the adapter has only met fakes. It goes through what the engine asks of it, in the engine's order:
a maker order that rests (with the 5.5-minute expiry that clears it if this program dies), the cancel and re-place the
engine does before that expiry, a taker order that opens a position, the position as the adapter reports it, the
stop-loss and take-profit pair, cancel-all, the taker order that closes. After each request it asks Lighter itself
(REST) what happened and compares that with what the adapter says.

The owner runs it (ARB_LIVE=1 and LIVE typed); it refuses an account that has an order or a position on the market,
before it sends anything; it stops when the account is `max_loss` down; it always ends flat with no orders. With a
zero-fee account the cost is the spread on one minimum order, twice: cents. It does not touch Arcus, and it does not
run the two-leg engine: a live arbitrage run is the owner's own step after this.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbitrage.exec.venue import BUY, SELL, OrderInfo

FAR = (0.02, 0.01, 0.005)        # how far under the bid the resting test order is tried, widest first
TAKER_SLIP = 0.003
AWAY = 0.03                      # the stop and the take-profit, this far from the entry: they never fire here
PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


class Abort(Exception):
    pass


@dataclass
class Report:
    symbol: str
    account: int = 0
    started: float = 0.0
    steps: list[tuple[str, str, str]] = field(default_factory=list)      # (name, result, what happened)
    equity_start: float | None = None
    equity_end: float | None = None
    aborted: str = ""
    clean: bool = False

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [s for s in self.steps if s[1] == FAIL]

    def text(self) -> str:
        n = {r: sum(1 for s in self.steps if s[1] == r) for r in (PASS, FAIL, INFO)}
        cost = (f"Cost ${self.equity_start - self.equity_end:+.4f} (equity ${self.equity_start:.4f} to "
                f"${self.equity_end:.4f})" if self.equity_start is not None and self.equity_end is not None else "")
        out = [f"# Arbitrage live test, the Lighter leg: {self.symbol}, account {self.account}, "
               f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(self.started))}", "",
               f"{n[PASS]} passed, {n[FAIL]} failed, {n[INFO]} notes. {cost}".rstrip(),
               ("Ended flat with no orders." if self.clean else "DID NOT END CLEAN: look at the Lighter app now."),
               *([f"Stopped early: {self.aborted}"] if self.aborted else []), "",
               "| Step | Result | What happened |", "|---|---|---|"]
        out += [f"| {a} | {b} | {c} |" for a, b, c in self.steps]
        return "\n".join(out) + "\n"


class LegTest:
    def __init__(self, venue: Any, symbol: str, say: Callable[[str], None] = print, *, max_loss: float = 1.0,
                 expiry: bool = False, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time) -> None:
        self.v = venue                     # a LighterTrade (exec/lighter.py)
        self.symbol = symbol
        self.say = say
        self.max_loss = max_loss
        self.expiry = expiry               # also wait for Lighter to expire a maker order (6 more minutes)
        self.sleep = sleep
        self.clock = clock
        self.rep = Report(symbol, started=clock())
        self.armed = False                 # True once the account was seen flat with no orders
        self.size = 0.0
        self.last = ""                     # why the last maker order did not rest

    # ---------------------------------------------------------------- reading Lighter itself
    def note(self, name: str, result: str, detail: str) -> None:
        self.rep.steps.append((name, result, detail))
        self.say(f"  {result:<4} {name}: {detail}")

    async def listed(self) -> list[dict[str, Any]]:
        r = await self.v.rest.active_orders(self.v.account, self.v.market.market_id)
        return list(r.get("orders") or [])

    async def account(self) -> tuple[float, float]:
        """(equity, signed position) from Lighter's account read."""
        r = await self.v.rest.account(self.v.account)
        acc = (r.get("accounts") or [{}])[0]
        pos = 0.0
        for p in acc.get("positions") or []:
            if int(p.get("market_id", -1)) == self.v.market.market_id:
                pos = float(p.get("position") or 0) * (1 if int(p.get("sign") or 1) >= 0 else -1)
        return float(acc.get("total_asset_value") or 0), pos if abs(pos) > self.v.market.step / 2 else 0.0

    async def until(self, cond: Callable[[], Awaitable[bool]], timeout: float = 12.0, every: float = 1.0) -> bool:
        end = self.clock() + timeout
        while True:
            if await cond():
                return True
            if self.clock() >= end:
                return False
            await self.sleep(every)

    async def info(self, oid: str) -> OrderInfo:
        got: OrderInfo | None = await self.v.order(self.symbol, oid)
        if got is None:
            raise Abort(f"the adapter does not know its own order {oid}")
        return got

    async def has(self, oid: str) -> bool:
        return any(str(o.get("client_order_index")) == oid for o in await self.listed())

    async def guard(self) -> None:
        eq, _pos = await self.account()
        if self.rep.equity_start is not None and self.rep.equity_start - eq > self.max_loss:
            raise Abort(f"the account is down ${self.rep.equity_start - eq:.2f}, more than the ${self.max_loss:.2f} "
                        "this test may lose")

    # ---------------------------------------------------------------- the steps
    async def step_connect(self) -> None:
        from lighter_bot.livetest import min_qty

        await self.v.start(self.symbol)
        self.rep.account = self.v.account
        eq, pos = await self.account()
        orders = await self.listed()
        self.rep.equity_start = eq
        if pos or orders:
            raise Abort(f"the account already has {'a position' if pos else ''}{' and ' if pos and orders else ''}"
                        f"{f'{len(orders)} open order(s)' if orders else ''} on {self.symbol}: the test would cancel "
                        "and close them. Nothing was sent")
        self.armed = True
        top = None
        for _ in range(15):
            top = await self.v.top(self.symbol)
            if top is not None:
                break
            await self.sleep(1.0)
        if top is None:
            raise Abort("no order book from Lighter's feed after 15 s")
        m = self.v.market
        self.size = min_qty(m.min_base, m.min_quote, m.step, top.ask)
        told = await self.v.position(self.symbol)
        self.note("connect", PASS if told == 0 else FAIL,
                  f"equity ${eq:.2f}; {self.symbol} {top.bid:g}/{top.ask:g}; one test order is {self.size:g} "
                  f"(${self.size * top.ask:.2f}); Lighter says flat with no orders, the adapter says position {told}")

    async def rest_far(self) -> tuple[str, float]:
        """A maker order far under the bid, through the adapter: (its id, its price), ("", 0) when all were refused."""
        top = await self.v.top(self.symbol)
        if top is None:
            return "", 0.0
        why = ""
        for frac in FAR:
            px = top.bid * (1 - frac)
            oid = str(await self.v.maker(self.symbol, BUY, self.size, px, False))

            async def there(oid: str = oid) -> bool:
                return await self.has(oid) or not (await self.info(oid)).open

            await self.until(there)
            got = await self.info(oid)
            if got.open and await self.has(oid):
                return oid, got.price
            why = got.note
        self.last = why
        return "", 0.0

    async def step_maker(self) -> None:
        oid, px = await self.rest_far()
        if not oid:
            self.note("maker order", FAIL, f"a post-only buy under the bid did not rest: {self.last}")
            return
        row = next((o for o in await self.listed() if str(o.get("client_order_index")) == oid), {})
        exp = next((row[k] for k in ("order_expiry", "expiry", "expired_at") if row.get(k)), None)
        left = ""
        if exp:
            sec = float(exp) / (1000 if float(exp) > 1e11 else 1) - self.clock()
            left = f"; Lighter gives it {sec:.0f} s to live" + ("" if 240 <= sec <= 400 else " (NOT the 5.5 minutes)")
        self.note("maker order", PASS if "NOT" not in left else FAIL,
                  f"post-only buy at {px:g}: on Lighter's book, and the adapter says open{left}")
        # what the engine does before the order's expiry: cancel it, place it again
        await self.v.cancel(self.symbol, oid)

        async def ended() -> bool:
            return not (await self.info(oid)).open and not await self.has(oid)

        gone = await self.until(ended)
        new, _px = await self.rest_far() if gone else ("", 0.0)
        why = (await self.info(oid)).note or "no reason"
        self.note("replaced as the engine does", PASS if gone and new else FAIL,
                  (f"cancelled: off Lighter's book, the adapter says ended ({why}); placed again: on the book"
                   + ("" if new else " NO")) if gone else
                  "the cancelled order is STILL on the book, or the adapter still says open")
        if new:
            await self.v.cancel(self.symbol, new)

            async def ended2() -> bool:
                return not (await self.info(new)).open and not await self.has(new)

            self.note("cancel", PASS if await self.until(ended2) else FAIL, "off the book, and the adapter says ended")

    async def step_expiry(self) -> None:
        from lighter_bot.venue import consts as C

        oid, _px = await self.rest_far()
        if not oid:
            self.note("expiry seen by the adapter", FAIL, "no resting order to watch")
            return
        self.say(f"       waiting up to {C.QUOTE_EXPIRY_S + 90:.0f} s for Lighter to expire it...")
        t0 = self.clock()

        async def ended() -> bool:
            return not (await self.info(oid)).open

        gone = await self.until(ended, C.QUOTE_EXPIRY_S + 90, 10.0)
        note = (await self.info(oid)).note
        self.note("expiry seen by the adapter", PASS if gone and not await self.has(oid) else FAIL,
                  f"after {self.clock() - t0:.0f} s Lighter removed the order by itself and the adapter says ended "
                  f"({note}): the engine would place it again" if gone else "the adapter STILL says open")
        if not gone:
            await self.v.cancel(self.symbol, oid)

    async def step_position(self) -> None:
        top = await self.v.top(self.symbol)
        if top is None:
            self.note("taker order", FAIL, "no order book")
            return
        oid = str(await self.v.taker(self.symbol, BUY, self.size, top.ask * (1 + TAKER_SLIP), False))
        step = self.v.market.step

        async def filled() -> bool:
            return (await self.info(oid)).filled >= self.size - step / 2

        got = await self.until(filled)

        async def held() -> bool:
            return (await self.account())[1] > 0

        on_venue = await self.until(held)
        _eq, pos = await self.account()

        async def told() -> bool:
            return abs((await self.v.position(self.symbol) or 0.0) - pos) < step / 2

        agree = pos > 0 and await self.until(told, 25.0)
        o = await self.info(oid)
        says = await self.v.position(self.symbol)
        self.note("taker order opens a position", PASS if got and on_venue and agree else FAIL,
                  f"Lighter says position {pos:+g}; the adapter says the order filled {o.filled:g}"
                  + (f" at {o.avg_px:g}" if o.avg_px else " (no fill price)") + f" and the position is {says}"
                  + ("" if o.note in ("", "filled") else f"; ended as {o.note}"))
        if not pos:
            return
        entry = o.avg_px or top.ask
        ok = await self.v.set_stops(self.symbol, pos, entry * (1 - AWAY), entry * (1 + AWAY))

        async def two() -> bool:
            return len(await self.listed()) >= 2

        both = await self.until(two)
        kinds = sorted(f"{o.get('type')}/{o.get('status')}" for o in await self.listed())
        self.note("stop and take-profit", PASS if ok and both else FAIL,
                  f"the adapter says {'placed' if ok else 'REFUSED'}; Lighter lists: {', '.join(kinds) or 'nothing'}")
        await self.v.cancel_all(self.symbol)

        async def none() -> bool:
            return not await self.listed()

        self.note("cancel-all", PASS if await self.until(none) else FAIL, "Lighter lists none after it")
        top = await self.v.top(self.symbol) or top
        await self.v.taker(self.symbol, SELL, pos, top.bid * (1 - TAKER_SLIP), True)

        async def flat() -> bool:
            return (await self.account())[1] == 0

        closed = await self.until(flat)

        async def told_flat() -> bool:
            return (await self.v.position(self.symbol)) == 0

        self.note("taker order closes it", PASS if closed and await self.until(told_flat, 25.0) else FAIL,
                  f"Lighter says position {(await self.account())[1]:+g}; the adapter says "
                  f"{await self.v.position(self.symbol)}")

    # ---------------------------------------------------------------- the run
    async def cleanup(self) -> None:
        if not self.armed:          # nothing was sent: what is on the account is the owner's, and stays
            self.rep.clean, self.rep.equity_end = True, self.rep.equity_start
            return
        left, orders = 1.0, [{}]
        with contextlib.suppress(Exception):
            await self.v.cancel_all(self.symbol)
        with contextlib.suppress(Exception):
            for _ in range(3):
                _eq, left = await self.account()
                told = await self.v.position(self.symbol) or 0.0
                left = left or told         # Lighter's read can trail its own stream after a fill
                if not left:
                    break
                top = await self.v.top(self.symbol)
                if top is None:
                    break
                side = SELL if left > 0 else BUY
                await self.v.taker(self.symbol, side, abs(left), top.bid * (1 - TAKER_SLIP) if side == SELL
                                   else top.ask * (1 + TAKER_SLIP), True)
                await self.sleep(3.0)
        with contextlib.suppress(Exception):
            await self.sleep(2.0)
            orders = await self.listed()
            self.rep.equity_end, left = await self.account()
        self.rep.clean = not left and not orders
        self.note("end", PASS if self.rep.clean else FAIL, "flat, no orders" if self.rep.clean else
                  f"position {left:+g}, {len(orders)} order(s) STILL OPEN: close them in the Lighter app now")

    async def run(self) -> Report:
        steps = [self.step_maker, self.step_position] + ([self.step_expiry] if self.expiry else [])
        try:
            await self.step_connect()
            for fn in steps:
                await self.guard()
                try:
                    await fn()
                except Abort:
                    raise
                except Exception as e:      # one step failing is a finding, not a reason to leave the rest untested
                    self.note(fn.__name__.removeprefix("step_"), FAIL, f"{type(e).__name__}: {str(e)[:200]}")
        except Abort as e:
            self.rep.aborted = str(e)
            self.say(f"  STOPPING: {e}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.rep.aborted = "interrupted"
            self.say("  STOPPING: interrupted")
        except Exception as e:              # the venue did not answer, a missing key: nothing more can be tried
            self.rep.aborted = f"{type(e).__name__}: {str(e)[:200]}"
            self.say(f"  STOPPING: {self.rep.aborted}")
        finally:
            await self.cleanup()
            with contextlib.suppress(Exception):
                await self.v.stop()
        return self.rep


def plan_text(symbol: str, expiry: bool, max_loss: float) -> str:
    return "\n".join((
        f"LIVE TEST of the arbitrage's Lighter leg on {symbol}: real orders, real money, the smallest size "
        "Lighter takes.",
        "  It rests a maker order far under the price, cancels it and places it again as the engine does, buys one",
        "  minimum order with a taker order, places the stop-loss and take-profit pair, cancels them, and sells."
        + (" Then it waits about 6 minutes to see Lighter expire a maker order." if expiry else ""),
        f"  It stops and closes everything if the account falls ${max_loss:.2f} below where it started.",
        "  Expected cost with a zero-fee account: the spread on one minimum order, twice (cents).",
        "  It ends flat with no orders, and sends nothing to Arcus. Do not trade this market by hand while it runs."))


def write_report(rep: Report, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    p = reports_dir / f"livetest-{time.strftime('%Y%m%d-%H%M%SZ', time.gmtime(rep.started))}.md"
    p.write_text(rep.text())
    return p
