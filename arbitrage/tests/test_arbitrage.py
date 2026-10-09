"""The funding arbitrage arithmetic, the venues' payloads and the paper record. Offline: no network."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from arbitrage import feeds, paper
from arbitrage.config import load
from arbitrage.rank import Leg, Settings, conservative_edge, max_leverage, plan, round_trip_bp
from arbitrage.scan import aligned
from arbitrage.venues import arcus_history, arcus_leg, lighter_history, lighter_leg, sigma_day

ARCUS_BABA = {
    "marketDisplayName": "BABA-USD", "baseAsset": "BABA", "type": "PERPETUAL", "status": "ONLINE", "tickSize": "0.01",
    "stepSize": "0.0000001", "minOrderNotional": "5", "minOrderSize": "0.01", "markPrice": "106.42",
    "fundingRate": "0.0000171", "nextFundingRate": "0.0000160", "nextFundingAt": 1791061200,
    "volume24hNotional": "522236.12", "openInterest": "909.2", "initialMarginFraction": "0.1",
    "maintenanceMarginFraction": "0.066667", "offHoursInitialMarginFraction": "0.15", "isOutsideRth": True}
LIGHTER_BABA = {
    "symbol": "BABA", "market_id": 19, "market_type": "perp", "status": "active", "min_base_amount": "0.0400",
    "min_quote_amount": "10.000000", "size_decimals": 4, "price_decimals": 2, "min_initial_margin_fraction": 1000,
    "maintenance_margin_fraction": 600, "mark_price": "106.40", "daily_quote_token_volume": 245128.25,
    "open_interest": 4354.5, "market_config": {"force_reduce_only": False}}


def legs() -> tuple[Leg, Leg]:
    a = arcus_leg(ARCUS_BABA)
    b = lighter_leg(LIGHTER_BABA, 3.2e-05, 1791059000)
    assert a is not None and b is not None
    return a, b


def test_the_venues_payloads_read_as_hourly_rates() -> None:
    a, b = legs()
    assert (a.symbol, a.venue, a.online, a.off_hours) == ("BABA", "arcus", True, True)
    assert a.rate_h == pytest.approx(1.71e-5) and a.next_rate_h == pytest.approx(1.6e-5) and a.next_at == 1791061200
    assert (a.imf, a.imf_off, a.mmf) == (0.1, 0.15, 0.066667)
    assert b.next_rate_h == pytest.approx(4e-6)                  # Lighter's figure is for 8 hours
    assert b.next_at == 1791061200                               # the top of the next hour
    assert (b.imf, b.mmf, b.step, b.tick, b.min_notional) == (0.1, 0.06, 1e-4, 0.01, 10.0)
    assert arcus_leg({**ARCUS_BABA, "type": "SPOT"}) is None
    assert lighter_leg({**LIGHTER_BABA, "status": "paused"}, None, 0).online is False       # type: ignore[union-attr]
    assert lighter_history([{"timestamp": 1790974800, "rate": "0.0008", "direction": "long"},
                            {"timestamp": 1790978400, "rate": "0.0004", "direction": "short"}]) == {
        1790974800: pytest.approx(8e-6), 1790978400: pytest.approx(-4e-6)}
    assert arcus_history([{"fundingRate": "0.0000125", "time": 1791054000000000}]) == {1791054000: 1.25e-5}


def test_the_edge_is_the_smallest_of_three_that_agree() -> None:
    assert conservative_edge(3e-6, 2e-6, 5e-6) == 2e-6
    assert conservative_edge(-3e-6, -2e-6, -5e-6) == -2e-6
    assert conservative_edge(3e-6, -2e-6, 5e-6) == 0.0            # it was the other way round yesterday
    assert conservative_edge(0.0, 2e-6, 5e-6) == 0.0


def test_leverage_keeps_the_stop_three_daily_moves_away() -> None:
    a, b = legs()
    s = Settings()
    lev, stop, liq = max_leverage(a, b, 0.0167, s)
    # venues allow 1/0.15 = 6.7x held overnight; the stop rule allows 1/(3*0.0167/0.5 + 0.0667) = 5.99x
    assert lev == pytest.approx(1 / (3 * 0.0167 / 0.5 + 0.066667), rel=1e-6)
    assert liq == pytest.approx(1 / lev - 0.066667) and stop == pytest.approx(liq / 2)
    assert stop >= 3 * 0.0167 - 1e-9
    assert max_leverage(a, b, 0.001, s)[0] == pytest.approx(1 / 0.15)                 # the venue's off-hours margin
    assert max_leverage(a, b, 0.001, Settings(hold_off_hours=False))[0] == pytest.approx(10.0)
    assert max_leverage(a, b, 0.001, Settings(max_leverage=3))[0] == 3.0              # the owner's ceiling
    assert max_leverage(a, b, 0.5, s)[0] == 1.0                                       # never under 1x


def test_a_plan_shorts_the_higher_rate_with_equal_legs_and_mirrored_stops() -> None:
    a, b = legs()
    ha, hl = [1.7e-5] * 168, [4e-6] * 168                         # Arcus 15% a year, Lighter 3.5%
    p = plan(a, b, ha, hl, 0.0167, {"arcus": 120.0, "lighter": 120.0})
    assert p.go and (p.short_venue, p.long_venue) == ("arcus", "lighter")
    assert p.edge_h == pytest.approx(1.2e-5)                      # next payment 1.6e-5 - 4e-6: the smallest
    assert p.edge_apr == pytest.approx(10.512)
    assert p.notional == pytest.approx(120 * 0.9 * p.leverage, rel=1e-3)
    assert p.size == pytest.approx(round(p.size, 4))              # Lighter's size step
    assert p.income_day == pytest.approx(p.notional * 1.2e-5 * 24)
    assert p.breakeven_h == pytest.approx(4e-4 / 1.2e-5)          # 4 fills of 1 bp: 33 h
    long_, short = p.prices["lighter"], p.prices["arcus"]
    assert long_["stop"] < long_["entry"] < long_["take"] and short["take"] < short["entry"] < short["stop"]
    assert long_["liq"] < long_["stop"] and short["liq"] > short["stop"]
    assert long_["stop"] / long_["entry"] == pytest.approx(1 - p.stop_dist)
    assert short["stop"] / short["entry"] == pytest.approx(1 + p.stop_dist)      # where the long leg takes profit


def test_what_stops_a_plan() -> None:
    a, b = legs()
    money = {"arcus": 120.0, "lighter": 120.0}
    flipped = plan(a, b, [1.7e-5] * 144 + [1e-6] * 24, [4e-6] * 168, 0.0167, money)
    assert not flipped.go and "changed sides" in flipped.reasons[0]
    assert (flipped.short_venue, flipped.edge_24h < 0 < flipped.edge_next_h) == ("arcus", True)
    weekend = plan(arcus_leg({**ARCUS_BABA, "nextFundingRate": "0.00000505"}), b,        # type: ignore[arg-type]
                   [5.05e-6] * 168, [4e-6] * 168, 0.0167, money)
    assert not weekend.go and "under the 5% floor" in weekend.reasons[0]
    no_money = plan(a, b, [1.7e-5] * 168, [4e-6] * 168, 0.0167, {"arcus": 0.0, "lighter": 240.0})
    assert no_money.reasons == ["no collateral on arcus"] and no_money.notional == 0
    wide = plan(a, b, [1.7e-5] * 168, [4e-6] * 168, 0.0167, money, spreads_bp={"arcus": 12.0, "lighter": 1.0})
    assert round_trip_bp(Settings(), {"arcus": 12.0, "lighter": 1.0}) == 14.0     # 2 x (6 + 1)
    assert not wide.go and "to pay for getting in and out" in wide.reasons[0]
    quiet = lighter_leg({**LIGHTER_BABA, "daily_quote_token_volume": 20_000}, 3.2e-05, 0)
    assert quiet is not None
    thin = plan(a, quiet, [1.7e-5] * 168, [4e-6] * 168, 0.0167, money)
    assert any(r.startswith("thin") for r in thin.reasons)
    young = plan(a, b, [1.7e-5] * 5, [4e-6] * 5, 0.0167, money)
    assert any("funding history" in r for r in young.reasons)
    dear = lighter_leg(LIGHTER_BABA, 2.4e-04, 0)                 # Lighter at 3e-5 an hour: now the one to short
    assert dear is not None
    long_arcus = plan(a, dear, [1.7e-5] * 168, [3e-5] * 168, 0.0167, money)
    assert long_arcus.go and (long_arcus.short_venue, long_arcus.long_venue) == ("lighter", "arcus")
    assert long_arcus.edge_h == pytest.approx(1.3e-5) and long_arcus.prices["arcus"]["side"] == 1.0


def test_sigma_ignores_hours_when_the_market_was_closed() -> None:
    closes = [100.0]
    for i in range(48):
        closes.append(closes[-1] * (1.002 if i % 2 else 0.998))
    busy = sigma_day(closes)
    assert busy == pytest.approx(0.002 * math.sqrt(24), rel=0.01)
    assert sigma_day(closes + [closes[-1]] * 60) == pytest.approx(busy)      # a flat weekend does not dilute it
    assert sigma_day([100.0] * 30) == 0.0


def test_history_lines_up_by_hour() -> None:
    a = {3600 * i: float(i) for i in range(10)}
    b = {3600 * i: float(-i) for i in range(3, 12)}
    xa, xb = aligned(a, b, hours=4)
    assert xa == [6.0, 7.0, 8.0, 9.0] and xb == [-6.0, -7.0, -8.0, -9.0]


def test_the_paper_record_counts_payments_prices_and_fills() -> None:
    a, b = legs()
    p = plan(a, b, [1.7e-5] * 168, [4e-6] * 168, 0.0167, {"arcus": 120.0, "lighter": 120.0})
    t0 = 1_791_000_000.0
    pos = paper.open_position(p, {"arcus": 106.42, "lighter": 106.40}, Settings(), now=t0)
    h0 = int(t0) // 3600 * 3600
    hist = {"arcus": {h0 + 3600 * i: 1.7e-5 for i in range(6)}, "lighter": {h0 + 3600 * i: 4e-6 for i in range(6)}}
    now = h0 + 3 * 3600 + 60
    earned, n = paper.funding_earned(pos, hist, now)
    assert n == 3 and earned == pytest.approx(3 * 1.3e-5 * pos.notional)   # not the hour it opened in
    r = paper.mark(pos, {"arcus": 107.42, "lighter": 107.30}, hist, now)
    assert r["long_leg"] == pytest.approx(pos.size * 0.90) and r["short_leg"] == pytest.approx(-pos.size * 1.00)
    assert r["basis"] == pytest.approx(-pos.size * 0.10)                         # the two venues drifted 10 cents apart
    assert r["cost"] == pytest.approx(pos.notional * 2e-4)                       # two fills so far
    assert r["net"] == pytest.approx(earned + r["basis"] - r["cost"]) and r["stop_hit"] == 0.0
    closing = paper.mark(pos, {"arcus": 107.42, "lighter": 107.30}, hist, now, closing=True)
    assert closing["cost"] == pytest.approx(pos.notional * 4e-4)
    far = paper.mark(pos, {"arcus": 106.42 * (1 + pos.stop_dist), "lighter": 106.40 * (1 + pos.stop_dist)}, hist, now)
    assert far["stop_hit"] == 1.0


def test_the_paper_book_survives_a_restart(tmp_path: Path) -> None:
    a, b = legs()
    p = plan(a, b, [1.7e-5] * 168, [4e-6] * 168, 0.0167, {"arcus": 120.0, "lighter": 120.0})
    book = {"BABA": paper.open_position(p, {"arcus": 106.42, "lighter": 106.40}, Settings(), now=1.0)}
    paper.save(tmp_path / "paper.json", book)
    assert paper.load(tmp_path / "paper.json") == book and paper.load(tmp_path / "none.json") == {}
    paper.log_close(tmp_path / "h.jsonl", book["BABA"], {"net": 1.5}, 2.0)
    row = json.loads((tmp_path / "h.jsonl").read_text())
    assert row["symbol"] == "BABA" and row["net"] == 1.5 and row["closed_at"] == 2.0


def test_feeds_keep_only_the_two_venues() -> None:
    rows = [{"symbol": "PONS/USDC", "long_exchange": "LighterRH", "short_exchange": "Arcus", "net_apr": 66.4,
             "breakeven_days": 0.57}, {"symbol": "ETH/USDC", "long_exchange": "Kraken", "short_exchange": "Arcus"},
            {"symbol": "BTC/USDC", "long_exchange": "Arcus", "short_exchange": "LighterRH", "net_apr": 1.8}]
    got = feeds.profunding_pairs(rows)
    assert got["PONS"] == {"long": "lighter", "short": "arcus", "net_apr": 66.4, "breakeven_days": 0.57, "at": None}
    assert got["BTC"]["short"] == "lighter" and "ETH" not in got
    text = ('1c:["$","a",null,{"href":"/arbitrage/crypto/zec/lighter_rh-vs-arcus"}]\n'
            '2:{"href":"/arbitrage/rwa/ai/lighter_rh-vs-aster"}')
    assert feeds.arbsh_pairs(text) == [("ai", "lighter_rh", "aster"), ("zec", "lighter_rh", "arcus")]


async def test_profunding_answers_from_its_cache_and_needs_no_key_for_that(tmp_path: Path) -> None:
    import time
    cache = tmp_path / "pf.json"
    assert await feeds.profunding("", cache) == {}                               # no key, no cache: nothing asked
    cache.write_text(json.dumps({"ts": time.time(), "pairs": {"SPY": {"long": "lighter", "short": "arcus"}}}))
    assert await feeds.profunding("", cache) == {"SPY": {"long": "lighter", "short": "arcus"}}


def test_settings_and_ids_come_from_files(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"max_leverage": 10, "fill_cost_bp": 1.5, "hold_off_hours": False, "unknown": 1}))
    cfg = load({"ARCUS_ADDRESS": " 0xabc ", "LIGHTER_ACCOUNT_INDEX": "12345"}, f)
    assert (cfg.arcus_address, cfg.arcus_account, cfg.lighter_account, cfg.profunding_key) == ("0xabc", 0, 12345, "")
    assert (cfg.settings.max_leverage, cfg.settings.fill_cost_bp, cfg.settings.hold_off_hours) == (10.0, 1.5, False)
    assert load({}, tmp_path / "missing.json").settings == Settings()


async def test_telegram_messages_become_the_same_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from arbitrage import config, telegram

    t = telegram.to_argv
    assert t("/hold 72", False) == ["set", "max_hold_h", "72"] and t("/sl auto", False) == ["set", "stop_pct", "auto"]
    assert t("/minhold 0", True) == ["set", "min_hold_h", "0"] and t("/closenow", True) == ["close", "--now", "--live"]
    assert t("/status@the_bot", True) == ["status", "--live"] and t("/skip cashcat", False) == ["skip", "cashcat"]
    assert t("/run --live", True) is None and t("hello", False) is None and t("/backtest", False) is None
    assert t("/start live", True) is None and t("/stop", True) is None      # the executor itself: arbitrage/ops.py
    assert telegram.changes_something(["set", "max_hold_h", "72"]) and not telegram.changes_something(["status"])
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr("arbitrage.cli.ROOT", tmp_path)
    assert "max_hold_h = 72" in await telegram.run_command(["set", "max_hold_h", "72"])
    assert config.load({}).settings.max_hold_h == 72.0                    # the running bot reads it on its next loop
    assert "must be between" in await telegram.run_command(["set", "max_hold_h", "-5"])
    assert "unknown setting" in await telegram.run_command(["set", "nonsense", "1"])
    assert config.set_value("stop_pct", "auto", tmp_path / "settings.json") == 0.0




def test_the_credentials_come_from_the_bots_one_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from arbitrage.config import read_env

    for k in ("ARCUS_ADDRESS", "ARB_LIVE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LIGHTER_ACCOUNT_INDEX",
              "PROFUNDING_API_KEY", "ARCUS_ACCOUNT_INDEX"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "arcus").mkdir()
    (tmp_path / "arbitrage").mkdir()
    (tmp_path / "arcus" / ".env").write_text("ARCUS_API_PRIVATE_KEY=never-read-here\nARCUS_ADDRESS=0xone\nARB_LIVE=1\n"
                                           "TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=5\nLIGHTER_ACCOUNT_INDEX=7\n")
    e = {k: v for k, v in read_env(tmp_path / "arbitrage" / ".env").items() if k != "ARB_NO_SPAWN"}
    assert e == {"ARCUS_ADDRESS": "0xone", "ARB_LIVE": "1", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "5",
                 "LIGHTER_ACCOUNT_INDEX": "7"}
    (tmp_path / "arbitrage" / ".env").write_text("ARCUS_ADDRESS=0xtwo\nARB_LIVE=\n")
    e = read_env(tmp_path / "arbitrage" / ".env")
    assert e["ARCUS_ADDRESS"] == "0xtwo" and e["ARB_LIVE"] == "1"          # an empty line there changes nothing


def test_the_background_executor_is_never_started_without_its_conditions(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import os

    from arbitrage import ops

    monkeypatch.setenv("ARB_NO_SPAWN", "1")
    monkeypatch.delenv("ARB_LIVE", raising=False)
    monkeypatch.setattr(ops, "read_env", lambda: {})
    ok, msg = ops.start("live", state=tmp_path)
    assert not ok and "ARB_LIVE=1" in msg                                   # the owner's switch comes first
    ok, msg = ops.start("paper", {"arcus": 120.0, "lighter": 0.0}, state=tmp_path)
    assert not ok and "pretend" in msg
    with pytest.raises(RuntimeError):                                       # and never under tests
        ops.start("paper", {"arcus": 120.0, "lighter": 120.0}, state=tmp_path)
    assert ops.command("paper", {"arcus": 120.0, "lighter": 80.0})[-5:] == ["run", "--arcus", "120", "--lighter", "80"]
    assert ops.command("live", None)[-3:] == ["run", "--live", "--yes"]

    assert ops.running("paper", tmp_path) is None and ops.stop("paper", state=tmp_path) == (False, "paper: not running")
    ops.pid_path("paper", tmp_path).write_text(str(os.getpid()))
    assert ops.running("paper", tmp_path) == os.getpid() and ops.start("paper", state=tmp_path)[1].startswith(
        "paper: already running")
    assert ops.current_mode(tmp_path) == "paper"
    (tmp_path / "position-live.json").write_text(json.dumps({"phase": "open"}))
    assert ops.current_mode(tmp_path) == "live" and ops.phase("live", tmp_path) == "open"
    (tmp_path / "position-live.json").write_text(json.dumps({"phase": "flat"}))
    assert ops.current_mode(tmp_path) == "paper" and not ops.alive(0)



def test_the_executor_starts_only_on_a_machine_that_trades(tmp_path, monkeypatch):
    """Two machines (BOT_ROLE): a recorder or a scout machine never starts the arbitrage."""
    from arbitrage import ops
    from arbitrage.config import no_trading

    assert no_trading({}) == "" and no_trading({"BOT_ROLE": "trader"}) == "" and no_trading({"BOT_ROLE": "ALL"}) == ""
    assert "recorder" in no_trading({"BOT_ROLE": "recorder"}) and "must be one of" in no_trading({"BOT_ROLE": "x"})
    for r in ("recorder", "scout"):
        monkeypatch.setenv("BOT_ROLE", r)
        ok, msg = ops.start("paper", {"arcus": 120.0, "lighter": 120.0}, state=tmp_path)
        assert ok is False and f"this machine is a {r}" in msg and not (tmp_path / "run-paper.pid").exists()
