"""Telegram operator bot: runner-side controls end to end, and the bot against a fake Telegram API (no network)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from bot.common.config import AppConfig, load_app, load_arcus_config, load_lighter_config, load_session
from bot.common.secrets import SecretStore
from bot.core.calendar import TradingCalendar
from bot.core.heartbeat import write_heartbeat
from bot.core.livelock import RunMode
from bot.core.runner import BotRunner
from bot.core.state import StateStore
from bot.telegram.api import split_html
from bot.telegram.bot import TelegramBot
from bot.telegram.control import Control
from bot.telegram.watcher import Prefs, Watcher
from tests.unit.test_runner_offline import ROOT, FakeVenues

OWNER = 111
STRANGER = 999


# ------------------------------------------------------------------------------------------------ runner side
async def _runner(tmp_path: Path, srv: FakeVenues) -> tuple[BotRunner, AppConfig]:
    acfg, lcfg = load_arcus_config(ROOT / "config/venues/arcus.yaml"), load_lighter_config(ROOT / "config/venues/lighter_rh.yaml")
    acfg.rest.mainnet, acfg.ws.mainnet = f"http://{srv.url}", f"ws://{srv.url}/arcus-ws"
    lcfg.rest.mainnet, lcfg.ws.mainnet = f"http://{srv.url}", f"ws://{srv.url}/lighter-ws"
    app = load_app(ROOT / "config/app.yaml")
    app.data_dir, app.reports_dir, app.state_dir = str(tmp_path / "data"), str(tmp_path / "reports"), str(tmp_path / "state")
    runner = BotRunner([load_session(ROOT / "config/sessions/arcus_btc_mm.yaml")], mode=RunMode.PAPER, cli_live=False,
                       app=app, arcus_cfg=acfg, lighter_cfg=lcfg, secrets=SecretStore(tmp_path / "none.enc", password=""),
                       calendar=TradingCalendar.load(ROOT / "config/calendars"), state_db=str(tmp_path / "s.sqlite"))
    return runner, app


async def test_runner_publishes_status_and_honours_pause_and_stop(tmp_path: Path) -> None:
    srv = FakeVenues()
    await srv.start()
    runner, _ = await _runner(tmp_path, srv)
    runner.state.kv_set("paused", json.dumps({"*": "test"}))           # paused before it even starts

    async def stop_soon() -> None:
        await asyncio.sleep(8)
        runner.state.kv_set("control", json.dumps({"cmd": "stop", "by": "test"}))

    t0 = time.monotonic()
    task = asyncio.create_task(stop_soon())
    try:
        await runner.run(duration_s=40)
    finally:
        task.cancel()
        if srv.runner:
            await srv.runner.cleanup()
    assert time.monotonic() - t0 < 30, "stop flag ignored"
    snap = json.loads(runner.state.kv_get("status") or "{}")
    assert snap["mode"] == "paper" and snap["markets"] and snap["sessions"], snap
    assert snap["risk"]["operator_paused"] == ["BTC"]
    btc = next(m for m in snap["markets"] if m["market"] == "BTC")
    assert btc["quoting"] is False and "paused by operator" in btc["why"]
    assert not [o for o in runner.state.orders.values() if not o.req.reduce_only]   # paused: no new quotes at all
    kinds = {d["kind"] for d in runner.decisions.records}
    assert {"operator_pause", "operator_stop"} <= kinds, kinds


# ------------------------------------------------------------------------------------------------ fakes
class FakeAPI:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, str, Any]] = []
        self.edits: list[tuple[Any, int, str]] = []

    async def send(self, chat_id: Any, text: str, *, keyboard: Any = None, silent: bool = False) -> dict[str, Any]:
        self.sent.append((chat_id, text, keyboard))
        return {"message_id": len(self.sent)}

    async def edit(self, chat_id: Any, message_id: int, text: str, *, keyboard: Any = None) -> None:
        self.edits.append((chat_id, message_id, text))

    async def answer(self, callback_id: str, text: str = "") -> None:
        return None

    async def call(self, method: str, **kw: Any) -> Any:
        return {"username": "bot_test_bot"}

    async def set_commands(self, commands: Any) -> None:
        return None

    def texts(self) -> str:
        return "\n".join(t for _, t, _ in self.sent) + "\n".join(t for _, _, t in self.edits)


def _app(tmp_path: Path) -> AppConfig:
    app = AppConfig()
    app.state_dir, app.logs_dir, app.reports_dir = "state", "logs", "reports"
    (tmp_path / "state").mkdir()
    return app


def _running_paper(tmp_path: Path, app: AppConfig, snapshot: dict[str, Any] | None = None) -> None:
    """A paper bot 'running' in another process: fresh heartbeat with a live foreign pid, and a state DB."""
    hb = tmp_path / app.heartbeat_for("paper")
    write_heartbeat(hb, mode="paper")
    d = json.loads(hb.read_text())
    d["pid"] = os.getppid()
    hb.write_text(json.dumps(d))
    st = StateStore(tmp_path / app.state_db_for("paper"))
    snap = snapshot or {
        "ts_us": int(time.time() * 1e6), "mode": "paper", "started_us": int((time.time() - 3600) * 1e6),
        "markets": [{"venue": "arcus", "market": "AMD", "position": "0.04", "mark": "620", "net": "0.42",
                     "spread_capture": "0.50", "inventory_mtm": "-0.08", "fees": "0", "funding": "0",
                     "volume": "950", "maker_volume": "950", "fills": 38, "open_orders": 2, "quoting": True, "why": ""}],
        "sessions": [{"session": "arcus_amd_midagg", "market": "AMD", "venue": "arcus", "mode": "mid", "pnl": "0.42",
                      "day_pnl": "0.31", "capital": "35", "ticks": 3600, "actions": 900, "rejects": 0, "errors": 0}],
        "risk": {"all_stopped": None, "safe_mode": {}, "venue_stopped_day": {}, "operator_paused": []}, "budget": {}}
    st.kv_set("status", json.dumps(snap))
    st.close()


def _bot(tmp_path: Path, app: AppConfig, **kw: Any) -> tuple[TelegramBot, FakeAPI, Control]:
    api = FakeAPI()
    ctl = Control(app, root=tmp_path)
    bot = TelegramBot(api, ctl, owner_chat_id=OWNER, allowed_user_ids=set(),  # type: ignore[arg-type]
                      prefs_path=tmp_path / "state" / "prefs.json", **kw)
    return bot, api, ctl


def msg(text: str, chat: int = OWNER, user: int = OWNER) -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat}, "from": {"id": user, "username": "owner"}, "text": text}}


def press(data: str, chat: int = OWNER, user: int = OWNER) -> dict[str, Any]:
    return {"update_id": 2, "callback_query": {"id": "cb", "data": data, "from": {"id": user, "username": "owner"},
                                               "message": {"chat": {"id": chat}, "message_id": 7}}}


# ------------------------------------------------------------------------------------------------ bot
async def test_only_the_owner_is_served(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)
    await bot.handle(msg("/status", chat=STRANGER, user=STRANGER))
    await bot.handle(press("stop", chat=STRANGER, user=STRANGER))
    assert api.sent == [] and api.edits == []
    await bot.handle(msg("/whoami", chat=STRANGER, user=STRANGER))           # the one thing strangers get: own ids
    assert str(STRANGER) in api.sent[-1][1]


async def test_allowed_users_restrict_even_the_owner_chat(tmp_path: Path) -> None:
    app = _app(tmp_path)
    bot, api, _ = _bot(tmp_path, app)
    bot.allowed_user_ids = {42}
    await bot.handle(msg("/ping", chat=OWNER, user=OWNER))
    assert api.sent == []
    await bot.handle(msg("/ping", chat=OWNER, user=42))
    assert "pong" in api.sent[-1][1]


async def test_status_screen(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)
    await bot.handle(msg("/status"))
    text = api.sent[-1][1]
    assert "PAPER" in text and "running" in text and "+$0.31" in text and "AMD" in text, text
    await bot.handle(msg("/pnl"))
    assert "+$0.42" in api.sent[-1][1]


async def test_pause_and_unpause_write_the_runner_flag(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, _api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/pause AMD"))
    assert json.loads(ctl._kv_get("paper", "paused") or "{}") == {"AMD": "Telegram (owner)"}
    await bot.handle(msg("/pause"))
    assert set(json.loads(ctl._kv_get("paper", "paused") or "{}")) == {"AMD", "*"}
    await bot.handle(msg("/unpause AMD"))
    assert set(json.loads(ctl._kv_get("paper", "paused") or "{}")) == {"*"}
    await bot.handle(msg("/unpause"))
    assert not ctl._kv_get("paper", "paused")


async def test_stop_needs_the_confirm_button(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    bot._spawn = lambda coro: coro.close()  # type: ignore[method-assign]  # skip the SIGINT fallback timer
    await bot.handle(msg("/stop"))
    assert not ctl._kv_get("paper", "control")                           # nothing yet
    pid = next(iter(bot.pending))
    await bot.handle(press(f"ok {pid}", chat=STRANGER, user=STRANGER))   # a stranger's press does nothing
    assert not ctl._kv_get("paper", "control")
    await bot.handle(press(f"ok {pid}"))
    assert json.loads(ctl._kv_get("paper", "control") or "{}")["cmd"] == "stop"
    await bot.handle(press(f"ok {pid}"))                                 # a second press finds nothing pending
    assert "expired" in api.texts()


async def test_flatten_needs_the_typed_code_and_a_real_account(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/flatten"))
    assert "real account" in api.texts() and not bot.pending          # paper has no venue positions to flatten
    calls: list[tuple[str, str, bool]] = []

    async def fake_flatten(mode: str, venue: str, taker: bool) -> str:
        calls.append((mode, venue, taker))
        return "sent 1 reduce-only maker orders on arcus"

    ctl.flatten = fake_flatten  # type: ignore[method-assign]
    await bot.handle(msg("/flatten arcus live"))
    p = next(iter(bot.pending.values()))
    assert p.code and p.code in api.sent[-1][1]
    await bot.handle(press(f"ok {p.pid}"))                               # a button is not enough
    assert calls == [] and "code" in api.texts()
    await bot.handle(msg("000000" if p.code != "000000" else "111111"))  # wrong code
    assert calls == []
    await bot.handle(msg(p.code, chat=STRANGER, user=STRANGER))          # right code, wrong chat
    assert calls == []
    await bot.handle(msg(p.code))
    await asyncio.sleep(0.05)
    assert calls == [("live", "arcus", False)]


async def test_expired_confirmations_do_nothing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/stop"))
    p = next(iter(bot.pending.values()))
    p.expires = time.time() - 1
    await bot.handle(press(f"ok {p.pid}"))
    assert not ctl._kv_get("paper", "control") and "expired" in api.texts()


async def test_read_only_refuses_controls(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app, read_only=True)
    await bot.handle(msg("/pause"))
    assert "read-only" in api.texts() and not ctl._kv_get("paper", "paused")
    await bot.handle(msg("/status"))
    assert "PAPER" in api.sent[-1][1]


async def test_live_run_needs_live_enabled(tmp_path: Path) -> None:
    app = _app(tmp_path)
    (tmp_path / "config" / "sessions").mkdir(parents=True)
    (tmp_path / "config" / "sessions" / "amd.yaml").write_text(
        "session_id: amd\nvenue: arcus\naccount_index: 0\nmarket: AMD\nmode: mid\nlive_enabled: false\n")
    bot, api, _ = _bot(tmp_path, app)
    await bot.handle(msg("/run amd live"))
    assert "live_enabled" in api.sent[-1][1] and not bot.pending


# ------------------------------------------------------------------------------------------------ watcher
async def test_watcher_alerts(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    ctl = Control(app, root=tmp_path)
    got: list[tuple[str, bool]] = []

    async def notify(text: str, critical: bool) -> None:
        got.append((text, critical))

    w = Watcher(ctl, notify, Prefs(fills="each"), daily_loss_pct=3.0)
    await w.tick()                                   # first look: no alerts, no history replay
    assert got == []
    st = StateStore(tmp_path / app.state_db_for("paper"))
    from decimal import Decimal

    from bot.venues.base import Fill, Side, Venue
    st.on_fill(Fill(Venue.ARCUS, "AMD", "c1", Side.BUY, Decimal("620"), Decimal("0.04"), Decimal(0), True, False,
                    int(time.time() * 1e6), "t1"))
    snap = json.loads(st.kv_get("status") or "{}")
    snap["sessions"][0]["day_pnl"] = "-0.70"         # past half of the limit (3% of $35 = $1.05), not all of it
    st.kv_set("status", json.dumps(snap))
    st.close()
    await w.tick()
    texts = "\n".join(t for t, _ in got)
    assert "AMD BUY" in texts and "half of the daily loss limit" in texts, texts
    await w.tick()
    assert sum("half of the daily loss limit" in t for t, _ in got) == 1   # once per day
    (tmp_path / app.heartbeat_for("paper")).unlink()                      # the bot dies
    await w.tick()
    assert any("DOWN" in t and crit for t, crit in got)


def test_split_html_keeps_pre_balanced() -> None:
    text = "<b>x</b>\n<pre>" + "\n".join(f"line {i}" for i in range(2000)) + "</pre>"
    chunks = split_html(text, limit=500)
    assert len(chunks) > 5
    for c in chunks:
        assert c.count("<pre>") == c.count("</pre>") and len(c) <= 512


# ------------------------------------------------------------------------------------------------ pilot
def _cand(market: str, config: str, go: bool = True, vol: float = 2000.0, reasons: list[str] | None = None
          ) -> dict[str, Any]:
    return {"market": market, "config": config, "go": go, "reasons": reasons or ([] if go else ["trending now"]),
            "days": 3, "fills_day": 80, "volume_day": vol, "pnl_day": 0.2, "worst_day": -0.1, "day_stops": 0,
            "positive_days": 3, "recent_pnl": 0.1, "recent_fills": 70, "recent_volume": 1800, "tail_pnl": 0.0,
            "taker_day": 0, "actions_per_usd": 5, "now": {}}


def _scan(top: list[dict[str, Any]], extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    allc = top + (extra or [])
    return {"ts_us": int(time.time() * 1e6), "took_s": 1, "risk": {}, "markets": 20, "configs": 18,
            "top": [c for c in top if c["go"]][:3], "ranked": allc, "all": allc}


def test_pilot_session_file_is_a_valid_session(tmp_path: Path) -> None:
    from bot.scout.pilot import session_for
    from bot.scout.scan import MENU
    from bot.scout.sim import Risk

    for cfg in MENU:
        s = session_for("NVDA-USD", cfg, Risk(), live=False)
        p = tmp_path / "pilot.yaml"
        import yaml

        p.write_text(yaml.safe_dump(s))
        sess = load_session(p)
        assert sess.market == "NVDA" and sess.daily_stop_usd == 2 and sess.pos_stop_usd == 1  # type: ignore[union-attr]
        assert sess.kill_usd == 10 and not sess.live_enabled  # type: ignore[union-attr]


async def test_pilot_offers_pauses_resumes_and_suggests(tmp_path: Path) -> None:
    from bot.scout.pilot import Pilot

    app = _app(tmp_path)
    ctl = Control(app, root=tmp_path)
    pilot = Pilot(tmp_path, ctl)
    nvda, gld = _cand("NVDA-USD", "deep 3bp"), _cand("GLD-USD", "deep 2bp x2", vol=1500)
    ev = pilot.review(_scan([nvda, gld]))
    assert [e["kind"] for e in ev] == ["offer"] and len(ev[0]["top"]) == 2
    assert pilot.review(_scan([nvda, gld])) == []                      # same top: no repeat
    # deployed on NVDA (paper bot running)
    _running_paper(tmp_path, app)
    st = pilot.state()
    st["active"] = {"market": "NVDA-USD", "config": "deep 3bp", "mode": "paper", "since": time.time()}
    pilot.save(st)
    bad = _cand("NVDA-USD", "deep 3bp", go=False)
    ev = pilot.review(_scan([gld], [bad]))
    assert [e["kind"] for e in ev] == ["paused"] and "trending now" in ev[0]["text"]
    assert ctl.view("paper").paused.get("NVDA", "").startswith("scout: trending now")
    assert pilot.review(_scan([nvda, gld])) == []                      # one GO scan is not enough
    ev = pilot.review(_scan([nvda, gld]))
    assert [e["kind"] for e in ev] == ["resumed"] and "NVDA" not in ctl.view("paper").paused
    big = _cand("HOOD-USD", "deep 2bp", vol=5000)
    ev = pilot.review(_scan([big, nvda]))
    assert [e["kind"] for e in ev] == ["suggest"] and "HOOD-USD" in ev[0]["text"]
    assert pilot.review(_scan([big, nvda])) == []                      # suggested once


async def test_scout_run_button_needs_confirm_and_deploys_paper(tmp_path: Path) -> None:
    from bot.scout.pilot import Pilot

    app = _app(tmp_path)
    bot, api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    bot.pilot = pilot
    (tmp_path / "data" / "scout").mkdir(parents=True)
    (tmp_path / "data" / "scout" / "latest.json").write_text(json.dumps(_scan([_cand("NVDA-USD", "deep 3bp")])))
    await bot.handle(msg("/scout"))
    assert "NVDA-USD" in api.texts() and api.sent[-1][2][0][0] == ("Run #1", "pick 1")
    await bot.handle(press("pick 1"))
    assert "Paper" in str(api.edits[-1]) or "deploy 1 paper" in str(api.sent[-1][2])
    calls: list[tuple[int, bool]] = []

    async def fake_approve(k: int, *, live: bool, by: str) -> str:
        calls.append((k, live))
        return "ok"
    pilot.approve = fake_approve  # type: ignore[method-assign]
    await bot.handle(press("deploy 1 paper"))
    assert calls == []                                                  # a confirm is still needed
    pid = next(iter(bot.pending))
    await bot.handle(press(f"ok {pid}"))
    await asyncio.sleep(0.05)
    assert calls == [(1, False)]
    await bot.handle(press("deploy 1 live"))
    assert "Live is off" in api.texts()                                 # BOT_PILOT_LIVE not set


# ------------------------------------------------------------------------------------------------ command names
async def test_the_renamed_commands_work_and_the_old_names_still_do(tmp_path: Path) -> None:
    import re

    from bot.telegram.bot import ALIASES
    from bot.telegram.views import COMMANDS, HELP, menu_keyboard

    names = [n for n, _ in COMMANDS]
    assert {"top3", "openpositions", "yesterdayreport", "pauseneworders", "resumeaftersl", "closeall"} <= set(names)
    assert not set(ALIASES) & set(names)                     # old names work but are not in Telegram's menu
    assert all(re.fullmatch(r"[a-z0-9_]{1,32}", n) for n in names)   # Telegram's rule for command names
    for old in ALIASES:
        assert f"/{old} " not in HELP and f"/{old}\n" not in HELP
    buttons = {data.split()[0] for row in menu_keyboard() for _, data in row}
    assert not buttons & set(ALIASES)                        # the menu buttons use the new names

    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/pauseneworders AMD"))
    assert json.loads(ctl._kv_get("paper", "paused") or "{}") == {"AMD": "Telegram (owner)"}
    await bot.handle(msg("/pause"))                          # the old name: same command
    assert set(json.loads(ctl._kv_get("paper", "paused") or "{}")) == {"AMD", "*"}
    await bot.handle(msg("/yesterdayreport"))
    assert "report" in api.sent[-1][1].lower()
    await bot.handle(msg("/closeall"))
    assert "real account" in api.sent[-1][1]                 # paper: close-all is refused, as flatten was
