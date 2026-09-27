import pytest

from lbot.trade.strategy import BUY, SELL, Params, Quoter, Rules, Setup, View, from_sid, parse


def rules(order=1000.0, cap=2000.0, tick=0.1, step=0.0001, min_usd=10.0):
    return Rules(tick, step, min_usd, order, cap)


def test_parse_names_and_ids():
    assert parse("mid 0").name == "Mid 0"
    assert parse("Mid +1 long").name == "Mid +1 Long"
    assert parse("touch 0.5").name == "Touch +0.5"
    assert parse("smart+1").name == "Smart +1"
    assert parse("grid 2 short").sid == "g+2s"
    assert from_sid("m+0.25n") == Setup("mid", 0.25, "neutral")
    with pytest.raises(ValueError):
        parse("grid -1")
    with pytest.raises(ValueError):
        parse("rgrid 1")


def test_mid_quotes_at_the_spread_and_rounds_away():
    q = Quoter(Setup("mid", 1.0).params(kappa=0))
    v = View(0, 99_990.0, 100_010.0)          # mid 100,000; 1 bp = 10
    p = q.plan(v, rules())
    b, a = next(x for x in p.quotes if x.side == BUY), next(x for x in p.quotes if x.side == SELL)
    assert b.px == pytest.approx(99_990.0)
    assert a.px == pytest.approx(100_010.0)
    assert b.qty == pytest.approx(0.01)


def test_mid_zero_on_an_even_book_never_puts_both_sides_at_one_price():
    q = Quoter(Setup("mid", 0).params())
    v = View(0, 765.00, 765.02)               # two ticks wide: the mid is on a tick
    p = q.plan(v, rules(tick=0.01, step=0.0001))
    b = next(x for x in p.quotes if x.side == BUY)
    a = next(x for x in p.quotes if x.side == SELL)
    assert a.px - b.px == pytest.approx(0.01)
    assert b.px < a.px


def test_post_only_guard_never_crosses():
    q = Quoter(Setup("mid", -5).params())
    p = q.plan(View(0, 100.0, 100.5), rules(tick=0.1, order=100, cap=200))
    b = next(x for x in p.quotes if x.side == BUY)
    a = next(x for x in p.quotes if x.side == SELL)
    assert b.px <= 100.4 + 1e-9 and a.px >= 100.1 - 1e-9 and b.px < a.px


def test_touch_joins_and_steps_back():
    q = Quoter(Setup("touch", 0).params())
    p = q.plan(View(0, 100.0, 101.0), rules(tick=0.01, order=100, cap=200))
    assert {x.px for x in p.quotes} == {100.0, 101.0}
    q = Quoter(Setup("touch", 10).params())            # 10 bp behind
    p = q.plan(View(0, 100.0, 101.0), rules(tick=0.01, order=100, cap=200))
    assert sorted(x.px for x in p.quotes) == [pytest.approx(99.89), pytest.approx(101.11)]   # 10 bp of the mid


def test_cap_stops_the_adding_side():
    q = Quoter(Setup("mid", 1).params())
    v = View(0, 99.99, 100.01, pos=20.0)       # $2,000 long = the cap
    p = q.plan(v, rules(order=1000, cap=2000, tick=0.01, step=0.01))
    assert all(x.side == SELL for x in p.quotes)


def test_bias_sizes_toward_the_target():
    q = Quoter(Setup("mid", 1, "long").params())
    p = q.plan(View(0, 99.99, 100.01), rules(order=1000, cap=2000, tick=0.01, step=0.01))
    b = next(x for x in p.quotes if x.side == BUY).qty
    a = next(x for x in p.quotes if x.side == SELL).qty
    assert b > a


def test_smart_leaves_out_the_side_the_book_leans_against():
    q = Quoter(Setup("smart", 0.5).params())
    v = View(0, 99.99, 100.01, bid_sz=1.0, ask_sz=50.0)     # the ask side is 50x the bid: a bid would be run over
    p = q.plan(v, rules(tick=0.01, step=0.01))
    assert [x.side for x in p.quotes] == [SELL]


def test_smart_leaves_out_after_a_drop():
    q = Quoter(Setup("smart", 0.5).params())
    r = rules(tick=0.01, step=0.01)
    for t in range(7):
        mid = 100.0 - 0.01 * t                      # 1 bp a second down
        p = q.plan(View(t * 1_000_000, mid - 0.01, mid + 0.01), r)
    assert [x.side for x in p.quotes] == [SELL]


def test_grid_quotes_around_the_last_fill_and_resets():
    q = Quoter(Setup("grid", 10).params())                   # 10 bp
    r = rules(tick=0.01, step=0.01)
    q.on_fill(BUY, 100.0, "b", 10.0, 0.01)
    p = q.plan(View(0, 99.99, 100.01, pos=10.0, entry=100.0), r)
    a = next(x for x in p.quotes if x.side == SELL)
    assert a.px == pytest.approx(100.10)
    p = q.plan(View(1, 99.30, 99.32, pos=10.0, entry=100.0), r)   # 0.69% below the last fill: reset
    assert p.quotes and all(x.reduce_only and x.side == SELL for x in p.quotes)


def test_hold_closes_with_a_taker_order():
    q = Quoter(Params(mode="mid", spread=1, hold_s=30))
    p = q.plan(View(31_000_000, 99.99, 100.01, pos=5.0, pos_since=1), rules(tick=0.01, step=0.01))
    assert p.taker == -5.0
