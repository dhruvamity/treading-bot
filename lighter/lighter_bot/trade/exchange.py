"""What the engine trades through: the paper exchange (here) and the live one (lighter_bot/trade/live.py).

Both show the engine the same things: the book (from the public feed), our orders, the position, the equity; and take
the same requests: a batch of changes (new, modify, cancel; one request), a taker order, cancel everything.

Paper fills its orders from the live trade stream with the backtest's rules (lighter_bot/scout/sim.py): an order lands
`maker_us` after it is sent (Lighter's speed bump plus the network) and is cancelled by Lighter if it would cross;
it waits behind the size shown at its price when it landed; a taker transaction fills it for what it printed at our
price beyond that queue plus everything it printed through our price. Taker orders walk the book `taker_us` later.
Fees are the market's (0 on a standard account). The same request budget applies, so paper cannot requote faster
than live could.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lighter_bot.config import Latency, Requests
from lighter_bot.log import Log
from lighter_bot.trade.feed import MarketFeed
from lighter_bot.trade.strategy import BUY, SELL, Quote
from lighter_bot.venue.market import Market
from lighter_bot.venue.nonce import ClientIds
from lighter_bot.venue.rest import Budget

log = Log("exchange")


@dataclass
class Order:
    cid: int
    side: int
    px: float
    qty: float                 # remaining
    tag: str
    reduce_only: bool = False
    state: str = "sent"        # sent | open | cancelling | done
    sent_at: float = 0.0
    lands_at: float = 0.0      # paper: when the speed bump lets it in
    ahead: float = -1.0        # paper: queue ahead at our price
    oid: int = 0               # Lighter's order index, once known
    why_done: str = ""
    expires: float = 0.0       # live: when Lighter drops the order by itself (0: paper, or not ours)


@dataclass
class Fill:
    t: float
    side: int
    px: float
    qty: float
    maker: bool
    tag: str = ""
    fee: float = 0.0
    tid: int = 0

    @property
    def usd(self) -> float:
        return self.px * self.qty


@dataclass
class Change:
    kind: str                  # new | modify | cancel
    quote: Quote | None = None
    cid: int = 0


@dataclass
class Account:
    equity: float | None = None
    free: float | None = None
    pos: float = 0.0
    entry: float | None = None
    updated: float = 0.0


class Exchange:
    """The part paper and live share: the feed, the orders, the fill callbacks."""

    mode = "base"

    def __init__(self, market: Market, feed: MarketFeed, requests: Requests, latency: Latency,
                 ids: ClientIds) -> None:
        self.market = market
        self.feed = feed
        self.budget = Budget(requests)
        self.latency = latency
        self.ids = ids
        self.orders: dict[int, Order] = {}
        self.acct = Account()
        self.fill_cbs: list[Callable[[Fill], None]] = []
        self.rejects: list[tuple[float, str]] = []

    def live_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.state != "done"]

    def _emit(self, f: Fill) -> None:
        for cb in self.fill_cbs:
            cb(f)

    def _book_fill(self, side: int, px: float, qty: float) -> None:
        """Position and average entry after a fill (paper; live reads them from Lighter)."""
        a = self.acct
        pos, entry = a.pos, a.entry
        new = pos + side * qty
        step = self.market.step
        if pos == 0 or (pos > 0) == (side > 0):
            entry = px if pos == 0 or entry is None else (entry * abs(pos) + px * qty) / (abs(pos) + qty)
        elif (new > 0) != (pos > 0) and abs(new) > step / 2:
            entry = px
        if abs(new) < step / 2:
            new, entry = 0.0, None
        a.pos, a.entry = new, entry

    # the interface
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, changes: list[Change], kind: str) -> bool: ...
    async def taker(self, qty_signed: float) -> None: ...
    async def cancel_all(self) -> None: ...
    async def set_leverage(self, leverage: float) -> None: ...
    def tick(self, now: float) -> None: ...


class PaperExchange(Exchange):
    mode = "paper"

    def __init__(self, market: Market, feed: MarketFeed, requests: Requests, latency: Latency, ids: ClientIds,
                 capital: float) -> None:
        super().__init__(market, feed, requests, latency, ids)
        self.cash = 0.0                    # realized trading cash flow (position value is added at the mark)
        self.capital = capital
        self.pending_takers: list[tuple[float, float]] = []   # (lands at, signed qty)
        feed.trade_listeners.append(self.on_trades)
        self.acct.equity = capital

    def mid(self) -> float | None:
        b = self.feed.bbo()
        return (b[0] + b[1]) / 2 if b else None

    def tick(self, now: float) -> None:
        """Land orders past their speed bump, finish cancels, run due taker orders, mark the equity."""
        bbo = self.feed.bbo()
        for o in list(self.orders.values()):
            if o.state == "sent" and now >= o.lands_at:
                if bbo is None or (o.side == BUY and o.px >= bbo[1]) or (o.side == SELL and o.px <= bbo[0]):
                    o.state, o.why_done = "done", "canceled-post-only"
                    self.rejects.append((now, "canceled-post-only"))
                    continue
                o.state = "open"
                o.ahead = self._shown(o.side, o.px)
            elif o.state == "cancelling" and now >= o.lands_at:
                o.state = "done"
            elif o.state == "open":
                o.ahead = min(o.ahead, self._shown(o.side, o.px))
        for due, q in list(self.pending_takers):
            if now >= due:
                self.pending_takers.remove((due, q))
                self._run_taker(q, now)
        m = self.mid()
        if m is not None:
            self.acct.equity = self.capital + self.cash + self.acct.pos * m
            self.acct.updated = now
        self.orders = {k: o for k, o in self.orders.items() if o.state != "done" or now - o.sent_at < 60}

    def _shown(self, side: int, px: float) -> float:
        book = self.feed.book
        s = (book.bids if side == BUY else book.asks)
        return s.size.get(round(px, self.market.price_decimals), 0.0) if s.size else 0.0

    def on_trades(self, trades: list[dict[str, Any]]) -> None:
        now = time.time()
        self.tick(now)
        groups: dict[str, list[dict[str, Any]]] = {}
        for t in trades:
            groups.setdefault(str(t.get("tx_hash", "")), []).append(t)
        tick = self.market.tick
        for g in groups.values():
            taker_buy = bool(g[0].get("is_maker_ask"))
            ms = SELL if taker_buy else BUY
            prints = [(float(t["price"]), float(t["size"])) for t in g]
            cands = [o for o in self.orders.values() if o.state == "open" and o.side == ms and o.qty > 0]
            cands.sort(key=lambda o: -o.px if ms == BUY else o.px)
            taken = 0.0
            for o in cands:
                at_px = sum(s for p, s in prints if abs(p - o.px) <= tick / 2)
                use = min(at_px, max(0.0, o.ahead))
                o.ahead -= use
                thru = sum(s for p, s in prints if abs(p - o.px) > tick / 2 and (p < o.px if ms == BUY else p > o.px))
                fq = min(o.qty, at_px - use + thru - taken)
                if o.reduce_only:
                    fq = min(fq, abs(self.acct.pos))
                fq = math.floor(fq / self.market.step + 1e-9) * self.market.step
                if fq <= 0:
                    continue
                o.qty -= fq
                taken += fq
                if o.qty <= self.market.step / 2:
                    o.state, o.why_done = "done", "filled"
                fee = fq * o.px * self.market.maker_fee
                self.cash -= o.side * fq * o.px + fee
                self._book_fill(o.side, o.px, fq)
                self._emit(Fill(now, o.side, o.px, fq, True, o.tag, fee))

    async def send(self, changes: list[Change], kind: str) -> bool:
        if not changes:
            return True
        if not self.budget.take(kind):
            return False
        now = time.time()
        lands = now + self.latency.maker_us / 1e6
        pd = self.market.price_decimals
        for c in changes:
            if c.quote is not None:        # the price Lighter would get: whole ticks (live sends integers)
                c = Change(c.kind, Quote(c.quote.side, round(c.quote.px, pd), c.quote.qty, c.quote.tag,
                                         c.quote.reduce_only), c.cid)
            if c.kind == "new" and c.quote is not None:
                q = c.quote
                cid = self.ids.take()[0]
                self.orders[cid] = Order(cid, q.side, q.px, q.qty, q.tag, q.reduce_only, "sent", now, lands)
            elif c.kind == "modify" and c.quote is not None and c.cid in self.orders:
                o = self.orders[c.cid]
                o.state, o.lands_at = "cancelling", lands
                cid = self.ids.take()[0]
                q = c.quote
                self.orders[cid] = Order(cid, q.side, q.px, q.qty, q.tag, q.reduce_only, "sent", now, lands)
            elif c.kind == "cancel" and c.cid in self.orders:
                o = self.orders[c.cid]
                if o.state != "done":
                    o.state, o.lands_at = "cancelling", lands
        return True

    async def taker(self, qty_signed: float) -> None:
        self.budget.take("reserve")
        self.pending_takers.append((time.time() + self.latency.taker_us / 1e6, qty_signed))

    def _run_taker(self, qty_signed: float, now: float) -> None:
        pos = self.acct.pos
        side = BUY if qty_signed > 0 else SELL
        qty = abs(qty_signed)
        if (side == SELL and pos > 0) or (side == BUY and pos < 0):
            qty = min(qty, abs(pos))     # reduce-only
        qty = math.floor(qty / self.market.step + 1e-9) * self.market.step
        levels = (self.feed.book.asks if side == BUY else self.feed.book.bids).top(50)
        if qty <= 0 or not levels:
            return
        left, cost = qty, 0.0
        for p, s in levels:
            take = min(left, s)
            cost += take * p
            left -= take
            if left <= 0:
                break
        if left > 0:
            cost += left * levels[-1][0] * (1 + side * 0.0005)
        px = cost / qty
        fee = qty * px * self.market.taker_fee
        self.cash -= side * qty * px + fee
        self._book_fill(side, px, qty)
        self._emit(Fill(now, side, px, qty, False, "taker", fee))

    async def cancel_all(self) -> None:
        self.budget.take("reserve")
        lands = time.time() + self.latency.maker_us / 1e6
        for o in self.orders.values():
            if o.state in ("sent", "open"):
                o.state, o.lands_at = "cancelling", lands

    async def set_leverage(self, leverage: float) -> None:
        return None

    async def start(self) -> None:
        await self.feed.start()

    async def stop(self) -> None:
        for o in self.orders.values():      # a stopped paper run leaves nothing on its book
            if o.state != "done":
                o.state, o.why_done = "done", "stopped"
        await self.feed.stop()
