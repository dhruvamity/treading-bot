"""The three top-3 lists (breakeven, volume, aggressive Mid), the cost budget, and running a pick at the recommended or
the maximum leverage from Telegram."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from bot.common import settings
from bot.scout import profiles as P
from bot.scout.scan import Pct, long_checks, passes_long
from tests.unit.test_telegram import _app, _bot, _running_paper, msg, press

LOSES = "loses $0.65 (2.32% of $28)/day over 4 days"


def row(market: str, setting: str, lev: float, *, vol: float = 5000.0, pnl: float = -0.5, at_max: bool = False,
        reasons: list[str] | None = None, money: list[str] | None = None, recent_pnl: float = -0.4,
        recent_volume: float = 4000.0, recent_checked: bool = True) -> dict[str, Any]:
    reasons = [LOSES] if reasons is None and pnl < -0.07 else (reasons or [])
    money = [r for r in reasons if r.startswith(P.MONEY_PREFIXES)] if money is None else money
    return {"market": market, "config": f"{setting} @ {lev:g}x", "setting": setting, "leverage": lev,
            "leverage_off": lev, "at_max": at_max, "go": not reasons, "reasons": reasons, "money_reasons": money,
            "cost_1k": max(0.0, -pnl) / vol * 1000, "days": 4, "fills_day": 60, "volume_day": vol, "pnl_day": pnl,
            "worst_day": pnl * 1.2, "day_stops": 0, "positive_days": 2, "recent_pnl": recent_pnl,
            "recent_fills": 50, "recent_volume": recent_volume, "tail_pnl": 0.0, "recent_checked": recent_checked,
            "order_usd": 112 * lev / 10, "cap_usd": 224 * lev / 10, "used_usd": 28, "capital_usd": 28,
            "taker_day": 0, "actions_per_usd": 3, "now": {}}


def scan_of(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ts_us": int(time.time() * 1e6), "markets": 3, "configs": 18, "capital": {"usd": 28},
            "top": [r for r in rows if r["go"]][:3], "ranked": rows, "all": rows}


ROWS = [
    row("SPY-USD", "touch 1bp", 50, vol=9234, pnl=-1.10, at_max=True),       # $0.119 per $1,000
    row("SPY-USD", "touch 1bp", 10, vol=6947, pnl=-0.43),                     # $0.062
    row("QQQ-USD", "touch 1bp", 25, vol=5287, pnl=-0.65, at_max=True),       # $0.123
    row("QQQ-USD", "touch 1bp", 10, vol=5642, pnl=-0.65),                     # $0.115
    row("QQQ-USD", "deep 2bp x2", 10, vol=5304, pnl=0.32),                    # breakeven: GO
    row("NVDA-USD", "improve touch", 5, vol=6240, pnl=-1.40),                 # $0.224: over the default budget
    row("HOOD-USD", "deep 3bp", 10, vol=9000, pnl=-0.3, reasons=["hit the 10% kill", LOSES]),   # a safety fail
    row("GLD-USD", "improve touch", 10, vol=8000, pnl=-0.4, recent_checked=False),   # its 24 h not re-run yet
]


# ------------------------------------------------------------------------------------------------ the lists
def test_breakeven_is_the_scans_go_list() -> None:
    assert [(c["market"], c["config"]) for c in P.top(scan_of(ROWS), "breakeven", 0.15)] == \
        [("QQQ-USD", "deep 2bp x2 @ 10x")]


def test_volume_takes_a_known_cost_but_never_a_safety_fail() -> None:
    top = P.top(scan_of(ROWS), "volume", 0.15)
    # SPY at 50x has the most volume; HOOD (kill) and GLD (24 h not re-checked) are out; NVDA is over budget
    assert [(c["market"], c["config"]) for c in top] == [("SPY-USD", "touch 1bp @ 50x"),
                                                         ("QQQ-USD", "touch 1bp @ 10x")]
    assert "hit the 10% kill" in P.verdict(ROWS[6], "volume", 0.15)
    assert any("not re-checked" in r for r in P.verdict(ROWS[7], "volume", 0.15))
    assert any("costs $0.22 per $1,000 (budget $0.15)" in r for r in P.verdict(ROWS[5], "volume", 0.15))
    assert [c["market"] for c in P.top(scan_of(ROWS), "volume", 0.25)] == ["SPY-USD", "NVDA-USD", "QQQ-USD"]


def test_max_volume_ignores_the_cost_but_never_a_safety_fail() -> None:
    top = P.top(scan_of(ROWS), "max", 0.15)
    # the most volume per market whatever it costs: NVDA ($0.22 per $1,000) is in, HOOD (kill) never is; GLD's
    # last 24 h was not re-run (only settings within the budget are), which this list does not require
    assert [c["market"] for c in top] == ["SPY-USD", "GLD-USD", "NVDA-USD"]
    assert "hit the 10% kill" in P.verdict(ROWS[6], "max", 0.15) and P.verdict(ROWS[5], "max", 0.01) == []


def test_the_owners_own_pick_is_never_judged() -> None:
    assert P.verdict(ROWS[6], "manual", 0.15) == []   # even a kill in the backtest: the owner chose it


def test_aggressive_only_lists_touch_settings() -> None:
    rows = [*ROWS, row("TSLA-USD", "deep 1bp", 10, vol=20000, pnl=-0.2)]     # cheap and big, but not aggressive
    top = P.top(scan_of(rows), "aggressive", 0.15)
    assert all(c["setting"] in P.AGGRESSIVE for c in top) and "TSLA-USD" not in {c["market"] for c in top}
    assert P.verdict(rows[-1], "aggressive", 0.15) == ["not a Aggressive Mid setting"]


def test_a_bad_last_day_drops_it() -> None:
    r = row("SPY-USD", "touch 1bp", 10, vol=6947, pnl=-0.43, recent_pnl=-2.0, recent_volume=5000)   # $0.40 today
    assert any(x.startswith("last 24 h cost $0.40") for x in P.verdict(r, "volume", 0.15))


def test_nearest_names_the_budget_that_would_let_them_in() -> None:
    near = P.nearest(scan_of(ROWS), "aggressive", 0.01)
    assert [c["market"] for c in near] == ["SPY-USD", "QQQ-USD", "NVDA-USD"]   # cheapest first, one per market


def test_max_leverage_sibling() -> None:
    s = scan_of(ROWS)
    assert P.at_max(s, ROWS[3])["config"] == "touch 1bp @ 25x"
    assert P.at_max(s, ROWS[0]) is ROWS[0]
    assert P.at_max(s, ROWS[4]) is None                                  # no max-leverage row for that setting


def test_older_scans_without_money_reasons_still_work() -> None:
    r = row("SPY-USD", "touch 1bp", 10, vol=6947, pnl=-0.43)
    del r["money_reasons"], r["cost_1k"]
    assert P.verdict(r, "volume", 0.15) == []


def test_profile_names_and_aliases() -> None:
    assert P.profile_of(None).key == "breakeven" and P.profile_of("agg").key == "aggressive"
    with pytest.raises(ValueError):
        P.profile_of("yolo")


# ------------------------------------------------------------------------------------------------ the scout
def _day(pnl: float, maker_usd: float, **kw: Any) -> dict[str, Any]:
    return {"config": "touch 1bp", "pnl": pnl, "maker_usd": maker_usd, "maker_fills": 60, "day_stops": 1,
            "killed": False, **kw}


def test_money_reasons_are_told_apart_and_the_budget_widens_the_shortlist() -> None:
    days = [_day(-0.6, 5000), _day(-0.7, 5500), _day(-0.5, 5200), _day(-0.6, 5400)]   # $0.115 per $1,000
    checks = long_checks(days, 28, Pct())
    assert checks and all(money for money, _r in checks)
    entry = {"days": {"d": days}, "risk": {"used_usd": 28}}
    assert not passes_long(entry, "touch 1bp", Pct())
    assert passes_long(entry, "touch 1bp", Pct(), volume_cost=0.15)
    assert not passes_long(entry, "touch 1bp", Pct(), volume_cost=0.10)
    killed = [*days[:3], _day(-0.6, 5400, killed=True)]
    assert not passes_long({"days": {"d": killed}, "risk": {"used_usd": 28}}, "touch 1bp", Pct(), volume_cost=5)


def test_volume_cost_setting() -> None:
    assert settings.parse("volume_cost", "$0.20") == 0.20
    assert settings.show("volume_cost", 0.2) == "$0.20 per $1,000"
    assert settings.volume_cost({}) == settings.DEFAULT_VOLUME_COST == 0.15
    with pytest.raises(ValueError):
        settings.parse("volume_cost", "9")


# ------------------------------------------------------------------------------------------------ pilot
def _pilot(tmp_path: Path, rows: list[dict[str, Any]]) -> tuple[Any, Any, Any]:
    from bot.scout.pilot import Pilot

    app = _app(tmp_path)
    bot, api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    bot.pilot = pilot
    (tmp_path / "data" / "scout").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "scout" / "latest.json").write_text(json.dumps(scan_of(rows)))
    return bot, api, pilot


def test_pilot_picks_from_a_list_at_either_leverage(tmp_path: Path) -> None:
    _, _, pilot = _pilot(tmp_path, ROWS)
    c = pilot.pick(2, "volume")
    assert (c["config"], c["profile"], c["lev"]) == ("touch 1bp @ 10x", "volume", "rec")
    m = pilot.pick(2, "volume", "max")
    assert (m["config"], m["lev"]) == ("touch 1bp @ 25x", "max")
    assert pilot.pick(1, "breakeven")["config"] == "deep 2bp x2 @ 10x"
    with pytest.raises(ValueError, match="nothing is in the Aggressive Mid list"):
        settings.save(tmp_path / "state", "volume_cost", 0.01, pilot.control.app.sizing)
        pilot.pick(1, "aggressive")


def test_a_setting_dropped_from_the_menu_is_refused_before_anything_stops(tmp_path: Path) -> None:
    # a scan made before the menu changed can still list "grid 10bp"; picking it must fail up front, not after the
    # running bot was closed to make room for it
    _, _, pilot = _pilot(tmp_path, [row("QQQ-USD", "grid 10bp", 10, vol=9000, pnl=0.1)])
    with pytest.raises(ValueError, match="no longer in the scout's menu"):
        pilot.pick(1, "volume")


def test_review_judges_a_deployment_by_its_own_list(tmp_path: Path) -> None:
    _, _, pilot = _pilot(tmp_path, ROWS)
    _running_paper(tmp_path, pilot.control.app)
    st = pilot.state()
    st["active"] = {"market": "QQQ-USD", "config": "touch 1bp @ 10x", "mode": "paper", "since": time.time(),
                    "profile": "volume"}
    pilot.save(st)
    ev = pilot.review(scan_of(ROWS))                                    # loses a little, within budget: keep going
    assert pilot.state()["last_review"]["go"] is True and not pilot.state().get("paused_by_scout")
    assert [e["kind"] for e in ev] == ["suggest"] and "SPY-USD" in ev[0]["text"]   # 1.6x the volume, same list
    assert ev[0]["profile"] == "volume"                                  # its Run buttons open that list
    st = pilot.state()
    st["active"]["profile"] = "breakeven"                               # the same setup under the breakeven list
    pilot.save(st)
    ev = pilot.review(scan_of(ROWS))
    assert [e["kind"] for e in ev] == ["paused"] and "loses $0.65" in ev[0]["text"]


# ------------------------------------------------------------------------------------------------ Telegram
async def test_a_stale_list_asks_for_a_scan_and_posts_the_fresh_list(tmp_path: Path) -> None:
    from bot.scout.service import SCAN_NOW

    bot, api, _ = _pilot(tmp_path, ROWS)
    old = {**scan_of(ROWS), "ts_us": int((time.time() - 3 * 3600) * 1e6)}   # the last scan is 3 h old
    (tmp_path / "data" / "scout" / "latest.json").write_text(json.dumps(old))
    await bot.handle(msg("/aggressive"))
    text, kb = api.sent[-1][1], api.sent[-1][2]
    assert "⚡ <b>Aggressive Mid</b>" in text and "SPY · touch 1bp · 50x" in text and "$0.12/1k" in text
    assert (tmp_path / "state" / SCAN_NOW).exists() and "Scan started" in text
    assert kb[0][0] == ("▶️ 1", "pick aggressive 1") and ("🔄 Scan", "rescan aggressive") in kb[-1]
    n = len(api.sent)
    await bot.scan_waiters_tick()
    assert len(api.sent) == n                                            # the old scan does not count
    s = {**scan_of(ROWS), "ts_us": int((time.time() + 1) * 1e6)}        # a scan finishes
    (tmp_path / "data" / "scout" / "latest.json").write_text(json.dumps(s))
    await bot.scan_waiters_tick()
    assert len(api.sent) == n + 1 and "Aggressive Mid" in api.sent[-1][1] and bot.scan_waiters == []


async def test_a_list_pick_can_run_at_another_leverage_of_the_ladder(tmp_path: Path) -> None:
    from bot.scout.pilot import setting_id

    bot, api, pilot = _pilot(tmp_path, ROWS)
    calls: list[tuple[str, float, str]] = []

    async def fake_deploy(c: dict[str, Any], *, live: bool, by: str) -> str:
        calls.append((c["market"], c["leverage"], c["profile"]))
        return "ok"
    pilot.deploy = fake_deploy  # type: ignore[method-assign]
    sid = setting_id("touch 1bp")
    await bot.handle(press("pick volume 2"))                             # QQQ touch 1bp, the list's pick at 10x
    text, kb = api.edits[-1][2], api.edit_keyboards[-1]
    assert "25x" in text and "10x" in text and "⭐" in text
    assert kb[0] == [("25x max", f"rl QQQ-USD {sid} 25 volume"), ("10x", f"rl QQQ-USD {sid} 10 volume")]
    await bot.handle(press(f"rl QQQ-USD {sid} 25 volume"))              # also in the volume list at 25x
    assert "In 🔥 Volume" in api.edits[-1][2] and "Runs as 🔥 Volume" in api.edits[-1][2]
    await bot.handle(press(f"rd QQQ-USD {sid} 25 volume paper"))
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    await asyncio.sleep(0.05)
    assert calls == [("QQQ-USD", 25, "volume")]


async def test_maxvolume_command_lists_the_most_volume(tmp_path: Path) -> None:
    bot, api, _ = _pilot(tmp_path, ROWS)
    await bot.handle(msg("/maxvolume"))
    assert "🚀 <b>Max volume</b>" in api.sent[-1][1] and "NVDA · improve touch · 5x" in api.sent[-1][1]
    assert "≤$" not in api.sent[-1][1]                                   # no budget on this list


async def test_set_volume_cost_rescans(tmp_path: Path) -> None:
    from bot.scout.service import SCAN_NOW

    bot, api, _ = _pilot(tmp_path, ROWS)
    await bot.handle(msg("/set volume_cost 0.25"))
    pid = next(iter(bot.pending))
    await bot.handle(press(f"ok {pid}"))
    assert settings.load(tmp_path / "state")["volume_cost"] == 0.25 and (tmp_path / "state" / SCAN_NOW).exists()
    await bot.handle(msg("/top3 volume"))
    assert "NVDA · improve touch" in api.sent[-1][1]                     # $0.22 per $1,000 now fits


def test_menu_and_commands_have_the_lists() -> None:
    from bot.telegram.views import COMMANDS, HELP, menu_keyboard

    names = [n for n, _ in COMMANDS]
    assert {"volume", "aggressive", "maxvolume", "run"} <= set(names)
    assert all(f"/{n}" in HELP for n in ("volume", "aggressive", "maxvolume", "run"))
    buttons = {d for row in menu_keyboard() for _t, d in row}
    assert {"top3", "volume", "aggressive", "maxvolume", "run", "openpositions"} <= buttons
