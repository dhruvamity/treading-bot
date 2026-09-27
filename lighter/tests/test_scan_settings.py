import pytest

from lbot import settings
from lbot.scout.scan import Scanner, checks, menu


def row(market, setup, vol, cost, days=1, why=()):
    return {"market": market, "setup": setup, "volume_d": vol, "cost_1k": cost, "days": days, "why": list(why),
            "x_capital": vol / 100, "pnl_d": -cost * vol / 1000, "fills_d": 100}


def test_menu_is_complete():
    names = [s.name for s in menu()]
    assert len(names) == len(set(names)) == 36
    assert "Mid 0" in names and "Smart +1" in names and "Grid +2 Short" in names and "Touch +0.5" in names


def test_lists_budget_bias_and_one_per_market():
    t = [row("A", "Mid 0", 1e6, 0.05), row("A", "Mid +1", 5e5, 0.01), row("B", "Mid +1 Short", 2e6, 0.02),
         row("C", "Mid +2", 3e6, 0.5), row("D", "Smart +1", 4e5, -0.01, why=["hit the kill"])]
    ls = Scanner.lists(t, 0.10, 100)
    assert [r["market"] for r in ls["most"]] == ["A"]                     # B's bias needs 3 days; C over budget
    assert ls["most"][0]["setup"] == "Mid 0"
    assert [r["market"] for r in ls["max"]] == ["C", "A"]
    t[2]["days"] = 3
    assert [r["market"] for r in Scanner.lists(t, 0.10, 100)["most"]] == ["B", "A"]


def test_checks():
    a = {"days": 1, "killed": False, "liquidated": False, "fills_d": 3}
    assert checks(a, {"known": False}, 1) == ["under 5 fills a day"]
    assert "trending now" in checks({**a, "fills_d": 9}, {"known": True, "ok": False, "trend": 0.8, "age_s": 5}, 1)


def test_settings_validate(cfg):
    assert settings.parse("capital", "auto") == "auto"
    assert settings.parse("max_lev", "20x") == 20
    with pytest.raises(ValueError):
        settings.parse("daily_stop", "200")
    settings.save(cfg, "daily_stop", 6.0)
    assert settings.stops(cfg)[1] == 6.0
    with pytest.raises(ValueError):
        settings.save(cfg, "position_stop", 9.0)                           # above the daily stop
    settings.reset(cfg, "daily_stop")
    assert settings.stops(cfg)[1] == cfg.sizing.daily_stop_pct


def test_pilot_refuses_without_a_scan(cfg):
    from lbot.scout import pilot
    with pytest.raises(ValueError):
        pilot.pick(cfg, "most", 1)


def test_ops_never_spawns_under_tests(cfg):
    from lbot import ops
    with pytest.raises(RuntimeError):
        ops.start(cfg, "scout", ["scout", "run"])
