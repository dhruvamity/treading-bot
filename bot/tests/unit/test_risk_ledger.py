"""C5 ledger identity, C6 every kill switch, pre-trade checks, DMS, guardian, calendar, budget governor."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from bot.common.config import RiskLimitsCfg, SafetyPauseCfg
from bot.common.errors import PreTradeReject
from bot.common.ids import ClientIdFactory
from bot.core.alerts import Alerter
from bot.core.budget import ArcusGovernor, BudgetMode
from bot.core.calendar import TradingCalendar, in_ist_windows
from bot.core.dms import DeadMansSwitch
from bot.core.guardian import GuardedVenue, Guardian, flatten_orders
from bot.core.heartbeat import write_heartbeat
from bot.core.ledger import Ledger
from bot.core.marketdata import MarketView
from bot.core.risk import AccountSnapshot, MarketLimits, RiskAction, RiskContext, RiskEngine
from bot.venues.base import TIF, Fill, OrderRequest, Position, Side, Venue
from tests.helpers import fixture_markets

MK = fixture_markets()
BTC = MK[Venue.ARCUS]["BTC"]
T0 = 1_790_000_000_000_000


def fill(side: Side, px: str, sz: str, *, fee: str = "0", liq: bool = False, tid: str = "1", maker: bool = True) -> Fill:
    return Fill(Venue.ARCUS, "BTC", "c", side, D(px), D(sz), D(fee), maker, liq, T0, tid)


# ------------------------------------------------------------------------------------------------ ledger (C5)
def test_ledger_identity_and_terms() -> None:
    lg = Ledger()
    seq = [(Side.BUY, "100", "1", "100.5", "0.01"), (Side.SELL, "101", "1", "100.5", "0.01"),
           (Side.SELL, "99", "2", "100", "0.02"), (Side.BUY, "98", "1", "97.9", "0")]
    cash = D(0)
    fees = D(0)
    pos = D(0)
    for i, (side, px, sz, mid, fee) in enumerate(seq):
        lg.on_fill(fill(side, px, sz, fee=fee, tid=str(i)), D(mid))
        cash -= D(sz) * side.sign * D(px)
        fees += D(fee)
        pos += D(sz) * side.sign
    lg.on_funding(Venue.ARCUS, "BTC", D("0.37"))
    liq = fill(Side.BUY, "105", "1", fee="1.05", liq=True, tid="L")
    lg.on_fill(liq, D("104"))
    cash -= D("105")
    pos += 1
    mark = D("103")
    b = lg.breakdown(Venue.ARCUS, "BTC", mark)
    d_equity = cash + pos * mark + D("0.37") - fees - D("1.05")
    assert b.net == d_equity  # exact identity
    assert b.spread_capture == D("0.5") + D("0.5") + D("-2") + D("-0.1")
    assert b.liquidation_loss == D("1") + D("1.05")  # costs are positive
    assert b.volume == D("100") + D("101") + D("198") + D("98") + D("105")
    assert b.cpm is not None


def test_oi_hours() -> None:
    lg = Ledger()
    lg.mark(Venue.ARCUS, "BTC", D("100"), T0)
    lg.on_fill(fill(Side.BUY, "100", "2"), D("100"))
    lg.mark(Venue.ARCUS, "BTC", D("100"), T0 + 3_600_000_000)
    assert lg.breakdown(Venue.ARCUS, "BTC").oi_hours_usd == D("200")


# ------------------------------------------------------------------------------------------------ pre-trade
def engine(**ctx: object) -> RiskEngine:
    r = RiskEngine(limits=RiskLimitsCfg(), safety=SafetyPauseCfg())
    r.ctx = RiskContext(bbo=lambda v, b: (D("86000.0"), D("86000.1")), oracle=lambda v, b: D("86000"),
                        account=lambda v: AccountSnapshot(D(100), D(100)), **ctx)  # type: ignore[arg-type]
    r.market_limits[(Venue.ARCUS, "BTC")] = MarketLimits(position_cap_usd=D("37.5"), leverage_cap=D(5))
    return r


def req(side: Side, px: str, sz: str, tif: TIF = TIF.POST_ONLY, ro: bool = False) -> OrderRequest:
    return OrderRequest(Venue.ARCUS, "BTC", side, D(px), D(sz), tif, ro, "cid")


def test_pretrade_checks() -> None:
    r = engine()
    r.check(req(Side.BUY, "85990.0", "0.00012"), BTC)
    cases = [
        (req(Side.BUY, "85990.05", "0.00012"), "alignment"),
        (req(Side.BUY, "85990.0", "0.00005"), "min_size"),
        (req(Side.BUY, "86000.1", "0.00012"), "post_only_cross"),
        (req(Side.SELL, "86000.0", "0.00012"), "post_only_cross"),
        (req(Side.BUY, "80000.0", "0.00012"), "oracle_deviation"),
        (req(Side.BUY, "85990.0", "0.0005"), "position_cap"),
    ]
    for rq, check in cases:
        with pytest.raises(PreTradeReject) as e:
            r.check(rq, BTC)
        assert e.value.check == check, (rq, e.value)
    r.check(req(Side.SELL, "86001.0", "0.00012", ro=True), BTC)  # reduce-only skips the exposure checks


def test_pretrade_collateral_and_oi_cap() -> None:
    r = engine()
    r.ctx.account = lambda v: AccountSnapshot(D(10), D("0.1"))
    with pytest.raises(PreTradeReject) as e:
        r.check(req(Side.BUY, "85990.0", "0.00012"), BTC)
    assert e.value.check == "free_collateral"
    r2 = engine(oi=lambda v, b: (D("23.26"), D("2000000")))  # 23.26 BTC x ~$86k > $2M cap
    with pytest.raises(PreTradeReject) as e:
        r2.check(req(Side.BUY, "85990.0", "0.00012"), BTC)
    assert e.value.check == "oi_cap"


def test_an_order_that_reduces_the_position_needs_no_margin() -> None:
    """2026-09-25, live QQQ after the US close: long $416, ~$12 of free collateral at the off-hours margin; the reducing
    sell was refused as if it opened a position (it wanted ~$20) ~4,900 times and the bot could not work it off."""
    r = engine(position=lambda v, b: D("0.004"))                        # long 0.004 BTC (~$344)
    r.market_limits[(Venue.ARCUS, "BTC")] = MarketLimits(position_cap_usd=D(500), leverage_cap=D(5))
    r.ctx.account = lambda v: AccountSnapshot(D(30), D("0.01"))       # no free collateral; 11.5x > the 5x cap
    r.check(req(Side.SELL, "86001.0", "0.002"), BTC)                   # reduces: passes margin, OI and leverage
    r.check(req(Side.SELL, "86001.0", "0.004"), BTC)                   # closes it: passes too
    with pytest.raises(PreTradeReject) as e:
        r.check(req(Side.SELL, "86001.0", "0.006"), BTC)               # flips to short 0.002: that part needs margin
    assert e.value.check == "free_collateral" and "needs $" in str(e.value)
    with pytest.raises(PreTradeReject) as e:
        r.check(req(Side.BUY, "85990.0", "0.00012"), BTC)              # adds to the long: refused
    assert e.value.check == "free_collateral"
    covered = engine(position=lambda v, b: D("0.004"), open_notional=lambda v, b, s, x: D(344))
    covered.market_limits[(Venue.ARCUS, "BTC")] = MarketLimits(position_cap_usd=D(500), leverage_cap=D(20))
    covered.ctx.account = lambda v: AccountSnapshot(D(30), D("0.01"))
    with pytest.raises(PreTradeReject) as e:                           # resting sells already cover the long:
        covered.check(req(Side.SELL, "86001.0", "0.002"), BTC)         # another sell would open a short
    assert e.value.check == "free_collateral"


# ------------------------------------------------------------------------------------------------ kill switches (C6)
def test_kill_session_sl_daily_loss_drawdown() -> None:
    r = RiskEngine(limits=RiskLimitsCfg())
    ds = r.on_pnl(venue=Venue.ARCUS, session_id="s", session_pnl=D("-3.6"), session_margin=D(35), stop_loss_pct=10,
                  take_profit_pct=None, day_pnl=D("-3.6"), capital=D(100), equity=D("96.4"), ts_us=T0)
    acts = {d.action for d in ds}
    assert RiskAction.FLATTEN_SESSION in acts and RiskAction.STOP_VENUE_DAY in acts
    ds = r.on_pnl(venue=Venue.ARCUS, session_id="s", session_pnl=D(0), session_margin=D(35), stop_loss_pct=10,
                  take_profit_pct=None, day_pnl=D(0), capital=D(100), equity=D("86"), ts_us=T0)
    assert any(d.action is RiskAction.STOP_ALL for d in ds)  # peak 96.4 -> 86 is > 10% of $100
    with pytest.raises(PreTradeReject):
        r.check(req(Side.BUY, "85990.0", "0.00012"), BTC)
    r.roll_day(T0 + 86_400_000_000)
    assert Venue.ARCUS not in r.venue_stopped_day


def test_kill_take_profit_and_kill_usd() -> None:
    r = RiskEngine(limits=RiskLimitsCfg())
    ds = r.on_pnl(venue=Venue.ARCUS, session_id="s", session_pnl=D("3.6"), session_margin=D(35), stop_loss_pct=10,
                  take_profit_pct=10, day_pnl=D(0), capital=D(50), equity=D(50), ts_us=T0)
    assert any(d.trigger == "session_tp" for d in ds)
    ds = r.on_pnl(venue=Venue.ARCUS, session_id="s", session_pnl=D(0), session_margin=D(35), stop_loss_pct=10,
                  take_profit_pct=None, day_pnl=D(0), capital=D(50), equity=D("45.9"), ts_us=T0, kill_usd=D(4))
    assert any(d.action is RiskAction.STOP_ALL for d in ds)  # $4.10 below the peak > the $4 kill


def test_kill_safety_pause_and_resume() -> None:
    r = RiskEngine(safety=SafetyPauseCfg())
    v = MarketView(Venue.ARCUS, "BTC")
    v.book.load([(D("100"), D("5"))], [(D("100.1"), D("5"))], 1)
    for i in range(200):
        v.vol_1s.update(0.0001 if i % 2 else -0.0001)
    v.last_move_1s = 0.01  # 100 sigma
    d = r.safety_pause(v, T0)
    assert d is not None and d.action is RiskAction.PAUSE_QUOTES
    assert r.quoting_allowed(Venue.ARCUS, "BTC", T0 + 1)[0] is False
    v.last_move_1s = 0.0
    assert r.safety_pause(v, T0 + 10_000_000) is None
    assert r.quoting_allowed(Venue.ARCUS, "BTC", T0 + 31_000_000)[0] is True
    # quiet-market sigma (0.3 bp) is floored at 1 bp: a routine 2.2 bp tick is not a 6-sigma move, 7 bp is
    r = RiskEngine(safety=SafetyPauseCfg())
    q = MarketView(Venue.ARCUS, "BTC")
    for i in range(120):
        q.vol_1s.update(0.00003 if i % 2 else -0.00003)
    q.last_move_1s = 0.00022
    assert r.safety_pause(q, T0) is None
    q.last_move_1s = 0.0007
    assert r.safety_pause(q, T0) is not None


def test_kill_spread_and_depth_pause() -> None:
    r = RiskEngine()
    v = MarketView(Venue.ARCUS, "BTC")
    v.book.load([(D("100"), D("5"))], [(D("100.1"), D("5"))], 1)
    for _ in range(600):
        v.spread_med_1h.add(1.0)
        v.depth_med_1h.add(1000.0)
    v.book.load([(D("99"), D("0.01"))], [(D("101"), D("0.01"))], 2)
    d = r.safety_pause(v, T0)
    assert d is not None and "spread" in d.reason and "depth" in d.reason
    # tick-constrained book: 3x a 0.01 bp median is not a pause unless the excess is >= 1 bp
    r2, v2 = RiskEngine(), MarketView(Venue.ARCUS, "BTC")
    for _ in range(600):
        v2.spread_med_1h.add(0.0116)
        v2.depth_med_1h.add(1000.0)
    v2.book.load([(D("86000.0"), D("5"))], [(D("86000.4"), D("5"))], 1)  # 0.47 bp
    assert r2.safety_pause(v2, T0) is None


def test_kill_event_window_band_oi_liq_reject_safe_mode() -> None:
    cal = TradingCalendar.load(Path(__file__).parents[2] / "config" / "calendars")
    r = RiskEngine(calendar=cal)
    fomc = next(e for e in cal.events if e.kind == "fomc")
    assert r.event_window(Venue.ARCUS, "BTC", "CRYPTO", fomc.ts_us + 10 * 60_000_000, {"fomc"}) is not None
    assert r.event_window(Venue.ARCUS, "BTC", "CRYPTO", fomc.ts_us + 40 * 60_000_000, {"fomc"}) is None
    v = MarketView(Venue.ARCUS, "SPY")
    v.book.load([(D("700"), D("5"))], [(D("700.1"), D("5"))], 1)
    v.upper_in_zone = True
    d = r.band_or_oi(v, MK[Venue.ARCUS]["SPY"])
    assert d is not None and d.action is RiskAction.STOP_MARKET_QUOTING
    assert not r.quoting_allowed(Venue.ARCUS, "SPY", T0)[0]
    v.upper_in_zone = False
    assert r.band_or_oi(v, MK[Venue.ARCUS]["SPY"]) is None
    n_sig, d = r.liquidation_distance(Venue.ARCUS, "SPY", collateral=D(25), notional=D(75), mmf=D("0.012"),
                                      sigma_1h=0.1)
    assert d is not None and d.action is RiskAction.REDUCE_HALF and n_sig < 4
    _, d2 = r.liquidation_distance(Venue.ARCUS, "SPY", collateral=D(25), notional=D(75), mmf=D("0.012"),
                                   sigma_1h=0.003)
    assert d2 is None and (Venue.ARCUS, "SPY") not in r.reduce_active  # back above 6 sigma
    d = r.on_reject(Venue.ARCUS, "SELF_TRADE")
    assert d is not None and d.action is RiskAction.STOP_VENUE_CRIT
    assert r.on_reject(Venue.ARCUS, "POST_ONLY_WOULD_CROSS") is None
    r.enter_safe_mode(Venue.ARCUS, "dms failed twice")
    assert not r.quoting_allowed(Venue.ARCUS, "BTC", T0)[0]
    r.resume(Venue.ARCUS)
    assert r.quoting_allowed(Venue.ARCUS, "BTC", T0)[0]


def test_liquidation_distance_worked_example() -> None:
    """A6.7: C = $25, N = $75, MMF 1.2% -> ~32% adverse move."""
    from tests.sim.margin import distance_to_liquidation

    assert abs(distance_to_liquidation(D(25), D(75), D("0.012")) - D("0.3213333333")) < D("1e-9")


# ------------------------------------------------------------------------------------------------ budget (A6.8)
def test_arcus_governor_modes() -> None:
    g = ArcusGovernor(order_remaining=20_000, order_cap=20_000)
    assert g.mode() is BudgetMode.NORMAL
    g.record_actions(17_000)
    assert g.mode() is BudgetMode.WIDE and g.hysteresis_mult() == 2.0
    g.record_actions(2_500)
    assert g.mode() is BudgetMode.CANCELS_ONLY and not g.can_act("place") and g.can_act("cancel")
    g.record_fill(6.0)  # a $6 fill funds 60 actions (to both remaining and cap)
    assert g.order_remaining == 560 and g.order_cap == 20_060


def test_arcus_governor_ratio() -> None:
    g = ArcusGovernor(order_remaining=10**6, order_cap=10**6, warmup_actions=10)
    g.record_actions(100, now=0.0)
    g.record_fill(10.0, now=0.0)  # 100 actions / $10 = 10 > 8
    assert g.actions_per_filled_usd(now=1.0) == 10.0 and g.mode(now=1.0) is BudgetMode.WIDE


# ------------------------------------------------------------------------------------------------ DMS / guardian
async def test_dms_refresh_and_safe_mode_after_two_failures() -> None:
    calls: list[int | None] = []
    fail = {"on": False}

    async def arm(deadline: int | None) -> None:
        if fail["on"]:
            raise ConnectionError("down")
        calls.append(deadline)

    tripped: list[str] = []
    now = {"t": T0}
    dms = DeadMansSwitch("arcus:1", arm, refresh_s=20, deadline_s=60, on_failure=tripped.append,
                         now_us=lambda: now["t"])
    assert await dms.refresh_once()
    assert calls[-1] == T0 + 60_000_000
    fail["on"] = True
    await dms.refresh_once()
    assert not tripped
    await dms.refresh_once()
    assert tripped and "failed twice" in tripped[0]
    fail["on"] = False
    now["t"] = T0 + 61_000_000  # past the last deadline: the venue fired
    await dms.refresh_once()
    assert dms.fires_remaining_today() == 9


class _FakeAdapter:
    def __init__(self, equity: str, positions: list[Position]) -> None:
        self.equity = D(equity)
        self._pos = positions
        self.cancel_all_calls = 0
        self.placed: list[OrderRequest] = []

    async def cancel_all(self, base: str | None = None) -> None:
        self.cancel_all_calls += 1

    async def balances(self) -> dict[str, D]:
        return {"equity": self.equity}

    async def positions(self) -> list[Position]:
        return self._pos

    async def place(self, orders: list[OrderRequest]) -> list[object]:
        self.placed += orders
        return []


async def test_guardian_heartbeat_and_drawdown(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    pos = [Position(Venue.ARCUS, "BTC", D("0.0003"), D("86000"), D("86000"), D(0), "cross", None)]
    ad = _FakeAdapter("100", pos)
    g = Guardian(hb, [GuardedVenue(Venue.ARCUS, ad, {"BTC": BTC}, capital_usd=D(100))], Alerter(),
                 taker_after_s=0.0)
    acts = await g.check_once()  # no heartbeat file -> stale
    assert "heartbeat_cancel_all" in acts and ad.cancel_all_calls == 1
    write_heartbeat(hb)
    acts = await g.check_once()
    assert acts == []
    ad.equity = D("89")
    acts = await g.check_once()
    assert "drawdown_flatten" in acts
    assert ad.placed and all(o.reduce_only for o in ad.placed)
    assert {o.tif for o in ad.placed} == {TIF.POST_ONLY, TIF.IOC}


async def test_guardian_stands_down_when_the_bot_stops_on_purpose(tmp_path: Path) -> None:
    """2026-09-25: a live run stopped from Telegram left its guardian running, which a minute later raised a false
    CRITICAL "heartbeat silent" and sent a cancel-all. A clean stop now leaves a last heartbeat saying so."""
    hb = tmp_path / "hb"
    write_heartbeat(hb, mode="live", stopped="old run")          # a stop from before this guardian started
    ad = _FakeAdapter("100", [])
    g = Guardian(hb, [GuardedVenue(Venue.ARCUS, ad, {"BTC": BTC}, capital_usd=D(100))], Alerter())
    g.started_us = int(json.loads(hb.read_text())["ts_us"]) + 1   # started after that stop
    assert await g.check_once() == [] and not g.stood_down and ad.cancel_all_calls == 0   # waits for its bot
    write_heartbeat(hb, mode="live")                              # its bot runs ...
    assert await g.check_once() == []
    write_heartbeat(hb, mode="live", stopped="quotes cancelled, bot stopped")   # ... and is stopped from Telegram
    assert await g.check_once() == ["stood_down"] and g.stood_down and ad.cancel_all_calls == 0
    await asyncio.wait_for(g.run(), 1)                            # its loop ends instead of alarming


def test_flatten_orders_only_reduce() -> None:
    pos = [Position(Venue.ARCUS, "BTC", D("-0.00025"), D("86000"), D("86000"), D(0), "cross", None)]
    out = flatten_orders(pos, {"BTC": BTC}, {"BTC": D("86000")}, ClientIdFactory("guardian", 1), Venue.ARCUS,
                         taker=True)
    assert len(out) == 1 and out[0].side is Side.BUY and out[0].reduce_only and out[0].size == D("0.00025")


# ------------------------------------------------------------------------------------------------ calendar
def test_calendar_sessions_and_ist() -> None:
    cal = TradingCalendar.load(Path(__file__).parents[2] / "config" / "calendars")
    from datetime import datetime

    from bot.common.time import NEW_YORK, dt_to_us

    def et(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
        return dt_to_us(datetime(y, mo, d, h, mi, tzinfo=NEW_YORK))

    assert cal.session_label(et(2026, 9, 23, 10)) == "rth"
    assert cal.session_label(et(2026, 9, 23, 5)) == "pre"
    assert cal.session_label(et(2026, 9, 23, 17)) == "post"
    assert cal.session_label(et(2026, 9, 23, 22)) == "overnight"
    assert cal.session_label(et(2026, 9, 26, 12)) == "weekend"
    assert cal.session_label(et(2026, 11, 26, 12)) == "weekend"  # Thanksgiving
    assert cal.session_label(et(2026, 11, 27, 14)) == "post"  # early close 13:00
    assert cal.coverage_days(et(2026, 9, 23, 0), "fomc") > 300
    ist = dt_to_us(datetime(2026, 9, 23, 1, 30, tzinfo=NEW_YORK))  # = 11:00 IST
    assert in_ist_windows(ist, ["06:30-12:30"]) and not in_ist_windows(ist, ["13:00-14:00"])
    assert in_ist_windows(ist, ["22:00-12:00"])  # wraps midnight
    assert in_ist_windows(ist, [])

    us = ["09:00-16:30"]
    assert cal.in_skip_window(et(2026, 9, 23, 9), us) == "09:00-16:30"      # a trading day, 09:00 New York
    assert cal.in_skip_window(et(2026, 9, 23, 8, 59), us) is None
    assert cal.in_skip_window(et(2026, 9, 23, 16, 30), us) is None           # the end is not in the window
    assert cal.in_skip_window(et(2026, 9, 26, 12), us) is None               # Saturday
    assert cal.in_skip_window(et(2026, 11, 26, 12), us) is None              # Thanksgiving
    assert cal.in_skip_window(et(2026, 11, 27, 9, 30), us) == "09:00-16:30"  # an early close still opens
    assert cal.in_skip_window(et(2026, 12, 7, 10), us) and cal.in_skip_window(et(2026, 12, 7, 10), []) is None


def test_alerter_rate_limit_and_redaction() -> None:
    a = Alerter(min_interval_s=30)

    async def go() -> None:
        assert await a.send(__import__("bot.core.alerts", fromlist=["Level"]).Level.WARN, "k", "x " + "ab" * 32)
        assert not await a.send(__import__("bot.core.alerts", fromlist=["Level"]).Level.WARN, "k", "again")

    asyncio.run(go())
    assert "abab" not in a.sent[0][2]
