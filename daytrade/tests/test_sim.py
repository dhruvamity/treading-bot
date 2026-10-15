from daytrade.core import Exit, Order, Venue
from daytrade.sim import Book

V = Venue("test", 50, taker_bp=1.0, stop_bp=2.0, maker_bp=0.0, funding_bp_h=0.0)
T0 = 1_760_000_000


def book() -> Book:
    return Book(V, tick=0.01, through=1)


def order(side=1, kind="market", price=100.0, stop=99.0, target=102.0, **kw) -> Order:
    return Order(side, kind, price, stop, target, expire=T0 + 3600, flat_at=T0 + 7200, **kw)


def test_market_order_fills_next_open_and_hits_target():
    b = book()
    b.after(T0, 100.0, [order()])
    b.bar(1, T0 + 60, 100.10, 100.50, 99.90, 100.40)           # fills at the open 100.10
    assert b.pos is not None and b.pos.entry == 100.10
    b.bar(1, T0 + 120, 100.40, 102.05, 100.30, 101.90)          # 102.05 >= 102 + 1 tick: target
    tr = b.done[0]
    assert tr.reason == "target"
    assert abs(tr.r_gross - (102.0 - 100.10) / 1.0) < 1e-9      # 1R is the decided distance, 100 -> 99
    assert tr.r_cost > 0


def test_stop_wins_when_one_bar_reaches_both():
    b = book()
    b.after(T0, 100.0, [order()])
    b.bar(1, T0 + 60, 100.0, 100.2, 99.9, 100.1)
    b.bar(1, T0 + 120, 100.1, 102.5, 98.9, 101.0)
    assert b.done[0].reason == "stop" and b.done[0].exit == 99.0


def test_gap_through_stop_fills_at_open():
    b = book()
    b.after(T0, 100.0, [order()])
    b.bar(1, T0 + 60, 100.0, 100.2, 99.9, 100.1)
    b.bar(1, T0 + 120, 98.5, 98.8, 98.2, 98.6)
    assert b.done[0].exit == 98.5 and b.done[0].r_gross < -1


def test_limit_needs_a_trade_through_and_checks_only_the_stop_on_its_bar():
    b = book()
    b.after(T0, 101.0, [order(kind="limit", price=100.0)])
    b.bar(1, T0 + 60, 100.5, 100.6, 100.0, 100.3)              # a touch is not a fill
    assert b.pos is None and b.pend is not None
    b.bar(1, T0 + 120, 100.3, 102.5, 99.95, 100.2)              # through by a tick: filled; target not on this bar
    assert b.pos is not None and b.pos.entry == 100.0 and not b.done
    b.bar(1, T0 + 180, 100.2, 102.1, 100.1, 102.0)
    assert b.done[0].reason == "target" and abs(b.done[0].r_gross - 2.0) < 1e-9


def test_limit_is_dropped_once_the_price_runs_to_the_target_without_it():
    b = book()
    b.after(T0, 101.0, [order(kind="limit", price=100.0, cancel_beyond=102.0)])
    b.bar(1, T0 + 60, 101.0, 102.2, 100.5, 102.0)
    assert b.pend is None and b.pos is None


def test_partial_then_breakeven_stop():
    b = book()
    b.after(T0, 100.0, [order(target=None, partial=101.0)])
    b.bar(1, T0 + 60, 100.0, 100.2, 99.9, 100.1)
    b.bar(1, T0 + 120, 100.1, 101.2, 100.0, 101.1)              # half off at 101, stop to the entry (100.0)
    assert b.pos.partial_done and b.pos.stop == 100.0
    b.bar(1, T0 + 180, 101.0, 101.1, 99.9, 100.0)               # the rest out at the entry
    tr = b.done[0]
    assert tr.reason == "stop" and abs(tr.r_gross - 0.5) < 1e-9


def test_exit_signal_closes_at_the_next_open_and_time_exit_at_close():
    b = book()
    b.after(T0, 100.0, [order(target=None)])
    b.bar(1, T0 + 60, 100.0, 100.2, 99.9, 100.1)
    b.after(T0 + 60, 100.1, [Exit("trail")])
    b.bar(1, T0 + 120, 100.3, 100.4, 100.2, 100.3)
    assert b.done[0].reason == "trail" and b.done[0].exit == 100.3
    b2 = book()
    b2.after(T0, 100.0, [order(target=None)])
    b2.bar(1, T0 + 60, 100.0, 100.2, 99.9, 100.1)
    b2.after(T0 + 7140, 100.7, [])                              # the bar before flat_at closes
    assert b2.done[0].reason == "time" and b2.done[0].exit == 100.7


def test_short_side_mirrors():
    b = book()
    b.after(T0, 100.0, [order(side=-1, stop=101.0, target=98.0)])
    b.bar(1, T0 + 60, 100.0, 100.1, 99.5, 99.6)
    b.bar(1, T0 + 120, 99.6, 99.7, 97.95, 98.1)
    assert b.done[0].reason == "target" and abs(b.done[0].r_gross - 2.0) < 1e-9
