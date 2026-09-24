"""Paper venue: implements VenueAdapter with NO real writes (P2 task 11, P5 task 1).

The same class backs the simulator (venues/sim/adapter.py) with a simulated clock, so paper and sim share the
queue-aware fill model, latency model, fees, margin and funding. Books are the live (or replayed) L2Book objects;
the adapter attaches a level-change listener to tell cancels from trades.

Latency (defaults from the spec; calibrate in P5): Arcus ALO place = RTT; Arcus taker +50 ms speed bump; Arcus
cancel = RTT (priority lane); Lighter standard maker/cancel = RTT + 200 ms (docs) - run 0 ms as the optimistic
variant; Lighter taker = RTT + 300 ms. A cancel takes effect only after its latency.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from bot.core.book import L2Book
from bot.research.sim.fills import FillMode, QueueFillModel, SimOrder
from bot.venues.base import (
    TIF,
    Fill,
    Market,
    OrderRequest,
    OrderState,
    OrderStatus,
    Position,
    PublicTrade,
    RateBudget,
    Side,
    Venue,
)

Z = Decimal(0)


@dataclass(frozen=True, slots=True)
class LatencyModel:
    rtt_us: int = 40_000
    maker_extra_us: int = 0
    taker_extra_us: int = 50_000
    cancel_extra_us: int = 0

    @staticmethod
    def arcus(rtt_ms: float = 40) -> LatencyModel:
        return LatencyModel(int(rtt_ms * 1000), 0, 50_000, 0)

    @staticmethod
    def lighter(rtt_ms: float = 150, maker_ms: float = 200) -> LatencyModel:
        return LatencyModel(int(rtt_ms * 1000), int(maker_ms * 1000), 300_000, 200_000)

    def place(self, taker: bool) -> int:
        return self.rtt_us + (self.taker_extra_us if taker else self.maker_extra_us)

    def cancel(self) -> int:
        return self.rtt_us + self.cancel_extra_us


@dataclass
class _Acct:
    cash: Decimal = Z  # realised cash incl. fees and funding
    positions: dict[str, Decimal] = field(default_factory=dict)
    entry: dict[str, Decimal] = field(default_factory=dict)


class PaperVenue:
    def __init__(self, venue: Venue, markets: dict[str, Market], books: dict[str, L2Book], *,
                 now_us: Callable[[], int], latency: LatencyModel | None = None,
                 fill_mode: FillMode = FillMode.PESSIMISTIC, starting_equity: Decimal = Decimal(100),
                 touch_only: set[str] | None = None, marks: Callable[[str], Decimal | None] | None = None) -> None:
        self.venue = venue
        self._markets = markets
        self.books = books
        self.now_us = now_us
        self.latency = latency or (LatencyModel.arcus() if venue is Venue.ARCUS else LatencyModel.lighter())
        self.models: dict[str, QueueFillModel] = {}
        for b, m in markets.items():
            self.models[b] = QueueFillModel(mode=fill_mode, step=m.step_size, tick=m.tick_size,
                                            touch_only=b in (touch_only or set()))
        for b, book in books.items():
            self.attach_book(b, book)
        self.acct = _Acct(cash=starting_equity)
        self.starting_equity = starting_equity
        self._reqs: dict[str, OrderRequest] = {}
        self._updates: list[OrderState] = []
        self._fills: list[Fill] = []
        self._q_updates: asyncio.Queue[OrderState] | None = None
        self._q_fills: asyncio.Queue[Fill] | None = None
        self._marks = marks
        self._trade_seq = 0
        self.dms_deadline_us: int | None = None
        self.dms_fires = 0
        self.actions = 0
        self.ioc_pending: list[tuple[int, OrderRequest]] = []

    def attach_book(self, base: str, book: L2Book) -> None:
        self.books[base] = book
        model = self.models.get(base)
        if model is not None:
            book.listener = model.on_level_change

    # ---------------------------------------------------------------- VenueAdapter
    async def connect(self) -> None:
        self._q_updates = asyncio.Queue()
        self._q_fills = asyncio.Queue()

    async def close(self) -> None:
        return None

    async def markets(self) -> Sequence[Market]:
        return list(self._markets.values())

    def _emit(self, st: OrderState) -> None:
        self._updates.append(st)
        if self._q_updates is not None:
            self._q_updates.put_nowait(st)

    def _state(self, cid: str, status: OrderStatus, reason: str | None = None) -> OrderState:
        r = self._reqs.get(cid)
        o = None
        if r is not None and r.base in self.models:
            o = self.models[r.base].orders.get(cid)
        return OrderState(cid, cid, status, o.filled if o else Z, r.price if (r and o and o.filled) else None, reason,
                          self.now_us(), self.venue, r.base if r else "", r.side if r else None,
                          r.price if r else None, r.size if r else None, r.tif if r else None,
                          r.reduce_only if r else False, r.tag if r else "")

    async def place(self, orders: Sequence[OrderRequest]) -> Sequence[OrderState]:
        now = self.now_us()
        out = []
        for r in orders:
            self.actions += 1
            self._reqs[r.client_id] = r
            if r.tif in (TIF.IOC, TIF.FOK):
                self.ioc_pending.append((now + self.latency.place(True), r))
            else:
                o = SimOrder(r.client_id, r.side, r.price, r.size, post_only=r.tif is TIF.POST_ONLY,
                             reduce_only=r.reduce_only, tag=r.tag, submitted_us=now,
                             active_from_us=now + self.latency.place(False))
                self.models[r.base].submit(o)
            st = self._state(r.client_id, OrderStatus.PENDING_NEW)
            out.append(st)
            self._emit(st)
        self.process(now)
        return out

    async def modify(self, client_id: str, price: Decimal, size: Decimal) -> OrderState:
        r = self._reqs[client_id]
        self.actions += 1
        m = self.models[r.base]
        o = m.orders.get(client_id)
        if o is None or o.status not in ("open", "pending"):
            return self._state(client_id, OrderStatus.CANCELED, "ORDER_NOT_FOUND_FOR_MODIFY")
        book = self.books.get(r.base)
        # Modify = cancel + replace except same-price size decrease; post-only still must not cross.
        bb, ba = (book.best_bid(), book.best_ask()) if book else (None, None)
        if o.post_only and ((r.side is Side.BUY and ba and price >= ba[0]) or (r.side is Side.SELL and bb and price <= bb[0])):
            o.status = "rejected"
            st = self._state(client_id, OrderStatus.REJECTED, "POST_ONLY_WOULD_CROSS")
            self._emit(st)
            return st
        if book is not None:
            m.modify(client_id, price, size, book, self.now_us())
        self._reqs[client_id] = OrderRequest(r.venue, r.base, r.side, price, size, r.tif, r.reduce_only, r.client_id,
                                             r.tag, r.reason)
        st = self._state(client_id, OrderStatus.OPEN)
        self._emit(st)
        return st

    async def cancel(self, client_ids: Sequence[str]) -> None:
        now = self.now_us()
        for cid in client_ids:
            r = self._reqs.get(cid)
            if r is None:
                continue
            self.actions += 1
            self.models[r.base].request_cancel(cid, now + self.latency.cancel())
        self.process(now)

    async def cancel_all(self, base: str | None = None) -> None:
        now = self.now_us()
        for b, m in self.models.items():
            if base is not None and b != base:
                continue
            for o in m.open_orders():
                m.request_cancel(o.client_id, now + self.latency.cancel())
        self.actions += 1
        self.process(now)

    async def set_leverage(self, base: str, leverage: int, isolated: bool = False) -> None:
        return None

    async def arm_dead_mans_switch(self, deadline_us: int | None) -> None:
        self.dms_deadline_us = deadline_us

    async def positions(self) -> Sequence[Position]:
        out = []
        for b, sz in self.acct.positions.items():
            if sz == 0:
                continue
            mark = self._mark(b) or Z
            entry = self.acct.entry.get(b, mark)
            out.append(Position(self.venue, b, sz, entry, mark, (mark - entry) * sz, "cross", None))
        return out

    async def open_orders(self) -> Sequence[OrderState]:
        return [self._state(o.client_id, OrderStatus.OPEN) for m in self.models.values() for o in m.open_orders()
                if o.status == "open"]

    def equity(self) -> Decimal:
        eq = self.acct.cash
        for b, sz in self.acct.positions.items():
            mk = self._mark(b)
            if mk is not None:
                eq += sz * mk
        return eq

    async def balances(self) -> dict[str, Decimal]:
        eq = self.equity()
        im = Z
        for b, sz in self.acct.positions.items():
            mk = self._mark(b) or Z
            im += abs(sz) * mk * self._markets[b].imf
        return {"equity": eq, "free_collateral": eq - im}

    def _mark(self, base: str) -> Decimal | None:
        if self._marks is not None:
            v = self._marks(base)
            if v is not None:
                return v
        book = self.books.get(base)
        return book.mid() if book else None

    async def order_updates(self) -> AsyncIterator[OrderState]:
        assert self._q_updates is not None
        while True:
            yield await self._q_updates.get()

    async def fills(self) -> AsyncIterator[Fill]:
        assert self._q_fills is not None
        while True:
            yield await self._q_fills.get()

    def budget(self) -> RateBudget:
        return RateBudget(self.venue, None, None, None, None, None, 0)

    def health(self) -> dict[str, float]:
        return {"paper": 1.0}

    # ---------------------------------------------------------------- simulation hooks
    def drain(self) -> tuple[list[OrderState], list[Fill]]:
        u, f = self._updates, self._fills
        self._updates, self._fills = [], []
        return u, f

    def process(self, now_us: int) -> None:
        """Run arrivals, IOC executions, cancel expiries and the DMS at time `now_us`."""
        for b, m in self.models.items():
            book = self.books.get(b)
            for o in list(m.orders.values()):
                if not o.arrived and o.status == "pending" and now_us >= o.active_from_us:
                    if o.cancel_effective_us is not None and o.cancel_effective_us <= o.active_from_us:
                        o.status = "canceled"
                        self._emit(self._state(o.client_id, OrderStatus.CANCELED))
                        continue
                    if book is None or book.mid() is None:
                        continue
                    ok = m.arrive(o, book, now_us)
                    self._emit(self._state(o.client_id, OrderStatus.OPEN if ok else OrderStatus.REJECTED,
                                           None if ok else "POST_ONLY_WOULD_CROSS"))
            for o in m.expire_cancels(now_us):
                self._emit(self._state(o.client_id, OrderStatus.CANCELED))
        still = []
        for due, r in self.ioc_pending:
            if now_us >= due:
                self._execute_ioc(r, now_us)
            else:
                still.append((due, r))
        self.ioc_pending = still
        if self.dms_deadline_us is not None and now_us >= self.dms_deadline_us:
            self.dms_fires += 1
            self.dms_deadline_us = None
            for m in self.models.values():
                for o in m.open_orders():
                    o.status = "canceled"
                    self._emit(self._state(o.client_id, OrderStatus.CANCELED, "DEAD_MANS_SWITCH"))
        for m in self.models.values():
            m.gc()

    def _execute_ioc(self, r: OrderRequest, now_us: int) -> None:
        book = self.books.get(r.base)
        if book is None:
            self._emit(self._state(r.client_id, OrderStatus.CANCELED, "IOC_CANCELED"))
            return
        m = self._markets[r.base]
        rem = r.size
        if r.reduce_only:
            pos = self.acct.positions.get(r.base, Z)
            if (r.side is Side.BUY and pos >= 0) or (r.side is Side.SELL and pos <= 0):
                self._emit(self._state(r.client_id, OrderStatus.REJECTED, "REDUCE_ONLY_WOULD_INCREASE"))
                return
            rem = min(rem, abs(pos))
        for px, sz in book.levels(r.side is Side.SELL, 10_000):
            if (r.side is Side.BUY and px > r.price) or (r.side is Side.SELL and px < r.price) or rem <= 0:
                break
            q = min(rem, sz)
            q = (q / m.step_size).to_integral_value(rounding="ROUND_FLOOR") * m.step_size
            if q <= 0:
                break
            self._book_fill(r, px, q, now_us, is_maker=False)
            rem -= q
        filled = r.size - rem
        status = OrderStatus.FILLED if rem <= 0 else OrderStatus.CANCELED
        self._emit(OrderState(r.client_id, r.client_id, status, filled, None, None if rem <= 0 else "IOC_CANCELED",
                              now_us, self.venue, r.base, r.side, r.price, r.size, r.tif, r.reduce_only, r.tag))

    def on_public_trade(self, t: PublicTrade) -> list[Fill]:
        m = self.models.get(t.base)
        if m is None:
            return []
        out = []
        for sf in m.on_trade(t.price, t.size, t.taker_side, t.ts_us):
            r = self._reqs[sf.client_id]
            out.append(self._book_fill(r, sf.price, sf.size, t.ts_us, is_maker=True))
            o = m.orders.get(sf.client_id)
            if o is not None and o.status == "filled":
                self._emit(self._state(sf.client_id, OrderStatus.FILLED))
            else:
                self._emit(self._state(sf.client_id, OrderStatus.PARTIALLY_FILLED))
        return out

    def _book_fill(self, r: OrderRequest, price: Decimal, size: Decimal, ts_us: int, *, is_maker: bool) -> Fill:
        mk = self._markets[r.base]
        fee = price * size * (mk.maker_fee if is_maker else mk.taker_fee)
        signed = size * r.side.sign
        old = self.acct.positions.get(r.base, Z)
        new = old + signed
        if old == 0 or (old > 0) == (signed > 0):
            prev = self.acct.entry.get(r.base, price)
            self.acct.entry[r.base] = (abs(old) * prev + abs(signed) * price) / abs(new) if new else price
        elif new != 0 and (old > 0) != (new > 0):
            self.acct.entry[r.base] = price
        self.acct.positions[r.base] = new
        self.acct.cash -= signed * price + fee
        self._trade_seq += 1
        f = Fill(self.venue, r.base, r.client_id, r.side, price, size, fee, is_maker, False, ts_us,
                 f"paper-{self.venue.value}-{self._trade_seq}", r.client_id, r.tag)
        self._fills.append(f)
        if self._q_fills is not None:
            self._q_fills.put_nowait(f)
        return f

    def liquidate(self, base: str, close_size: Decimal, price: Decimal, fee: Decimal, ts_us: int, kind: str) -> Fill:
        """Book a forced close (simulated liquidation) with the venue's liquidation fee."""
        side = Side.BUY if close_size > 0 else Side.SELL
        req = OrderRequest(self.venue, base, side, price, abs(close_size), TIF.IOC, reduce_only=True,
                           client_id=f"liq-{self.venue.value}-{ts_us}", tag="liquidation", reason=kind)
        self._reqs[req.client_id] = req
        f = self._book_fill(req, price, abs(close_size), ts_us, is_maker=False)
        self.acct.cash -= fee - f.fee  # replace the taker fee with the liquidation fee
        f = Fill(f.venue, f.base, f.client_id, f.side, f.price, f.size, fee, False, True, f.ts_us, f.trade_id, None,
                 "liquidation")
        self._fills[-1] = f
        return f

    def apply_funding(self, base: str, rate_h: Decimal, pay_price: Decimal) -> Decimal:
        """payment = -position x pay_price x rate (positive rate: longs pay). Returns the payment (+ received)."""
        pos = self.acct.positions.get(base, Z)
        pay = -pos * pay_price * rate_h
        self.acct.cash += pay
        return pay

    def live_orders(self) -> dict[str, OrderRequest]:
        return {o.client_id: self._reqs[o.client_id] for m in self.models.values() for o in m.open_orders()}
