"""Telegram operator bot: runner-side controls end to end, and the bot against a fake Telegram API (no network)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from bot.common import settings
from bot.common.config import AppConfig, SizingDefaults, load_app, load_arcus_config, load_session
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
    acfg = load_arcus_config(ROOT / "config/venues/arcus.yaml")
    acfg.rest.mainnet, acfg.ws.mainnet = f"http://{srv.url}", f"ws://{srv.url}/arcus-ws"
    app = load_app(ROOT / "config/app.yaml")
    app.data_dir, app.reports_dir, app.state_dir = str(tmp_path / "data"), str(tmp_path / "reports"), str(tmp_path / "state")
    runner = BotRunner([load_session(ROOT / "config/sessions/arcus_btc_mm.yaml")], mode=RunMode.PAPER, cli_live=False,
                       app=app, arcus_cfg=acfg, secrets=SecretStore(tmp_path / "none.enc", password=""),
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
    acct = snap["account"]["arcus"]            # the account reading the dashboard shows (paper: its start money)
    assert acct["equity"] > 0 and acct["net_deposits"] > 0 and acct["ts_us"] > 0, acct
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
        self.edit_keyboards: list[Any] = []
        self.last_keyboard: Any = None       # the keyboard of whatever was sent or edited last

    async def send(self, chat_id: Any, text: str, *, keyboard: Any = None, silent: bool = False) -> dict[str, Any]:
        self.sent.append((chat_id, text, keyboard))
        self.last_keyboard = keyboard
        return {"message_id": len(self.sent)}

    async def edit(self, chat_id: Any, message_id: int, text: str, *, keyboard: Any = None) -> None:
        self.edits.append((chat_id, message_id, text))
        self.edit_keyboards.append(keyboard)
        self.last_keyboard = keyboard

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
    assert "🏓 <b>PONG</b>" in api.sent[-1][1]


async def test_status_screen(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, _ = _bot(tmp_path, app)
    await bot.handle(msg("/status"))
    text = api.sent[-1][1]
    assert text.startswith("🟢 <b>PAPER · HEALTHY</b>") and "+$0.31" in text and "AMD" in text, text
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
    assert "EXPIRED" in api.texts()


async def test_flatten_needs_the_typed_code_and_a_real_account(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/flatten"))
    assert "real account" in api.texts() and not bot.pending          # paper has no venue positions to flatten
    calls: list[tuple[str, str, bool]] = []

    async def fake_flatten(mode: str, venue: str, taker: bool) -> dict[str, Any]:
        calls.append((mode, venue, taker))
        return {"venue": venue, "positions": 1, "orders": 1, "open": 1 if len(calls) == 1 else 0, "taker": taker}

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
    assert api.sent[-1][1].startswith("🧯 <b>CLOSING POSITIONS</b>") and "Closed: 0 · Open: 1" in api.sent[-1][1]
    await bot.handle(msg("/closeall arcus live"))            # the owner's template once nothing is left open
    await bot.handle(msg(next(iter(bot.pending.values())).code))
    await asyncio.sleep(0.05)
    assert api.sent[-1][1] == "✅ <b>POSITIONS CLOSED</b>\n\n<code>Closed: 1 · Open: 0</code>"


async def test_expired_confirmations_do_nothing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/stop"))
    p = next(iter(bot.pending.values()))
    p.expires = time.time() - 1
    await bot.handle(press(f"ok {p.pid}"))
    assert not ctl._kv_get("paper", "control") and "EXPIRED" in api.texts()


async def test_read_only_refuses_controls(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app, read_only=True)
    await bot.handle(msg("/pause"))
    assert "READ-ONLY" in api.texts() and not ctl._kv_get("paper", "paused")
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
    assert "AMD BUY" in texts and "HALF THE DAY STOP" in texts, texts
    await w.tick()
    assert sum("HALF THE DAY STOP" in t for t, _ in got) == 1   # once per day
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
def _cand(market: str, setting: str, go: bool = True, vol: float = 2000.0, reasons: list[str] | None = None,
          lev: float = 10.0, at_max: bool = False) -> dict[str, Any]:
    return {"market": market, "config": f"{setting} @ {lev:g}x", "setting": setting, "leverage": lev,
            "leverage_off": lev, "at_max": at_max, "go": go, "reasons": reasons or ([] if go else ["trending now"]),
            "money_reasons": [], "days": 3, "fills_day": 80, "volume_day": vol, "pnl_day": 0.2, "worst_day": -0.1,
            "day_stops": 0, "positive_days": 3, "recent_pnl": 0.1, "recent_fills": 70, "recent_volume": 1800,
            "tail_pnl": 0.0, "taker_day": 0, "actions_per_usd": 5, "now": {}, "order_usd": 11.2 * lev,
            "cap_usd": 22.4 * lev, "cap_off_usd": 22.4 * lev, "used_usd": 28, "capital_usd": 28, "cost_1k": 0.0}


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
        want = Risk().with_stops(cfg.stops)                    # the Risk's dollar stops, or the setting's own
        assert sess.market == "NVDA" and sess.daily_stop_usd == want.daily_stop_usd  # type: ignore[union-attr]
        assert sess.pos_stop_usd == want.pos_stop_usd and sess.kill_usd == want.kill_usd  # type: ignore[union-attr]
        assert not sess.live_enabled  # type: ignore[union-attr]


async def test_pilot_offers_pauses_resumes_and_suggests(tmp_path: Path) -> None:
    from bot.scout.pilot import Pilot

    app = _app(tmp_path)
    ctl = Control(app, root=tmp_path)
    pilot = Pilot(tmp_path, ctl)
    nvda, gld = _cand("NVDA-USD", "Mid +3"), _cand("GLD-USD", "Mid +2", vol=1500)
    ev = pilot.review(_scan([nvda, gld]))
    assert [e["kind"] for e in ev] == ["offer"] and len(ev[0]["top"]) == 2
    assert pilot.review(_scan([nvda, gld])) == []                      # same top: no repeat
    # deployed on NVDA (paper bot running)
    _running_paper(tmp_path, app)
    st = pilot.state()
    st["active"] = {"market": "NVDA-USD", "config": "Mid +3 @ 10x", "mode": "paper", "since": time.time()}
    pilot.save(st)
    bad = _cand("NVDA-USD", "Mid +3", go=False)
    ev = pilot.review(_scan([gld], [bad]))
    assert [e["kind"] for e in ev] == ["paused"] and "trending now" in ev[0]["text"]
    assert ctl.view("paper").paused.get("NVDA", "").startswith("scout: trending now")
    assert pilot.review(_scan([nvda, gld])) == []                      # one GO scan is not enough
    ev = pilot.review(_scan([nvda, gld]))
    assert [e["kind"] for e in ev] == ["resumed"] and "NVDA" not in ctl.view("paper").paused
    big = _cand("HOOD-USD", "Mid +2", vol=5000)
    ev = pilot.review(_scan([big, nvda]))
    assert [e["kind"] for e in ev] == ["suggest"] and "HOOD-USD" in ev[0]["text"]
    assert pilot.review(_scan([big, nvda])) == []                      # suggested once


def _with_pilot(tmp_path: Path, rows: list[dict[str, Any]], top: list[dict[str, Any]] | None = None
                ) -> tuple[Any, Any, Any, list[tuple[str, str, float, bool, str]]]:
    """A bot with a pilot on a scan of `rows`; pilot.deploy records (market, setting, leverage, live, profile)."""
    from bot.scout.pilot import Pilot

    app = _app(tmp_path)
    bot, api, ctl = _bot(tmp_path, app)
    pilot = Pilot(tmp_path, ctl)
    bot.pilot = pilot
    (tmp_path / "data" / "scout").mkdir(parents=True, exist_ok=True)
    top = rows if top is None else top
    scan = _scan(top, [r for r in rows if r not in top])
    (tmp_path / "data" / "scout" / "latest.json").write_text(json.dumps(scan))
    calls: list[tuple[str, str, float, bool, str]] = []

    async def fake_deploy(c: dict[str, Any], *, live: bool, by: str) -> str:
        calls.append((c["market"], c["setting"], c["leverage"], live, c["profile"]))
        return "ok"
    pilot.deploy = fake_deploy  # type: ignore[method-assign]
    return bot, api, pilot, calls


def _buttons(api: Any) -> list[str]:
    """Every button's data on the last message (sent or edited)."""
    return [d for row in api.last_keyboard or [] for _t, d in row]


async def test_a_list_pick_opens_the_run_form_then_deploys_paper(tmp_path: Path) -> None:
    rows = [_cand("NVDA-USD", "Mid +3", lev=20, at_max=True, go=False, reasons=["loses $1.00 (3.57% of $28)/day"]),
            _cand("NVDA-USD", "Mid +3", lev=10), _cand("NVDA-USD", "Mid +3", lev=5, vol=900)]
    bot, api, _pilot, calls = _with_pilot(tmp_path, rows, top=[rows[1]])
    await bot.handle(msg("/scout"))
    assert "<b>1. NVDA</b>\n<code>Mid +3 · Neutral · 10x" in api.sent[-1][1] and \
        api.sent[-1][2][0][0] == ("▶️ 1", "pick volume 1")
    await bot.handle(press("pick 1"))                                   # a button from before the lists still works
    text = api.texts()
    assert "<code>NVDA · Mid +3 · Neutral · 10x</code>" in text and "From Most Volume" in text
    assert {"f NVDA-USD m+3n 20 0 0 0 volume", "f NVDA-USD m+3n 5 0 0 0 volume"} <= set(_buttons(api))
    await bot.handle(press("f NVDA-USD m+3n 20 0 0 0 volume"))          # not in the list at 20x: your own pick
    assert "In no list: loses $1.00" in api.texts() and "Runs as your pick" in api.texts()
    await bot.handle(press("f NVDA-USD m+3n 10 0 0 0 volume"))
    assert "In Most Volume, Cheapest, Max Volume" in api.texts()
    assert "fd NVDA-USD m+3n 10 0 0 0 volume paper" in _buttons(api)
    await bot.handle(press("fd NVDA-USD m+3n 10 0 0 0 volume paper"))
    assert calls == []                                                  # a confirm is still needed
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    await asyncio.sleep(0.05)
    assert calls == [("NVDA-USD", "Mid +3", 10, False, "volume")]
    await bot.handle(press("fd NVDA-USD m+3n 10 0 0 0 volume live"))
    assert "LIVE is off" in api.texts()                                 # BOT_PILOT_LIVE not set


async def test_the_run_form_changes_one_field_at_a_time(tmp_path: Path) -> None:
    """Tread's order form as buttons: mode, spread, bias, leverage, run stop and volume target, each one tap."""
    rows = [_cand("BTC-USD", "Mid 0", lev=20, at_max=True, vol=3000),
            _cand("BTC-USD", "Grid +1 Long", lev=20, at_max=True)]
    bot, api, _pilot, calls = _with_pilot(tmp_path, rows, top=[])
    (tmp_path / "data" / "scout" / "markets.json").write_text(json.dumps({"markets": [
        {"marketDisplayName": "BTC-USD", "status": "ONLINE", "initialMarginFraction": "0.05", "tickSize": "0.1",
         "stepSize": "0.0001", "minOrderNotional": "5", "minOrderSize": "0.0001", "markPrice": "86000"}]}))
    (tmp_path / "state" / "scout_capital.json").write_text(json.dumps({"usd": 28}))
    await bot.handle(msg("/run BTC"))                                   # opens with the market's best setup
    text, kb = api.sent[-1][1], api.sent[-1][2]
    assert text.startswith("🎛 <b>BTC · RUN SETUP</b>\n\n<code>BTC · Mid 0 · Neutral · 20x</code>")
    assert "Quotes the best bid and ask, following the mid" in text
    labels = [[t for t, _d in row] for row in kb]
    assert labels[0] == ["• Mid", "Grid"] and labels[1] == ["-1 bp", "• 0 bp", "+1 bp", "+2 bp", "+3 bp", "+5 bp"]
    assert labels[2] == ["Short", "• Neutral", "Long"] and labels[4][0] == "• No SL" and labels[5][0] == "• No target"
    data = {t: d for row in kb for t, d in row}
    await bot.handle(press(data["Grid"]))                               # Grid keeps the spread (never below 0)
    data = {t: d for row in api.edit_keyboards[-1] for t, d in row}
    assert "Sells no lower than the last buy" in api.edits[-1][2] and "• 0 bp" in data and "-1 bp" not in data
    await bot.handle(press(data["+1 bp"]))
    data = {t: d for row in api.edit_keyboards[-1] for t, d in row}
    await bot.handle(press(data["Long"]))
    text = api.edits[-1][2]
    assert "<code>BTC · Grid +1 · Long · 20x</code>" in text and "Long bias: holds about half the position cap long" \
        in text and "In Most Volume" in text                           # backtested: its numbers show
    data = {t: d for row in api.edit_keyboards[-1] for t, d in row}
    await bot.handle(press(data["SL $10"]))
    data = {t: d for row in api.edit_keyboards[-1] for t, d in row}
    await bot.handle(press(data["Vol $100.0k"]))
    text = api.edits[-1][2]
    assert "<code>Run stop -$10.00</code>\n<code>Volume target $100.0k</code>" in text
    data = {t: d for row in api.edit_keyboards[-1] for t, d in row}
    assert data["📝 Paper"] == "fd BTC-USD g+1l 20 10 100 0 manual paper" and len(data["📝 Paper"]) <= 64
    await bot.handle(press(data["📝 Paper"]))
    assert "<code>Run stop: -$10.00</code>" in api.texts() and "<code>Volume target: $100.0k</code>" in api.texts()
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    await asyncio.sleep(0.05)
    assert calls == [("BTC-USD", "Grid +1 Long", 20, False, "manual")]


async def test_run_any_market_setting_and_leverage_from_the_command(tmp_path: Path) -> None:
    rows = [_cand("BTC-USD", "Mid 0", lev=20, at_max=True, go=False, vol=12000,
                  reasons=["loses $1.51 (5.38% of $28)/day over 5 days"]),
            _cand("BTC-USD", "Mid 0", lev=10, go=False), _cand("QQQ-USD", "Mid +1", lev=25, at_max=True)]
    bot, api, _pilot, calls = _with_pilot(tmp_path, rows, top=[])
    await bot.handle(msg("/run"))
    assert any(d == "rm BTC-USD" for d in _buttons(api))                # markets, the most volume first
    await bot.handle(msg("/run btc"))
    assert any(d.startswith("f BTC-USD ") for d in _buttons(api))       # its run form
    await bot.handle(msg("/run BTC Mid 0 max"))
    assert "BTC · Mid 0 · Neutral · 20x" in api.texts() and "In no list" in api.texts()
    await bot.handle(msg('/run BTC "Mid 0" max paper'))
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    await asyncio.sleep(0.05)
    assert calls == [("BTC-USD", "Mid 0", 20, False, "manual")]    # runs as the owner's pick, never paused
    await bot.handle(msg("/run BTC touch 0bp 20x paper"))            # the name from before 2026-09-26: Mid 0
    await bot.handle(press(f"ok {list(bot.pending)[-1]}"))
    await asyncio.sleep(0.05)
    assert calls[-1] == ("BTC-USD", "Mid 0", 20, False, "manual")
    for bad, why in (("/run DOGE", "Unknown market"), ("/run BTC rgrid 5", "was retired"),
                     ("/run BTC mid", "Mid needs a spread"), ("/run BTC grid -1", "cannot be negative"),
                     ("/run BTC 20x live", "Which setup?"),
                     ("/run BTC Mid 0 7x", "no backtest at 7x (has 20x, 10x)")):   # no market list: scan rows only
        await bot.handle(msg(bad))
        assert why in api.texts(), bad
    # 2026-09-26: a direct run waited hours for a scan at the new capital. With the market list it runs any leverage
    # up to the Arcus maximum, sized for the capital the scout uses now (a deposit moves it at once)
    (tmp_path / "data" / "scout" / "markets.json").write_text(json.dumps({"markets": [
        {"marketDisplayName": "BTC-USD", "status": "ONLINE", "initialMarginFraction": "0.025", "tickSize": "0.1",
         "stepSize": "0.0001", "minOrderNotional": "5", "minOrderSize": "0.0001", "markPrice": "86000"}]}))
    (tmp_path / "state" / "scout_capital.json").write_text(json.dumps({"usd": 100}))
    settings.save(tmp_path / "state", "crypto_lev", 20.0, SizingDefaults())   # caps the scan, not the owner's pick
    await bot.handle(msg("/run BTC Mid 0 60x"))
    assert "Arcus allows at most 40x" in api.texts()
    await bot.handle(msg("/run BTC Mid 0 40x"))
    assert "No backtest at 40x" in api.texts() and "Max position $4,000" in api.texts()
    await bot.handle(msg("/run BTC Mid 0 20x"))
    assert "Backtest · 3d at $28" in api.texts() and "Max position $2,000" in api.texts()
    await bot.handle(msg("/run BTC mid +0.5 long 40x"))              # any spread: runs, not in the scout's menu
    assert "BTC · Mid +0.5 · Long · 40x" in api.texts() and "Not in the scout" in api.texts()
    await bot.handle(msg("/run BTC Mid 0 40x paper"))
    await bot.handle(press(f"ok {list(bot.pending)[-1]}"))
    await asyncio.sleep(0.05)
    assert calls[-1] == ("BTC-USD", "Mid 0", 40, False, "manual")
    await bot.handle(msg("/run BTC grid 3 short 20x paper"))
    await bot.handle(press(f"ok {list(bot.pending)[-1]}"))
    await asyncio.sleep(0.05)
    assert calls[-1] == ("BTC-USD", "Grid +3 Short", 20, False, "manual")


async def test_run_with_a_loss_limit_replaces_the_running_live_bot(tmp_path: Path, monkeypatch: Any) -> None:
    """2026-09-26: /run while a live bot sat on its daily stop failed the "already running" check; and the owner
    wants to set the most a run may lose (sl=30)."""
    rows = [_cand("BTC-USD", "Mid 0", lev=20, go=False)]
    bot, api, pilot, _calls = _with_pilot(tmp_path, rows, top=[])
    st = pilot.state()
    st["active"] = {"market": "QQQ-USD", "config": "Mid +1 @ 20x", "mode": "live", "since": time.time()}
    pilot.save(st)
    monkeypatch.setenv("BOT_PILOT_LIVE", "1")
    pilot.control.is_running = lambda mode: mode == "live"  # type: ignore[method-assign]
    written: list[Path | None] = []
    pilot.write_session = lambda c, live, path=None: written.append(path) or tmp_path / "x.yaml"  # type: ignore[method-assign,assignment,func-returns-value]
    checked: list[bool] = []

    async def doctor(name: str, *, replacing: bool = False) -> tuple[bool, str]:
        checked.append(replacing)
        return True, "all good"
    pilot.control.doctor = doctor  # type: ignore[method-assign]
    deployed: list[dict[str, Any]] = []

    async def deploy(c: dict[str, Any], *, live: bool, by: str) -> str:
        deployed.append(c)
        return "ok"
    pilot.deploy = deploy  # type: ignore[method-assign]
    for bad, why in (("/run BTC Mid 0 20x live sl=abc", "sl= takes dollars"),
                     ("/run BTC Mid 0 20x live sl=0", "between $0.50"), ("/run BTC Mid 0 live vol=10", "vol= must be")):
        await bot.handle(msg(bad))
        assert why in api.texts(), bad
    await bot.handle(msg("/run BTC Mid 0 20x sl=30"))                  # no paper/live: the form, the limit set
    assert "<code>Run stop -$30.00</code>" in api.texts() and "f BTC-USD m0n 20 30 0 0 manual" in _buttons(api)
    await bot.handle(msg("/run BTC Mid 0 20x live sl=$30 vol=100k tp=5"))
    await asyncio.sleep(0.05)
    assert checked == [True] and written == [tmp_path / "state" / "pilot_check.yaml"]   # pilot.yaml left alone
    text = api.sent[-1][1]
    assert text.startswith("🔴 <b>LIVE RUN</b>\n\n<code>BTC · Mid 0 · Neutral · 20x</code>")   # the owner's template
    assert "<code>Run stop: -$30.00</code>\n<code>Take profit: +$5.00</code>\n<code>Volume target: $100.0k</code>" \
        in text and "Replaces the running LIVE bot (QQQ" in text
    assert "<b>Confirm within 2 min</b>" in text
    p = next(iter(bot.pending.values()))
    await bot.handle(msg(p.code))
    await asyncio.sleep(0.05)
    assert deployed and deployed[0]["market"] == "BTC-USD"
    assert (deployed[0]["max_loss_usd"], deployed[0]["take_profit_usd"], deployed[0]["volume_target_usd"]) == \
        (30, 5, 100_000)


async def test_a_new_run_clears_an_old_pause_and_waits_for_a_closing_bot(tmp_path: Path, monkeypatch: Any) -> None:
    """2026-09-26: /run BTC ... sl=10 started but quoted 0% for two hours behind an "all markets" pause from an
    earlier /pauseneworders ("paused by you 100%"); /resumeaftersl did not help. And a /run while the SPY bot was
    closing failed "already running": Telegram only looked at the heartbeat (15 s), the doctor at the process."""
    import bot.scout.pilot as pilot_mod
    from bot import ops

    rows = [_cand("BTC-USD", "Mid 0", lev=20, go=False)]
    bot, api, pilot, _calls = _with_pilot(tmp_path, rows, top=[])
    ctl = pilot.control
    ctl.set_pause("live", None, "Telegram (owner)")
    ctl.set_pause("live", "QQQ", "scout: trending now")
    monkeypatch.setenv("BOT_PILOT_LIVE", "1")
    closing = [True, True, False]           # the old bot: shutting down (no heartbeat), then gone
    started: list[bool] = []
    monkeypatch.setattr(ctl, "is_running", lambda mode: bool(started))
    monkeypatch.setattr(ctl, "alive", lambda mode: bool(started) or (closing.pop(0) if closing else False))
    monkeypatch.setattr(ctl, "start_run", lambda name, live: started.append(True) or {"pid": 1, "log": "x.log"})
    guardians: list[str] = []   # the real ops.start spawned a real guardian from the repo (2026-09-26)
    monkeypatch.setattr(ops, "start", lambda app, name: guardians.append(name) or (True, "started"))
    pilot.write_session = lambda c, live, path=None: tmp_path / "x.yaml"  # type: ignore[method-assign,assignment]
    checked: list[bool] = []

    async def doctor(name: str, *, replacing: bool = False) -> tuple[bool, str]:
        checked.append(replacing)
        return True, "all good"
    monkeypatch.setattr(ctl, "doctor", doctor)
    await bot.handle(msg("/run BTC Mid 0 20x live sl=10"))
    await asyncio.sleep(0.05)
    text = api.sent[-1][1]
    assert checked == [True] and "Starts once the LIVE bot that is stopping has exited" in text   # still exiting
    assert "Clears your pause on new orders (all markets, QQQ)" in text
    real_sleep = asyncio.sleep

    async def fast(_s: float) -> None:
        await real_sleep(0)
    monkeypatch.setattr(pilot_mod.asyncio, "sleep", fast)
    c = {**bot._find("BTC-USD", "Mid 0", 20.0, "manual"), "max_loss_usd": 10.0}
    assert await pilot_mod.Pilot.deploy(pilot, c, live=True, by="test") == "running in live"
    assert not closing and started == [True]                                 # waited for the old process first
    assert guardians == ["guardian"]                                         # a live run brings its watchdog
    assert ctl.paused("live") == {}
    ev, _ = pilot.events_since(0)
    assert any("Cleared the pause on new orders (all markets, QQQ)" in e["text"] for e in ev)


async def test_resume_after_a_stop_says_when_the_owner_pause_still_holds(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    ctl.set_pause("paper", None, "Telegram (owner)")
    await bot.handle(msg("/resumeaftersl"))
    await bot.handle(press(f"ok {next(iter(bot.pending))}"))
    assert "New orders paused by you: all markets" in api.texts() and "unpause all paper" in _buttons(api)
    from bot.telegram.views import mode_state

    assert mode_state(ctl.view("paper")) == ("⏸", "PAUSED", ["New orders paused by you: all markets", "/unpause"])
    ctl.clear_pause("paper", None)
    ctl.set_pause("paper", "QQQ", "scout: x")        # another market's pause does not hold an AMD bot
    assert mode_state(ctl.view("paper"))[1] == "RUNNING"


def test_a_stopped_heartbeat_is_a_stop_not_a_crash(tmp_path: Path) -> None:
    app = _app(tmp_path)
    ctl = Control(app, root=tmp_path)
    hb = tmp_path / app.heartbeat_for("live")
    write_heartbeat(hb, mode="live", stopped="quotes cancelled, bot stopped")
    d = json.loads(hb.read_text())
    d["pid"], d["ts_us"] = os.getppid(), d["ts_us"] - 20_000_000       # 20 s old, the process still exiting
    hb.write_text(json.dumps(d))
    v = ctl.view("live")
    assert v.stopped and not v.running and not ctl.is_running("live") and ctl.alive("live")
    ctl.request_close("live", "test")
    assert ctl.stop_asked["live"] > 0


async def test_a_paper_deployment_goes_live_with_the_same_setup(tmp_path: Path, monkeypatch: Any) -> None:
    rows = [_cand("QQQ-USD", "Mid +1", lev=20)]
    bot, api, pilot, calls = _with_pilot(tmp_path, rows)
    _running_paper(tmp_path, pilot.control.app)
    st = pilot.state()
    st["active"] = {"market": "QQQ-USD", "config": "Mid +1 @ 20x", "mode": "paper", "since": time.time(),
                    "profile": "aggressive", "lev": "rec", "backtest": rows[0]}
    pilot.save(st)
    await bot.handle(msg("/openpositions"))
    assert "golive" in _buttons(api)
    monkeypatch.setenv("BOT_PILOT_LIVE", "1")
    pilot.write_session = lambda c, live, path=None: tmp_path / "pilot.yaml"  # type: ignore[method-assign,assignment]

    async def doctor(name: str, *, replacing: bool = False) -> tuple[bool, str]:
        return True, "all good"
    pilot.control.doctor = doctor  # type: ignore[method-assign]
    await bot.handle(press("golive"))
    await asyncio.sleep(0.05)
    p = next(iter(bot.pending.values()))
    assert p.code and "LIVE" in api.sent[-1][1] and "QQQ · Mid +1 · Neutral · 20x" in api.sent[-1][1]
    await bot.handle(msg(p.code))
    await asyncio.sleep(0.05)
    assert calls == [("QQQ-USD", "Mid +1", 20, True, "volume")]         # the old list name: Most Volume


async def test_list_views_ask_for_a_scan_only_when_stale_and_once(tmp_path: Path) -> None:
    from bot.scout.service import SCAN_NOW, STATUS

    rows = [_cand("QQQ-USD", "Mid +1", lev=20)]
    bot, api, _pilot, _ = _with_pilot(tmp_path, rows)
    trigger = tmp_path / "state" / SCAN_NOW
    await bot.handle(msg("/volume"))
    assert not trigger.exists() and "Scan started" not in api.sent[-1][1] and "Scan running" not in api.sent[-1][1]
    st = {"running": True, "started": time.time() - 270, "workers": 1,
          "history": [{"ts": 0, "took_s": 1200.0, "workers": 1, "full": False}]}
    (tmp_path / "data" / "scout" / STATUS).write_text(json.dumps(st))
    await bot.handle(msg("/aggressive"))
    assert "Scan running · ~15m left" in api.sent[-1][1] and not trigger.exists()
    await bot.handle(msg("/aggressive"))
    assert len(bot.scan_waiters) == 1                                   # asked twice, posted once
    (tmp_path / "data" / "scout" / STATUS).write_text(json.dumps({**st, "running": False}))
    await bot.handle(press("rescan volume"))
    from bot.scout.service import scan_workers
    from bot.telegram.views import ago

    n = scan_workers(None, False)                    # no bot runs here: all cores but one, the 1-worker time scaled
    assert trigger.exists() and f"Scan started · ~{ago(1200 / n)}" in api.texts()


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


async def test_a_mistyped_doctor_session_answers_instead_of_ending_the_bot(tmp_path: Path) -> None:
    """/doctor with a session name that does not exist reached the CLI's sys.exit: SystemExit is not an Exception,
    so it went past the handler and ended the Telegram bot."""
    app = _app(tmp_path)
    bot, api, _ctl = _bot(tmp_path, app)
    await bot.handle(msg("/doctor no_such_session"))
    await asyncio.sleep(0.05)
    assert "CHECK FAILED" in api.texts() and "no session called" in api.texts() and "no_such_session" in api.texts()


async def test_an_old_scan_is_a_warning_not_a_refusal(tmp_path: Path) -> None:
    """"the last scan is over 90 minutes old" refused every list pick while a long scan ran; now it only warns."""
    rows = [_cand("QQQ-USD", "Mid +1", lev=20)]
    bot, api, _pilot, _calls = _with_pilot(tmp_path, rows)
    p = tmp_path / "data" / "scout" / "latest.json"
    scan = json.loads(p.read_text())
    scan["ts_us"] -= 3 * 3600 * 1_000_000
    p.write_text(json.dumps(scan))
    await bot.handle(msg("/top3"))
    assert "scan 3.0 h old" in api.texts() and "You can still run" in api.texts()
    await bot.handle(press("pick breakeven 1"))
    assert "over 90 minutes" not in api.texts() and "20x" in api.edits[-1][2]


async def test_run_any_leverage_below_the_maximum_without_a_backtest(tmp_path: Path, monkeypatch: Any) -> None:
    """2026-09-26: `/run SPY Mid 0 33x live sl=30` answered "no backtest at 33x (has 50x, 20x, ...)". The scan
    now backtests the maximum only; any leverage up to it runs, sized for the balance, marked as not backtested."""

    rows = [_cand("SPY-USD", "Mid 0", lev=50, at_max=True, go=False)]
    bot, api, pilot, _calls = _with_pilot(tmp_path, rows, top=[])
    (tmp_path / "data" / "scout" / "markets.json").write_text(json.dumps({"markets": [
        {"marketDisplayName": "SPY-USD", "status": "ONLINE", "initialMarginFraction": "0.02",
         "offHoursInitialMarginFraction": "0.03", "tickSize": "0.01", "stepSize": "0.001", "minOrderNotional": "5",
         "minOrderSize": "0.001", "markPrice": "770"}]}))
    (tmp_path / "state" / "scout_capital.json").write_text(json.dumps({"usd": 100}))
    monkeypatch.setenv("BOT_PILOT_LIVE", "1")
    pilot.control.is_running = lambda mode: False  # type: ignore[method-assign]
    pilot.write_session = lambda c, live, path=None: tmp_path / "x.yaml"  # type: ignore[method-assign,assignment]

    async def doctor(name: str, *, replacing: bool = False) -> tuple[bool, str]:
        return True, "all good"
    pilot.control.doctor = doctor  # type: ignore[method-assign]
    await bot.handle(msg("/run SPY Mid 0 33x live sl=30"))
    await asyncio.sleep(0.05)
    text = api.sent[-1][1]
    assert "no backtest" not in api.texts() and "LIVE" in text and "SPY · Mid 0 · Neutral · 33x" in text
    assert "Run stop: -$30.00" in text
    await bot.handle(msg("/run SPY Mid 0 20x"))                          # the form at a leverage not backtested
    assert "No backtest at 20x (the scout tests each market" in api.sent[-1][1]
    assert "f SPY-USD m0n 50 0 0 0 manual" in _buttons(api)              # the maximum, backtested, one tap away
