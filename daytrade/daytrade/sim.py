"""The backtest: a strategy's orders filled on one-minute bars, the conservative way round.

- A market order fills at the next bar's open and pays the taker cost.
- A limit order fills only when a bar trades THROUGH its price by `through` ticks (a touch is not a fill); it pays the
  maker cost. A bar that opens past it fills at the open.
- A stop fills at its price, or at the open when a bar gaps past it, and pays the stop cost (taker + slippage).
- When one bar reaches both the stop and the target, the stop counts. On the bar a limit order fills, only the stop
  is checked (the bar may have reached the target before the fill).
- A position still open at `flat_at` (or at the day's last bar) is closed at that bar's close as a taker.
- Funding is charged for every hour held.

Results are in R: one R is the distance from the decided entry to the first stop, which is what a position is sized
on (risk 1% of the account = 1R). `r` is after costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core import Cancel, Day, Exit, MoveStop, Order, Position, Strategy, Venue


@dataclass
class Trade:
    day: int
    side: int
    tag: str
    t_signal: int
    t_entry: int
    entry: float
    stop0: float
    target: float | None
    risk: float                     # price distance of 1R
    t_exit: int = 0
    exit: float = 0.0               # size-weighted exit price
    reason: str = ""
    r_gross: float = 0.0
    r_cost: float = 0.0
    legs: list[tuple[float, float, float]] = field(default_factory=list)   # (share, price, cost bp)

    @property
    def r(self) -> float:
        return self.r_gross - self.r_cost

    @property
    def dist(self) -> float:
        """1R as a share of the price: what turns a 1% risk into a notional (notional = risk / dist)."""
        return self.risk / self.entry


class Book:
    """The bot's view of one day: one pending order or one position. Used by the backtest and by paper trading."""

    def __init__(self, venue: Venue, tick: float, through: float = 1.0):
        self.venue, self.tick, self.through = venue, tick, through
        self.pend: Order | None = None
        self.pend_t = 0
        self.pos: Position | None = None
        self.trade: Trade | None = None
        self.done: list[Trade] = []
        self.exit_next: str | None = None
        self.n = 0

    # -- fills ---------------------------------------------------------------------------------------------------
    def _open(self, o: Order, px: float, t: int, cost_bp: float, day: int) -> None:
        self.pos = Position(o.side, px, o.stop, o.stop, o.target, t, o)
        self.trade = Trade(day, o.side, o.tag, self.pend_t, t, px, o.stop, o.target, abs(o.price - o.stop))
        self.trade.legs.append((0.0, px, cost_bp))
        self.n += 1

    def _close(self, share: float, px: float, t: int, cost_bp: float, reason: str) -> None:
        p, tr = self.pos, self.trade
        tr.legs.append((share, px, cost_bp))
        p.left -= share
        if p.left <= 1e-9:
            self._finish(t, reason)

    def _finish(self, t: int, reason: str) -> None:
        p, tr = self.pos, self.trade
        legs = tr.legs[1:]
        tr.t_exit, tr.reason = t, reason
        tr.exit = sum(s * x for s, x, _ in legs) / max(sum(s for s, _, _ in legs), 1e-12)
        tr.r_gross = sum(s * (x - tr.entry) * p.side for s, x, _ in legs) / tr.risk
        hours = max(0.0, (t - tr.t_entry) / 3600)
        cost = tr.entry * tr.legs[0][2] / 1e4 + sum(s * x * c / 1e4 for s, x, c in legs)
        cost += tr.entry * self.venue.funding_bp_h * hours / 1e4
        tr.r_cost = cost / tr.risk
        self.done.append(tr)
        self.pos, self.trade = None, None

    # -- one bar -------------------------------------------------------------------------------------------------
    def bar(self, day: int, t: int, o: float, h: float, lo: float, c: float) -> None:
        """Fill and manage against one closed one-minute bar (before the strategy sees it)."""
        v = self.venue
        if self.pos is not None and self.exit_next:
            self._close(self.pos.left, o, t, v.taker_bp, self.exit_next)
            self.exit_next = None
        filled_now = False
        if self.pend is not None and self.pos is None:
            p = self.pend
            if t >= p.expire:
                self.pend = None
            elif p.kind == "market":
                self._open(p, o, t, v.taker_bp, day)
                self.pend = None
            else:
                th = self.through * self.tick
                px = None
                if p.side > 0:
                    px = o if o <= p.price - th else (p.price if lo <= p.price - th else None)
                else:
                    px = o if o >= p.price + th else (p.price if h >= p.price + th else None)
                if px is not None:
                    self._open(p, px, t, v.maker_bp, day)
                    self.pend = None
                    filled_now = True
                elif p.cancel_beyond is not None and (h >= p.cancel_beyond if p.side > 0 else lo <= p.cancel_beyond):
                    self.pend = None
        pos = self.pos
        if pos is None:
            return
        th = self.through * self.tick
        if pos.side > 0:
            if not filled_now and o <= pos.stop:
                return self._close(pos.left, o, t, v.stop_bp, "stop")
            if lo <= pos.stop:
                return self._close(pos.left, pos.stop, t, v.stop_bp, "stop")
            if filled_now:
                return
            if pos.order.partial is not None and not pos.partial_done and h >= pos.order.partial + th:
                pos.partial_done = True
                self._close(pos.order.partial_frac, pos.order.partial, t, v.maker_bp, "partial")
                pos.stop = max(pos.stop, pos.entry)
            if pos.target is not None and h >= pos.target + th:
                return self._close(pos.left, pos.target, t, v.maker_bp, "target")
        else:
            if not filled_now and o >= pos.stop:
                return self._close(pos.left, o, t, v.stop_bp, "stop")
            if h >= pos.stop:
                return self._close(pos.left, pos.stop, t, v.stop_bp, "stop")
            if filled_now:
                return
            if pos.order.partial is not None and not pos.partial_done and lo <= pos.order.partial - th:
                pos.partial_done = True
                self._close(pos.order.partial_frac, pos.order.partial, t, v.maker_bp, "partial")
                pos.stop = min(pos.stop, pos.entry)
            if pos.target is not None and lo <= pos.target - th:
                return self._close(pos.left, pos.target, t, v.maker_bp, "target")

    def after(self, t: int, c: float, actions) -> None:
        """The strategy's answer to the bar that closed at `t`."""
        if self.pos is not None and t + 60 >= self.pos.order.flat_at:
            self._close(self.pos.left, c, t + 60, self.venue.taker_bp, "time")
        for a in actions:
            if isinstance(a, Order):
                if self.pos is None:
                    self.pend, self.pend_t = a, t + 60
            elif isinstance(a, Cancel):
                self.pend = None
            elif isinstance(a, Exit):
                if self.pos is not None:
                    self.exit_next = a.reason
            elif isinstance(a, MoveStop):
                if self.pos is not None:
                    self.pos.stop = a.price

    def end_day(self, t: int, c: float) -> None:
        if self.pos is not None:
            self._close(self.pos.left, c, t + 60, self.venue.taker_bp, "day end")
        self.pend = None
        self.exit_next = None


def run_day(s: Strategy, day: Day, venue: Venue, tick: float, through: float = 1.0) -> list[Trade]:
    book = Book(venue, tick, through)
    s.start_day(day)
    t, o, h, lo, c = day.t.tolist(), day.o.tolist(), day.h.tolist(), day.l.tolist(), day.c.tolist()
    for k in range(len(t)):
        book.bar(day.key, t[k], o[k], h[k], lo[k], c[k])
        book.after(t[k], c[k], s.on_bar(k, book.pos, book.n))
    if len(t):
        book.end_day(t[-1], c[-1])
    return book.done


def run(s: Strategy, days: list[Day], venue: Venue, tick: float, through: float = 1.0) -> list[Trade]:
    out: list[Trade] = []
    for d in days:
        out += run_day(s, d, venue, tick, through)
    return out
