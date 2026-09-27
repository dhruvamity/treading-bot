"""Smart (Mid that leaves a side out while its fill would likely lose): the rule, the live strategy, the backtest's
copy of it, the setup names, and the playbook adding it to days it already has."""

from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bot.core.book import L2Book
from bot.core.marketdata import MarketView
from bot.scout import playbook as pbk
from bot.scout.pilot import session_for
from bot.scout.scan import config_for
from bot.scout.sim import BUY, Book, Config, MarketInfo, Risk, Sim, SmartPolicy, Window
from bot.scout.tape import DayTape, day_start_us
from bot.strategies import make_strategy, smart
from bot.strategies import setup as su
from bot.strategies.base import StrategyContext
from bot.strategies.smart import SmartStrategy
from bot.venues.base import Side, Venue
from tests.helpers import fixture_markets, mm_session

S = 1_000_000
T0 = day_start_us("2026-09-20")
AMD = fixture_markets()[Venue.ARCUS]["AMD"]


def sess(**kw: Any) -> Any:
    return mm_session(market="AMD", mode="smart", execution_style="passive", passive_k_sigma=0.0, spacing_bps=0,
                      levels_per_side=1, skew_kappa=0.0, order_size_usd=25, inventory_cap_usd=50, **kw)


def ctx_at(now: int, bid: str, ask: str, bid_sz: str = "1", ask_sz: str = "1", inv: D = D(0),
           own: tuple[float, float] = (0.0, 0.0)) -> StrategyContext:
    view = MarketView(Venue.ARCUS, "AMD")
    view.book = L2Book()
    view.book.load([(D(bid), D(bid_sz))], [(D(ask), D(ask_sz))], 1)
    return StrategyContext(now_us=now, venue=Venue.ARCUS, market=AMD, view=view, params=sess(), inventory=inv,
                           own_touch=own)


def sides(out: Any) -> set[Side]:
    return {o.side for o in out.desired[(Venue.ARCUS, "AMD")]}


# ------------------------------------------------------------------------------------------------ the rule
def test_a_side_is_left_out_when_the_book_or_the_last_seconds_are_against_it() -> None:
    assert smart.imbalance(1, 9) == pytest.approx(-0.8) and smart.imbalance(0, 0) == 0.0
    assert smart.left_out(0.0, 0.0, 0.0) == (False, False)               # a balanced, quiet book: both sides
    assert smart.left_out(-0.8, 0.0, 0.0) == (True, False)               # the ask side is 9x ours: no bid
    assert smart.left_out(0.8, 0.0, 0.0) == (False, True)
    assert smart.left_out(0.0, -0.6, 0.0) == (True, False)               # the mid just fell 0.6 bp: no bid
    assert smart.left_out(0.0, 0.6, 0.0) == (False, True)
    assert smart.left_out(-0.8, -0.6, -0.3) == (False, False)            # short: a buy reduces it, so it stays
    assert smart.left_out(0.8, 0.6, 0.3) == (False, False)               # long: a sell reduces it
    assert smart.left_out(-0.5, -0.4, 0.0) == (False, False)             # within both limits


def test_the_live_strategy_leaves_out_the_bid_the_ask_side_outweighs() -> None:
    s = SmartStrategy(sess())
    assert sides(s.on_tick(ctx_at(T0, "620.00", "620.10"))) == {Side.BUY, Side.SELL}
    out = s.on_tick(ctx_at(T0 + S, "620.00", "620.10", bid_sz="1", ask_sz="9"))
    assert sides(out) == {Side.SELL} and out.reason.startswith("smart: bid out")
    assert out.metrics["imbalance"] == pytest.approx(-0.8)
    # our own 8 at the bid are not the market's: 1 + 8 shown, still 9 to 1 against the bid
    assert sides(s.on_tick(ctx_at(T0 + 2 * S, "620.00", "620.10", bid_sz="9", ask_sz="9", own=(8.0, 0.0)))) == \
        {Side.SELL}
    # short: the bid reduces the position, so it stays however the book leans
    assert sides(s.on_tick(ctx_at(T0 + 3 * S, "620.00", "620.10", bid_sz="1", ask_sz="9", inv=D("-0.05")))) == \
        {Side.BUY, Side.SELL}


def test_the_live_strategy_leaves_out_the_bid_right_after_a_drop_and_forgets_old_prices() -> None:
    s = SmartStrategy(sess())
    for k in range(6):                                                    # 620.05 for 5 s
        s.on_tick(ctx_at(T0 + k * S, "620.00", "620.10"))
    out = s.on_tick(ctx_at(T0 + 6 * S, "619.95", "620.05"))               # -0.8 bp in 5 s
    assert sides(out) == {Side.SELL} and out.metrics["move_bps"] < -0.5
    s2 = SmartStrategy(sess())
    s2.on_tick(ctx_at(T0, "620.00", "620.10"))
    out = s2.on_tick(ctx_at(T0 + 60 * S, "619.95", "620.05"))             # a minute's gap: no 5-second move to read
    assert sides(out) == {Side.BUY, Side.SELL} and out.metrics["move_bps"] == 0.0


def test_the_exit_book_is_never_thinned() -> None:
    s = SmartStrategy(sess())
    c = ctx_at(T0, "620.00", "620.10", bid_sz="1", ask_sz="9", inv=D("0.05"))
    c.quoting_allowed, c.quoting_block_reason = False, "paused"
    out = s.on_tick(c)
    orders = out.desired[(Venue.ARCUS, "AMD")]
    assert len(orders) == 1 and orders[0].reduce_only and orders[0].side is Side.SELL


@pytest.mark.parametrize(("bid_sz", "ask_sz", "move", "inv"),
                         [(1, 1, 0.0, 0.0), (1, 9, 0.0, 0.0), (9, 1, 0.0, 0.05), (1, 1, -0.8, 0.0),
                          (1, 1, 0.8, -0.05), (1, 9, 0.8, 0.0)])
def test_smart_policy_quotes_what_the_live_strategy_quotes(bid_sz: float, ask_sz: float, move: float,
                                                          inv: float) -> None:
    live = make_strategy(sess())
    assert isinstance(live, SmartStrategy)
    live.mids.append((T0 - 5 * S, 620.05 / (1 + move / 1e4)))             # the mid 5 s ago
    out = live.on_tick(ctx_at(T0, "620.00", "620.10", str(bid_sz), str(ask_sz), D(str(inv))))
    got = sorted((o.side, o.price_ticks, o.size_quantums) for o in out.desired[(Venue.ARCUS, "AMD")])
    mi = MarketInfo(float(AMD.tick_size), float(AMD.step_size), float(AMD.min_notional), float(AMD.min_size))
    pol = SmartPolicy(Config("x", "smart", spacing_bps=0), Risk(order_usd=25, cap_usd=50), mi)
    q, _ = pol.quotes(Book(T0, 620.0, 620.1, 620.05, inv, None, 0.0, 0.0, bid_sz, ask_sz, move))
    ours = sorted((Side.BUY if s == BUY else Side.SELL, round(p / mi.tick), round(qq / mi.step)) for s, p, qq, _t in q)
    assert got == ours


def test_the_backtest_skips_the_fills_that_follow_a_heavy_ask_side() -> None:
    """A book whose ask side outweighs the bid 9 to 1 whenever sellers are about to hit the bid: Mid buys every
    time, Smart never adds a long there, so it trades less and loses less."""
    n = 600
    ts = T0 + np.arange(n, dtype=np.int64) * S
    mid = 100.0 - 0.001 * np.arange(n)                                     # a slow fall
    heavy_ask = (np.arange(n) % 10) >= 7                                   # 3 s of a heavy ask before each sale
    bbo = {"ts": ts, "bid": np.round(mid - 0.005, 2), "ask": np.round(mid + 0.005, 2),
           "bid_sz": np.where(heavy_ask, 1.0, 5.0), "ask_sz": np.where(heavy_ask, 9.0, 5.0)}
    k = np.arange(9, n, 10)                                                # a taker sells through the bid
    tr = {"ts": ts[k] + 500_000, "px": np.round(mid[k] - 0.02, 2), "sz": np.full(len(k), 2.0),
          "buy": np.zeros(len(k), bool), "seq": np.arange(1, len(k) + 1, dtype=np.int64), "tid": np.arange(len(k))}
    tape = DayTape("TEST-USD", "2026-09-20", bbo, tr)
    mi = MarketInfo(tick=0.01, step=0.01, min_notional=1.0)
    risk = Risk(order_usd=25, cap_usd=1e6, pos_stop_usd=1e9, daily_stop_usd=1e9, kill_usd=1e9)
    w = Window(tape, T0 + 30 * S, T0 + n * S, warmup_s=0)
    mid_r = Sim(Config("Mid 0", "mid", spacing_bps=0, safety=False), risk, mi).run(w)
    smart_r = Sim(Config("Smart 0", "smart", spacing_bps=0, safety=False), risk, mi).run(w)
    assert mid_r.maker_fills > 20 and smart_r.maker_fills == 0
    assert smart_r.pnl > mid_r.pnl


# ------------------------------------------------------------------------------------------------ names and wiring
def test_smart_is_a_setup_the_owner_can_type_and_the_scout_backtests() -> None:
    s = su.parse("smart +3")
    assert (s.mode, s.spread, s.name, s.label, s.sid) == ("smart", 3.0, "Smart +3", "Smart +3 · Neutral", "s+3n")
    assert su.from_sid("s0n") == su.Setup("smart", 0.0) and su.parse("smart0").name == "Smart 0"
    assert [x.name for x in su.menu() if x.mode == "smart"] == ["Smart 0", "Smart +1", "Smart +2", "Smart +3"]
    c = config_for("Smart 0")
    assert (c.mode, c.style, c.spacing_bps, c.safety) == ("smart", "passive", 0.0, False)
    assert config_for("Smart +2").kappa == config_for("Mid +2").kappa == 1.0
    sd = session_for("SPY-USD", config_for("Smart +3"), Risk(order_usd=25, cap_usd=50, capital_usd=100), live=False)
    assert sd["mode"] == "smart" and sd["execution_style"] == "passive" and sd["passive_k_sigma"] == 0.0
    assert isinstance(make_strategy(mm_session(**{**{k: v for k, v in sd.items() if k in (
        "market", "mode", "execution_style", "spacing_bps")}})), SmartStrategy)


def test_the_playbook_adds_smart_to_days_it_already_has(tmp_path: Path, monkeypatch: Any) -> None:
    """A day cached before Smart existed (a plain list of rows) keeps its rows and gets only the Smart setups; a
    rebuilt-book day never gets them (no real sizes to read)."""
    assert "Smart 0" not in pbk.setups_for(True) and "Smart 0" in pbk.setups_for(False)
    root = tmp_path / "scout"
    root.mkdir()
    (root / "markets.json").write_text(json.dumps({"markets": [{
        "marketDisplayName": "SPY-USD", "status": "ONLINE", "tickSize": "0.01", "stepSize": "0.001",
        "minOrderNotional": "5", "minOrderSize": "0.001", "initialMarginFraction": "0.02"}]}))
    pb = pbk.Playbook(root, markets=("SPY-USD",))
    monkeypatch.setattr(pbk.TapeStore, "days", lambda self, m, kind="bbo": ["2026-09-24"])
    monkeypatch.setattr(pbk.Playbook, "days", lambda self, m, now: [("2026-09-23", True), ("2026-09-24", False)])
    monkeypatch.setattr(pbk.Playbook, "risk", lambda self, m, days: {"leverage": 50.0, "order_usd": 25.0})
    monkeypatch.setattr(pbk.Playbook, "_touch_size", lambda self, m: 1.0)
    monkeypatch.setattr(pbk.Playbook, "blocked", lambda self, m, d: [])
    monkeypatch.setattr(pbk.rg, "usual", lambda store, m, t: type("U", (), {"rv": {}})())
    rk = pbk.risk_key({"leverage": 50.0, "order_usd": 25.0})
    old = [[h, name, 1000.0, -0.1, "asia", "normal", 1.0] for h in range(2) for name in pbk.FIRST_SETUPS]
    for d in ("2026-09-23", "2026-09-24"):
        p = pb.cache_path("SPY-USD", d, rk)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(old))
    asked: list[tuple[str, tuple[str, ...]]] = []

    def fake_gather(ex: Any, jobs: list[tuple[Any, ...]], stop: Any, fn: Any, deadline: Any) -> Any:
        for i, j in enumerate(jobs):
            asked.append((j[2], j[-1]))
            yield i, [[0, n, 500.0, 0.01, "asia", "normal", 1.0] for n in j[-1]]

    monkeypatch.setattr(pbk, "_gather", fake_gather)
    table = pb.build(now_us=T0 + 10 * 86_400 * S, workers=1)
    assert asked == [("2026-09-24", ("Smart 0", "Smart +2", "Smart +3"))]
    done, rows = pbk.read_day(pb.cache_path("SPY-USD", "2026-09-24", rk))
    assert done == set(pbk.SETUPS) and len(rows) == len(old) + 3
    assert pbk.read_day(pb.cache_path("SPY-USD", "2026-09-23", rk))[0] == set(pbk.FIRST_SETUPS)
    setups = table["markets"]["SPY-USD"]["setups"]
    assert setups["Smart 0"]["all"][0] == 1 and setups["Mid 0"]["all"][0] == 4
    asked.clear()
    pb.build(now_us=T0 + 10 * 86_400 * S, workers=1)
    assert asked == []                                                     # nothing left to add
