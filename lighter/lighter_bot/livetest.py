"""`lighter livetest [MARKET]`: every kind of request the bot sends, once, on the real venue, with the smallest order.

Until this runs, everything in lighter_bot/trade/live.py has only met fakes. The owner runs it (it needs LBOT_LIVE=1, a
passing doctor and LIVE typed at the prompt; nothing else can start it), on an account with a few dollars and no
position or orders on the market. It takes about three minutes, or nine with the dead man's switch step, and ends
flat with no orders. With a zero-fee account the cost is the spread on two or three minimum orders: cents.

Each step does one thing through the bot's own code where the bot has it (LiveExchange: post-only orders, modify,
cancel, cancel-all, the reduce-only taker exit, the leverage, the dead man's switch, the account stream), signs it
directly where only the test needs it (an order that opens with a taker order, stop and take-profit orders as the
funding arbitrage places them, a batch with a bad member), and then asks Lighter itself, over REST, what happened:

    PASS   the venue did it and the bot's own state agrees
    FAIL   it did not, or the bot's state disagrees with the venue (a bug to fix before a real run)
    INFO   something measured or learned (how far from the mark an order may rest, how a flat market is reported)
    SKIP   not run, with the reason

It stops at once, cancels everything and closes the position when the account is down more than `max_loss` from
where it started, when a step leaves it in a state it does not understand, or on Ctrl-C. The report is printed and
written to lighter/reports/livetest-<UTC time>.md (no key, no signature in it).
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lighter_bot.trade.exchange import Change, Fill
from lighter_bot.trade.live import QUOTE_EXPIRY_MS
from lighter_bot.trade.strategy import BUY, SELL, Quote
from lighter_bot.venue import consts as C
from lighter_bot.venue.rest import RESERVE, ApiError

FAR = (0.02, 0.01, 0.005, 0.002)     # how far from the touch a resting test order is tried, widest first
TRIGGER_AWAY = 0.03                  # stop and take-profit triggers this far from the entry: they never fire in a test
TRIGGER_SLIPS = (0.05, 0.01)         # the worst price past a trigger: the arbitrage's 5% first, then 1%
SIZE_X = 1.1                         # of the minimum order
TAKER_SLIP = 0.003
LIQUID_USD = 1_000_000.0
DMS_TRY_MS = (30_000, C.CANCEL_ALL_MIN_MS + 30_000)   # a short horizon first (if Lighter takes it), else the bot's own
DMS_LATE_OK_S = 60.0                 # the scheduled cancel-all may fire this long after its time and still pass
DMS_LATE_S = 300.0                   # and the test waits this long past the time before it calls it never
PASS, FAIL, INFO, SKIP = "PASS", "FAIL", "INFO", "SKIP"


class Abort(Exception):
    """Stop the test now: clean up and report."""


@dataclass
class Step:
    name: str
    result: str
    detail: str


@dataclass
class Report:
    market: str
    account: int
    started: float
    steps: list[Step] = field(default_factory=list)
    equity_start: float | None = None
    equity_end: float | None = None
    aborted: str = ""
    clean: bool = False

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if s.result == FAIL]

    def text(self) -> str:
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self.started))
        out = [f"# Lighter live test: {self.market}, account {self.account}, {when}", ""]
        cost = None if self.equity_start is None or self.equity_end is None else self.equity_start - self.equity_end
        out.append(f"{sum(s.result == PASS for s in self.steps)} passed, {len(self.failed)} failed, "
                   f"{sum(s.result == INFO for s in self.steps)} notes, {sum(s.result == SKIP for s in self.steps)} "
                   "skipped" + (f". Cost ${cost:+.4f} (equity ${self.equity_start:.4f} to ${self.equity_end:.4f})"
                                if cost is not None else ""))
        out.append("Ended flat with no orders." if self.clean else
                   "DID NOT END CLEAN: look at the account in the Lighter app now (orders, position).")
        if self.aborted:
            out.append(f"Stopped early: {self.aborted}")
        out += ["", "| Step | Result | What happened |", "|---|---|---|"]
        out += [f"| {s.name} | {s.result} | {s.detail.replace('|', '/')} |" for s in self.steps]
        return "\n".join(out) + "\n"


def min_qty(min_base: float, min_quote: float, step: float, price: float, x: float = SIZE_X) -> float:
    """The smallest order the venue takes at `price`, times x, rounded up to the size step."""
    q = max(min_base, min_quote / price) * x
    return math.ceil(q / step - 1e-9) * step


class LiveTest:
    def __init__(self, ex: Any, *, say: Callable[[str], None] = print, max_loss: float = 1.0, wait_fill_s: float = 20.0,
                 dms: bool = True, dms_only: bool = False, lev_low: float = 5.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time) -> None:
        self.ex = ex                       # a LiveExchange (lighter_bot/trade/live.py)
        self.m = ex.market
        self.say = say
        self.max_loss = max_loss
        self.wait_fill_s = wait_fill_s
        self.dms = dms or dms_only
        self.dms_only = dms_only           # the dead man's switch step alone: one far order, no trade
        self.lev_low = min(lev_low, self.m.max_leverage)
        self.sleep = sleep
        self.clock = clock
        self.rep = Report(self.m.symbol, ex.account, clock())
        self.fills: list[Fill] = []
        self.far = FAR[0]
        self.armed = False                 # True once the account was seen flat with no orders: only then may the
                                           # test (and its clean-up) cancel and close anything
        ex.fill_cbs.append(self.fills.append)

    # ---------------------------------------------------------------- reading the venue
    def note(self, name: str, result: str, detail: str) -> None:
        self.rep.steps.append(Step(name, result, detail))
        self.say(f"  {result:<4} {name}: {detail}")

    def bbo(self) -> tuple[float, float]:
        b = self.ex.feed.bbo()
        if b is None:
            raise Abort("no order book from Lighter's feed")
        return float(b[0]), float(b[1])

    def qty(self) -> float:
        return min_qty(self.m.min_base, self.m.min_quote, self.m.step, self.bbo()[0])

    async def venue_orders(self) -> list[dict[str, Any]]:
        r = await self.ex.rest.active_orders(self.ex.account, self.m.market_id)
        return list(r.get("orders") or [])

    async def venue_account(self) -> tuple[float, float, bool]:
        """(equity, signed position, is there a row for this market) from Lighter's account read."""
        r = await self.ex.rest.account(self.ex.account)
        acc = (r.get("accounts") or [{}])[0]
        pos, row = 0.0, False
        for p in acc.get("positions") or []:
            if int(p.get("market_id", -1)) == self.m.market_id:
                row = True
                pos = float(p.get("position") or 0) * (1 if int(p.get("sign") or 1) >= 0 else -1)
        return float(acc.get("total_asset_value") or 0), pos if abs(pos) > self.m.step / 2 else 0.0, row

    async def venue_schedule(self) -> tuple[int, int]:
        """(the scheduled cancel-all time Lighter holds for the account, 0 for none; its open orders in every market)."""
        r = await self.ex.rest.account(self.ex.account)
        acc = (r.get("accounts") or [{}])[0]
        return int(acc.get("cancel_all_time") or 0), int(acc.get("total_order_count") or 0)

    @staticmethod
    def listed(orders: list[dict[str, Any]], cid: int) -> bool:
        return any(int(o.get("client_order_index") or 0) == cid for o in orders)

    async def tx_note(self, tx_hash: str) -> str:
        """What Lighter says it did with one transaction, for the report."""
        try:
            r = await self.ex.rest.tx(tx_hash)
        except (ApiError, TimeoutError, OSError) as e:
            return f"its record of the transaction could not be read: {str(e)[:120]}"
        return f"transaction status {r.get('status')}, event {str(r.get('event_info') or '')[:160] or 'none'}"

    async def until(self, cond: Callable[[], Any], timeout: float, every: float = 0.5) -> bool:
        end = self.clock() + timeout
        while self.clock() < end:
            if cond():
                return True
            await self.sleep(every)
        return bool(cond())

    async def until_venue(self, cond: Callable[[list[dict[str, Any]]], bool], timeout: float = 12.0) -> bool:
        """Poll Lighter's own list of our orders (a request each time: kept slow)."""
        end = self.clock() + timeout
        while True:
            if cond(await self.venue_orders()):
                return True
            if self.clock() >= end:
                return False
            await self.sleep(2.0)

    def last_error(self) -> str:
        return self.ex.errors[-1][1] if self.ex.errors else "no answer (the request budget, or the network)"

    async def guard(self) -> None:
        eq, _pos, _row = await self.venue_account()
        if self.rep.equity_start is not None and self.rep.equity_start - eq > self.max_loss:
            raise Abort(f"the account is down ${self.rep.equity_start - eq:.2f}, more than the ${self.max_loss:.2f} "
                        "this test may lose")

    # ---------------------------------------------------------------- sending
    async def send(self, changes: list[Change]) -> bool:
        """The bot's own send, once the minute's request budget has room (the bot skips a requote that does not fit;
        a test step must not read that as a refusal)."""
        for _ in range(90):
            if self.ex.budget.allows(RESERVE):
                break
            await self.sleep(1.0)
        return bool(await self.ex.send(changes, RESERVE))

    async def rest_order(self, side: int, qty: float, price: float, tag: str, *, reduce_only: bool = False
                         ) -> int | None:
        """A post-only order through the bot's own path. Its client id, or None when Lighter refused it."""
        before = set(self.ex.orders)
        ok = await self.send([Change("new", Quote(side, price, qty, tag, reduce_only))])
        new = [c for c in self.ex.orders if c not in before]
        return new[0] if ok and new else None

    async def far_order(self, side: int, tag: str) -> tuple[int | None, float]:
        """A post-only order that will not trade: as far from the touch as Lighter lets an order rest."""
        bid, ask = self.bbo()
        for frac in [f for f in FAR if f <= self.far]:
            px = bid * (1 - frac) if side == BUY else ask * (1 + frac)
            cid = await self.rest_order(side, self.qty(), px, tag)
            if cid is not None:
                self.far = frac
                return cid, px
        return None, 0.0

    async def raw(self, txs: list[Any]) -> tuple[Any, str]:
        """Send signed transactions directly: (Lighter's answer, "" or why it refused)."""
        try:
            return await self.ex.rest.send(txs, kind=RESERVE, wait=True), ""
        except ApiError as e:
            return None, f"{e.code}: {e.message}"
        except (TimeoutError, OSError) as e:
            return None, f"network: {e}"

    def sign_order(self, side: int, qty: float, price: float, *, order_type: int, tif: int, reduce_only: bool,
                   expiry: int, trigger: float = 0.0) -> tuple[int, Any]:
        cid = self.ex.ids.take()[0]
        m = self.m
        tx = self.ex.signer.create_order(
            market=m.market_id, client_index=cid, size=m.size_int(qty), price=m.price_int(price, side_buy=side != BUY),
            is_ask=side == SELL, order_type=order_type, tif=tif, reduce_only=reduce_only, expiry=expiry,
            nonce=self.ex.nonces.take()[0], trigger_price=m.price_int(trigger, side_buy=True) if trigger else 0)
        return cid, tx

    async def taker_open(self, side: int, qty: float) -> str:
        """An IOC market order that may open a position (the bot itself only ever exits with one)."""
        bid, ask = self.bbo()
        worst = ask * (1 + TAKER_SLIP) if side == BUY else bid * (1 - TAKER_SLIP)
        _cid, tx = self.sign_order(side, qty, worst, order_type=C.ORDER_MARKET, tif=C.TIF_IOC, reduce_only=False,
                                   expiry=C.IOC_EXPIRY)
        return (await self.raw([tx]))[1]

    async def flatten(self, tries: int = 3) -> float:
        """Close whatever position there is with the bot's own reduce-only taker exit. The position left."""
        pos = 0.0
        for _ in range(tries):
            _eq, pos, _row = await self.venue_account()
            if not pos:
                return 0.0
            self.ex.taker_in_flight_until = 0.0
            await self.ex.taker(-pos)
            await self.sleep(3.0)
        return (await self.venue_account())[1]

    # ---------------------------------------------------------------- the steps
    async def step_connect(self) -> None:
        # look first, with reads only: the bot's own start cancels every order on its market
        self.ex._auth()
        eq, pos, _row = await self.venue_account()
        orders = await self.venue_orders()
        self.rep.equity_start = eq
        if pos or orders:
            raise Abort(f"the account already has {'a position' if pos else ''}{' and ' if pos and orders else ''}"
                        f"{f'{len(orders)} open order(s)' if orders else ''} on {self.m.symbol}: the test would cancel "
                        "and close them. Use a market you hold nothing on. Nothing was sent")
        self.armed = True
        await self.ex.start()
        if not await self.until(lambda: self.ex.feed.bbo() is not None, 15.0):
            raise Abort("no order book from Lighter's feed after 15 s")
        bid, ask = self.bbo()
        need = self.qty() * ask / self.lev_low * 1.5
        if eq < need:
            raise Abort(f"equity ${eq:.2f} is under the ${need:.2f} one minimum order needs at {self.lev_low:g}x with room")
        self.note("connect", PASS, f"equity ${eq:.2f}; {self.m.symbol} {bid:g}/{ask:g}; one test order is "
                                   f"{self.qty():g} (${self.qty() * ask:.2f}); flat, no orders")

    async def step_leverage(self, lev: float, name: str) -> None:
        try:
            await self.ex.set_leverage(lev)
            self.note(name, PASS, f"set to {lev:g}x (cross margin); Lighter accepted it")
        except ApiError as e:
            self.note(name, FAIL, f"{lev:g}x refused: {e.code}: {e.message}")

    async def step_limit_modify_cancel(self) -> None:
        cid, px = await self.far_order(BUY, "test-bid")
        if cid is None:
            self.note("limit order", FAIL, f"a post-only buy {FAR[-1]:.1%} under the bid was refused: {self.last_error()}")
            return
        seen = await self.until_venue(lambda os_: any(int(o.get("client_order_index") or 0) == cid for o in os_))
        streamed = await self.until(lambda: self.ex.orders[cid].state == "open", 10.0)
        self.note("limit order", PASS if seen and streamed else FAIL,
                  f"post-only buy at {px:g} ({self.far:.1%} under the bid): on Lighter's book {'yes' if seen else 'NO'}, "
                  f"the bot's order stream said open {'yes' if streamed else 'NO'}")
        if not seen:
            return
        new_px = self.bbo()[0] * (1 - self.far * 0.8)
        ok = await self.send([Change("modify", Quote(BUY, new_px, self.qty(), "test-bid"), cid=cid)])
        want = self.m.price_of(self.m.price_int(new_px, side_buy=True))
        moved = ok and await self.until_venue(lambda os_: any(
            int(o.get("client_order_index") or 0) == cid and abs(float(o.get("price") or 0) - want) < self.m.tick / 2
            for o in os_))
        self.note("modify", PASS if moved else FAIL, f"moved to {want:g}: " + ("Lighter shows the new price" if moved
                                                                               else self.last_error() if not ok else
                                                                               "Lighter still shows the old price"))
        ok = await self.send([Change("cancel", cid=cid)])
        gone = ok and await self.until_venue(lambda os_: not any(int(o.get("client_order_index") or 0) == cid for o in os_))
        done = await self.until(lambda: self.ex.orders.get(cid) is None or self.ex.orders[cid].state == "done", 10.0)
        self.note("cancel", PASS if gone and done else FAIL,
                  f"off Lighter's book {'yes' if gone else 'NO'}, the bot's order stream said done {'yes' if done else 'NO'}")

    async def step_cancel_all(self) -> None:
        a, _ = await self.far_order(SELL, "test-ask")
        b, _ = await self.far_order(BUY, "test-bid")
        if a is None or b is None:
            self.note("two orders in one go", FAIL, f"refused: {self.last_error()}")
        both = await self.until_venue(lambda os_: len(os_) >= 2)
        await self.ex.cancel_all()
        none = await self.until_venue(lambda os_: not os_)
        self.note("cancel-all (this market)", PASS if both and none else FAIL,
                  f"a buy and a sell rested ({'both seen' if both else 'NOT both seen'}); after cancel-all Lighter "
                  f"lists {'none' if none else 'SOME STILL'}")

    async def step_batch(self) -> None:
        bid, ask = self.bbo()
        q = self.qty()
        before = set(self.ex.orders)
        ok = await self.send([Change("new", Quote(BUY, bid * (1 - self.far), q, "test-bid")),
                              Change("new", Quote(SELL, ask * (1 + self.far), q, "test-ask"))])
        cids = [c for c in self.ex.orders if c not in before]
        both = ok and await self.until_venue(lambda os_: len(os_) >= 2)
        self.note("batch of two", PASS if both else FAIL, "one request, two orders: " +
                  ("both on the book" if both else self.last_error() if not ok else "NOT both on the book"))
        if cids:
            await self.send([Change("cancel", cid=c) for c in cids])
            await self.until_venue(lambda os_: not os_)
        # a batch with one transaction Lighter must refuse: does the good one land?
        cid, good = self.sign_order(BUY, q, self.bbo()[0] * (1 - self.far), order_type=C.ORDER_LIMIT,
                                    tif=C.TIF_POST_ONLY, reduce_only=False, expiry=C.ORDER_EXPIRY_DEFAULT)
        bad = self.ex.signer.cancel_order(market=self.m.market_id, index=self.ex.ids.take()[0],
                                          nonce=self.ex.nonces.take()[0])        # an order that does not exist
        _r, err = await self.raw([good, bad])
        landed = await self.until_venue(lambda os_: any(int(o.get("client_order_index") or 0) == cid for o in os_), 8.0)
        self.note("batch with a bad member", INFO,
                  f"Lighter answered {err or 'OK'}; the good order {'LANDED' if landed else 'did not land'}"
                  + (" (a batch is not all-or-nothing: the bot must check each order)" if landed and err else
                     " (the batch was refused whole)" if err and not landed else
                     " (the bad cancel was ignored)" if landed else ""))
        await self.ex.cancel_all()
        await self.until_venue(lambda os_: not os_)

    async def step_post_only_crossing(self) -> None:
        _bid, ask = self.bbo()
        n = len(self.fills)
        cid = await self.rest_order(BUY, self.qty(), ask * 1.0005, "test-cross")
        await self.sleep(3.0)
        _eq, pos, _row = await self.venue_account()
        rested = any(int(o.get("client_order_index") or 0) == cid for o in await self.venue_orders()) if cid else False
        why = self.ex.orders[cid].why_done if cid is not None and cid in self.ex.orders else ""
        if pos or len(self.fills) > n:
            self.note("post-only that would cross", FAIL, "IT TRADED: a post-only order took liquidity")
        else:
            self.note("post-only that would cross", PASS,
                      "did not trade" + (f"; Lighter ended it as {why}" if why else f"; refused: {self.last_error()}"
                                         if cid is None else "; still resting" if rested else ""))
        if rested:
            await self.ex.cancel_all()

    async def step_open(self, side: int, name: str) -> float:
        """Get a position: as the bot does (a post-only order at the touch), else with a taker order. The position."""
        bid, ask = self.bbo()
        q, n = self.qty(), len(self.fills)
        cid = await self.rest_order(side, q, bid if side == BUY else ask, "test-maker")
        filled = cid is not None and await self.until(lambda: abs(self.ex.acct.pos) > self.m.step / 2, self.wait_fill_s, 1.0)
        if cid is not None and not filled:
            await self.send([Change("cancel", cid=cid)])
            await self.sleep(2.0)
        _eq, pos, _row = await self.venue_account()
        if pos:
            f = self.fills[-1] if len(self.fills) > n else None
            self.note(f"{name} as maker", PASS, f"a post-only order at the touch filled: position {pos:+g}"
                      + (f" at {f.px:g}, fee ${f.fee:.6f}, {'maker' if f.maker else 'TAKER'}" if f else
                         "; the bot's fill stream did NOT report it"))
            return pos
        self.note(f"{name} as maker", INFO, f"not filled in {self.wait_fill_s:.0f} s at the touch (normal): cancelled")
        err = await self.taker_open(side, q)
        got = await self.until(lambda: abs(self.ex.acct.pos) > self.m.step / 2, 10.0)
        _eq, pos, _row = await self.venue_account()
        f = self.fills[-1] if len(self.fills) > n else None
        ok = bool(pos) and (pos > 0) == (side == BUY)
        self.note(f"{name} with a taker order", PASS if ok and got and f else FAIL,
                  (f"position {pos:+g}" + (f" at {f.px:g}, fee ${f.fee:.6f}" if f else "")
                   + ("" if got else "; the bot's position stream did NOT show it")
                   + ("" if f else "; the bot's fill stream did NOT report it")) if ok else f"no position: {err or 'no fill'}")
        return pos

    async def step_stops(self, pos: float) -> None:
        """A stop-loss and a take-profit as the funding arbitrage places them (arbitrage/exec/lighter.py)."""
        close = SELL if pos > 0 else BUY
        mid = sum(self.bbo()) / 2
        placed = ""
        errs: list[str] = []
        for slip in TRIGGER_SLIPS:
            txs = []
            for order_type, away in ((C.ORDER_STOP_LOSS, -TRIGGER_AWAY), (C.ORDER_TAKE_PROFIT, TRIGGER_AWAY)):
                trig = mid * (1 + away * (1 if pos > 0 else -1))
                _cid, tx = self.sign_order(close, abs(pos), trig * (1 + close * slip), order_type=order_type,
                                           tif=C.TIF_IOC, reduce_only=True, expiry=C.ORDER_EXPIRY_DEFAULT, trigger=trig)
                txs.append(tx)
            _r, err = await self.raw(txs)
            if not err:
                placed = f"worst price {slip:.0%} past the trigger"
                break
            errs.append(f"{slip:.0%}: {err}")
        if not placed:
            self.note("stop and take-profit orders", FAIL, "refused: " + "; ".join(errs))
            return
        listed = await self.until_venue(lambda os_: len(os_) >= 2, 10.0)
        kinds = sorted({str(o.get("type")) + "/" + str(o.get("status")) for o in await self.venue_orders()})
        self.note("stop and take-profit orders", PASS if listed else FAIL,
                  f"placed with {placed}; Lighter lists them {'yes' if listed else 'NO'}: {', '.join(kinds) or 'nothing'}")
        await self.ex.cancel_all()
        gone = await self.until_venue(lambda os_: not os_)
        self.note("cancel-all removes the stops", PASS if gone else FAIL, "none left" if gone else "SOME STILL LISTED")

    async def step_close(self, pos: float, name: str) -> None:
        """Close as the bot does: a reduce-only post-only order at the touch, then the reduce-only taker exit."""
        bid, ask = self.bbo()
        side = SELL if pos > 0 else BUY
        cid = await self.rest_order(side, abs(pos), ask if side == SELL else bid, "exit-test", reduce_only=True)
        filled = cid is not None and await self.until(lambda: abs(self.ex.acct.pos) < self.m.step / 2, self.wait_fill_s, 1.0)
        if cid is not None and not filled:
            await self.send([Change("cancel", cid=cid)])
            await self.sleep(2.0)
        self.note(f"{name} as maker", PASS if filled else INFO, "a reduce-only post-only order at the touch filled" if filled
                  else f"not filled in {self.wait_fill_s:.0f} s (normal): cancelled" if cid is not None
                  else f"refused: {self.last_error()}")
        left = await self.flatten()
        _eq, vpos, row = await self.venue_account()
        streamed = abs(self.ex.acct.pos) < self.m.step / 2          # what the account stream told the bot
        await self.ex.reconcile()
        agree = abs(self.ex.acct.pos) < self.m.step / 2             # ... and what a REST reconcile leaves it with
        self.note(f"{name}: flat", PASS if not left and agree else FAIL,
                  f"Lighter says position {vpos:+g}; " + ("it still lists the market with 0" if row else
                                                         "it no longer lists the market at all")
                  + f"; the bot's stream said flat {'yes' if streamed else 'NO'}; after a reconcile the bot holds "
                  + f"{self.ex.acct.pos:+g}" + ("" if agree else " (WRONG: it would keep sending exits)"))

    async def step_reduce_only_when_flat(self) -> None:
        bid, _ask = self.bbo()
        _cid, tx = self.sign_order(SELL, self.qty(), bid * (1 - TAKER_SLIP), order_type=C.ORDER_MARKET, tif=C.TIF_IOC,
                                   reduce_only=True, expiry=C.IOC_EXPIRY)
        _r, err = await self.raw([tx])
        await self.sleep(3.0)
        _eq, pos, _row = await self.venue_account()
        self.note("reduce-only order with no position", PASS if not pos else FAIL,
                  f"opened nothing ({err or 'Lighter took it and cancelled it'})" if not pos
                  else f"IT OPENED A POSITION of {pos:+g}")

    async def step_dead_mans_switch(self) -> None:
        """What clears the orders of a dead bot: the expiry every quote of the bot carries, and Lighter's scheduled
        cancel-all. Two orders far under the price, neither meant to trade: one through the bot's own path (so with
        its expiry), one signed here with the 28-day expiry, which only the scheduled cancel-all can remove."""
        name = "dead man's switch"
        _held, others = await self.venue_schedule()
        if others:      # the scheduled cancel-all is for the whole account, not this market
            self.note(name, SKIP, f"the account has {others} open order(s) in other markets and Lighter's scheduled "
                                  "cancel-all would cancel them too. Nothing was sent for this step")
            return
        ecid, px = await self.far_order(BUY, "test-expiry")
        if ecid is None or not await self.until_venue(lambda os_: bool(os_)):
            self.note(name, SKIP, f"no resting order to cancel: {self.last_error()}")
            return
        exp_ms = int(self.clock() * 1000) + QUOTE_EXPIRY_MS
        expiring = self.ex.orders[ecid].expires > 0
        if not expiring:
            self.note("order expiry", FAIL, "the bot's own order carries no expiry: nothing would clear it")
        cid, tx = self.sign_order(BUY, self.qty(), px + self.m.tick, order_type=C.ORDER_LIMIT, tif=C.TIF_POST_ONLY,
                                  reduce_only=False, expiry=C.ORDER_EXPIRY_DEFAULT)
        _r, derr = await self.raw([tx])
        if derr or not await self.until_venue(lambda os_: self.listed(os_, cid)):
            self.note(name, SKIP, f"the order for the scheduled cancel-all did not rest: {derr or 'Lighter does not list it'}")
            cid = 0
        ahead, err, at_ms, reply = 0, "", 0, None
        for ms in DMS_TRY_MS if cid else ():
            n = self.ex.nonces.take(2)
            at_ms = int(self.clock() * 1000) + ms
            reply, err = await self.raw([self.ex.signer.cancel_all(tif=C.CANCEL_ALL_ABORT, time_ms=0, nonce=n[0]),
                                         self.ex.signer.cancel_all(tif=C.CANCEL_ALL_SCHEDULED, time_ms=at_ms,
                                                                   nonce=n[1])])
            if not err:
                ahead = ms
                break
        how = f"scheduled {ahead / 1000:.0f} s ahead" + (" (Lighter takes less than the 5 minutes the bot uses)"
                                                        if ahead < C.CANCEL_ALL_MIN_MS else "")
        held = 0
        if not cid:
            pass
        elif not ahead:
            self.note(name, FAIL, f"Lighter refused the scheduled cancel-all: {err}")
        else:       # "OK" only means Lighter took the request: what counts is the time it then holds for the account
            await self.sleep(3.0)
            held, _n = await self.venue_schedule()
            if not held:
                hashes = list(reply.get("tx_hash") or []) if isinstance(reply, dict) else []
                why = await self.tx_note(str(hashes[-1])) if hashes else "its answer named no transaction"
                self.note(name, FAIL, f"{how}; Lighter answered OK but holds NO scheduled time for the account ({why}): "
                                      "it would never fire")
        if not held and not expiring:
            await self.ex.disarm()
            return
        last = max(at_ms if held else 0, exp_ms if expiring else 0) / 1000 + DMS_LATE_S
        self.say(f"       waiting up to {last - self.clock():.0f} s for Lighter to remove the two orders by itself: one by "
                 + (f"the scheduled cancel-all it holds ({held}, asked {at_ms})" if held else "(no scheduled time held)")
                 + ", one by its own expiry...")
        gone: dict[int, float] = {}              # client id -> when Lighter stopped listing it
        watch = ([cid] if held else []) + ([ecid] if expiring else [])
        while self.clock() < last and len(gone) < len(watch):
            await self.sleep(10.0)
            os_ = await self.venue_orders()
            for c in watch:
                if c not in gone and not self.listed(os_, c):
                    gone[c] = self.clock()
        together = cid in gone and ecid in gone and gone[cid] == gone[ecid]
        if held:
            late = gone.get(cid, self.clock()) - at_ms / 1000
            if cid not in gone:
                now_held, _n = await self.venue_schedule()
                self.note(name, FAIL, f"{how}; Lighter held the time ({held}, asked {at_ms}) but the order was STILL "
                                      f"THERE {late:.0f} s past it (the account's time now reads {now_held})")
            elif late > DMS_LATE_OK_S:
                self.note(name, FAIL, f"{how}; Lighter cancelled the order by itself but LATE: {late:.0f} s past its "
                                      "time. For that long after the bot dies its orders stay up")
            else:
                self.note(name, PASS, f"{how}; Lighter cancelled the order by itself {max(late, 0):.0f} s past its time")
        if expiring:
            late = gone.get(ecid, self.clock()) - exp_ms / 1000
            what = f"an order of the bot's own, which carries a {QUOTE_EXPIRY_MS / 1000:.0f} s expiry,"
            if ecid not in gone:
                self.note("order expiry", FAIL, f"{what} was STILL THERE {late:.0f} s past its expiry")
            elif together:
                self.note("order expiry", INFO, f"{what} went in the same 10 s as the scheduled cancel-all: this run "
                                                "cannot tell which removed it")
            elif late > DMS_LATE_OK_S:
                self.note("order expiry", FAIL, f"{what} was removed by Lighter but LATE: {late:.0f} s past its expiry")
            else:
                self.note("order expiry", PASS, f"{what} was removed by Lighter {max(late, 0):.0f} s past its expiry"
                                                + (", while the other order stayed" if held and cid not in gone else ""))
        if held and cid not in gone:    # what does Lighter do with the account's next request, past the scheduled time?
            ok = await self.send([Change("modify", Quote(BUY, px + 4 * self.m.tick, self.qty(), "test-dms"), cid=cid)])
            await self.sleep(3.0)
            still = self.listed(await self.venue_orders(), cid)
            now_held, _n = await self.venue_schedule()
            self.note("a request after the scheduled time", INFO,
                      "moving the order Lighter should have cancelled was "
                      + ("accepted" if ok else f"refused ({self.last_error()})")
                      + f"; the order is {'still listed' if still else 'now gone'}; the account's time reads {now_held}")
        await self.ex.disarm()

    # ---------------------------------------------------------------- the run
    async def cleanup(self) -> None:
        if not self.armed:       # nothing was sent: what is on the account is the owner's, and stays
            self.rep.clean = True
            self.rep.equity_end = self.rep.equity_start
            return
        with contextlib.suppress(Exception):
            await self.ex.cancel_all()
        with contextlib.suppress(Exception):
            await self.ex.disarm()
        left = 1.0
        with contextlib.suppress(Exception):
            left = await self.flatten()
        orders: list[dict[str, Any]] = [{}]
        with contextlib.suppress(Exception):
            await self.sleep(2.0)
            orders = await self.venue_orders()
            self.rep.equity_end = (await self.venue_account())[0]
        self.rep.clean = not left and not orders
        self.note("end", PASS if self.rep.clean else FAIL,
                  "flat, no orders, the scheduled cancel-all withdrawn" if self.rep.clean else
                  f"position {left:+g}, {len(orders)} order(s) STILL OPEN: close them in the Lighter app now")

    async def run(self) -> Report:
        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("leverage", lambda: self.step_leverage(self.lev_low, f"leverage {self.lev_low:g}x")),
            ("limit order", self.step_limit_modify_cancel),
            ("cancel-all", self.step_cancel_all),
            ("batch", self.step_batch),
            ("post-only", self.step_post_only_crossing),
            ("long", self._long),
            ("reduce-only", self.step_reduce_only_when_flat),
            ("short", self._short),
        ]
        if self.dms_only:
            steps = []
        if self.dms:
            steps.append(("dead man's switch", self.step_dead_mans_switch))
        try:
            await self.step_connect()
            for name, fn in steps:
                await self.guard()
                try:
                    await fn()
                except Abort:
                    raise
                except Exception as e:      # one step failing is a finding, not a reason to leave the rest untested
                    self.note(name, FAIL, f"{type(e).__name__}: {str(e)[:200]}")
            if not self.dms:
                self.note("dead man's switch", SKIP, "left out (--skip-dms)")
        except Abort as e:
            self.rep.aborted = str(e)
            self.say(f"  STOPPING: {e}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.rep.aborted = "interrupted"
            self.say("  STOPPING: interrupted")
        finally:
            await self.cleanup()
            with contextlib.suppress(Exception):
                await self.ex.stop()
        return self.rep

    async def _long(self) -> None:
        pos = await self.step_open(BUY, "buy")
        if pos:
            await self.step_stops(pos)
            await self.step_close(pos, "sell to close")

    async def _short(self) -> None:
        top = self.m.max_leverage
        if top > self.lev_low:
            await self.step_leverage(top, f"leverage {top:g}x")
        pos = await self.step_open(SELL, "sell short")
        if pos:
            await self.step_close(pos, "buy to close")


def pick_market(markets: dict[str, Any]) -> Any:
    """The cheapest market to test on: open, liquid (at least LIQUID_USD traded in the last day, so the spread a
    taker order pays is small), the smallest minimum order; the busiest of those."""
    live = [m for m in markets.values() if m.active and not m.reduce_only and m.last_price > 0]
    if not live:
        raise ValueError("no Lighter perp is open for new positions now")
    busy = [m for m in live if m.day_volume_usd >= LIQUID_USD] or live
    return min(busy, key=lambda m: (round(m.min_order_usd(m.last_price), 2), -m.day_volume_usd))


def plan_text(m: Any, lev_low: float, max_loss: float, dms: bool, dms_only: bool = False) -> str:
    one = min_qty(m.min_base, m.min_quote, m.step, m.last_price) * m.last_price
    if dms_only:
        return "\n".join((
            f"LIVE TEST on {m.symbol}, a dead bot's orders only: two real orders of about ${one:.2f} each, far under "
            "the price, where they do not trade.",
            "  One is placed as the bot places its quotes, with their 5.5-minute expiry. For the other it asks Lighter",
            "  to cancel every order on the account 5.5 minutes later. Then it waits (up to 11 minutes) to see Lighter",
            "  remove them by itself. It refuses if the account has orders in other markets.",
            "  Expected cost: nothing. It ends with no orders. Do not trade this market by hand while it runs."))
    return "\n".join((
        f"LIVE TEST on {m.symbol}: real orders, real money, the smallest size Lighter takes (about ${one:.2f} each).",
        f"  It sets the leverage ({min(lev_low, m.max_leverage):g}x, then {m.max_leverage:g}x), rests and moves and cancels "
        "orders far from the price,",
        "  buys one minimum order and sells it, sells one short and buys it back, places a stop and a take-profit",
        "  and removes them" + (", and lets Lighter's dead man's switch cancel one far order and expire another (up to 11 minutes)." if dms
                                else "."),
        f"  It stops and closes everything if the account falls ${max_loss:.2f} below where it started.",
        "  Expected cost with a zero-fee account: the spread on about four minimum orders (cents).",
        "  It ends flat with no orders. Do not trade this market by hand while it runs."))


def write_report(rep: Report, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    p = reports_dir / f"livetest-{time.strftime('%Y%m%d-%H%M%SZ', time.gmtime(rep.started))}.md"
    p.write_text(rep.text())
    return p
