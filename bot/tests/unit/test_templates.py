"""The owner's Telegram templates (2026-09-26): one emoji and a bold title, a blank line, monospace lines in groups.
Each case is a message from the templates, as Telegram HTML, with no Markdown signs left in it."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from bot.common.tgfmt import plain_card
from bot.core.heartbeat import write_heartbeat
from bot.core.risk import RiskAction, RiskDecision, alert_text
from bot.telegram import dashboard
from bot.telegram.control import Control
from bot.telegram.watcher import Prefs, Watcher
from bot.venues.base import Venue
from tests.unit.test_telegram import _app, _bot, _running_paper, msg


def _no_markdown(text: str) -> None:
    assert "**" not in text and "`" not in text and not re.search(r"(^|\n)#+ ", text), text


def test_background_alerts_read_like_the_templates() -> None:
    cases = [
        (RiskDecision("position_stop", RiskAction.PAUSE_QUOTES, Venue.ARCUS, "BTC", "r", "x",
                      ("BTC · PnL -$1.34", "Stop -$1.10")),
         "🛑 <b>POSITION STOP</b>\n\n<code>BTC · PnL -$1.34</code>\n\n<code>Stop -$1.10</code>"),
        (RiskDecision("daily_loss", RiskAction.STOP_VENUE_DAY, Venue.ARCUS, None, "r", "x",
                      ("PnL -$3.30", "Trading stopped for today")),
         "🛑 <b>DAILY STOP</b>\n\n<code>PnL -$3.30</code>\n\n<code>Trading stopped for today</code>"),
        (RiskDecision("safety_pause", RiskAction.PAUSE_QUOTES, Venue.ARCUS, "BTC", "r", "x",
                      ("Spread 1.7bp > 3× median 0.5bp", "Quoting paused")),
         "⚠️ <b>SAFETY PAUSE</b>\n\n<code>Spread 1.7bp &gt; 3× median 0.5bp</code>\n\n<code>Quoting paused</code>"),
    ]
    for d, want in cases:
        assert plain_card(alert_text(d)) == want
    guardian = plain_card("🚨 GUARDIAN STOP\nBot heartbeat lost · 75s\n\nOrders cancelled · position status: Flat")
    assert guardian == ("🚨 <b>GUARDIAN STOP</b>\n\n<code>Bot heartbeat lost · 75s</code>\n\n"
                        "<code>Orders cancelled · position status: Flat</code>")


def test_a_safety_pause_names_what_tripped_it() -> None:
    from bot.common.config import RiskLimitsCfg, SafetyPauseCfg
    from bot.core.risk import RiskEngine

    r = RiskEngine(limits=RiskLimitsCfg(), safety=SafetyPauseCfg())

    class Book:
        def spread_bps(self) -> float:
            return 1.7

        def depth_notional(self, bid: bool, within_bps: float) -> float:
            return 1e9

    class Med:
        def __init__(self, x: float) -> None:
            self.x, self.buf = x, [x] * 1000

        def median(self) -> float:
            return self.x

    class Vol:
        n = 0

        def sigma(self) -> float:
            return 0.0

    view: Any = type("V", (), {"venue": Venue.ARCUS, "base": "BTC", "vol_1s": Vol(), "last_move_1s": 0.0,
                               "book": Book(), "spread_med_1h": Med(0.5), "depth_med_1h": Med(0.0)})()
    d = r.safety_pause(view, 1_000_000)
    assert d is not None and d.lines == ("Spread 1.7bp > 3× median 0.5bp", "Quoting paused")


def test_start_is_the_bot_control_card(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    text = dashboard.control_text(dashboard.collect(Control(app, root=tmp_path)))
    assert text.startswith("🤖 <b>Bot Control</b>\n\n<code>No setup deployed · PAPER · RUNNING</code>")
    _no_markdown(text)


async def test_start_menu_and_cancel_all_follow_the_templates(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    bot, api, ctl = _bot(tmp_path, app)
    await bot.handle(msg("/start"))
    text, kb = api.sent[-1][1], api.sent[-1][2]
    assert text.startswith("🤖 <b>Bot Control</b>") and kb[:2] == [
        [("📊 Dashboard", "dashboard"), ("▶️ Resume", "unpause")], [("⏸ Pause Orders", "pauseneworders"), ("⏹ Stop", "stop")]]

    async def cancel_all(mode: str, venue: str) -> dict[str, Any]:
        return {"venue": venue, "net": "MAINNET", "account": 0, "open": 0}
    ctl.cancel_all = cancel_all  # type: ignore[method-assign]
    await bot.handle(msg("/cancelall live"))
    assert api.sent[-1][1].startswith("❌ <b>CANCEL ALL ORDERS</b>")
    await bot.handle({"callback_query": {"id": "1", "data": f"ok {next(iter(bot.pending))}",
                                         "from": {"id": 111}, "message": {"chat": {"id": 111}, "message_id": 5}}})
    await asyncio.sleep(0.05)
    assert "⏳ <b>Cancelling orders…</b>" in [t for _, _, t in api.edits] + [t for _, t, _ in api.sent]
    assert api.sent[-1][1] == "✅ <b>ORDERS CANCELLED</b>\n\n<code>Active orders: 0</code>"
    await bot.handle(msg("/pauseneworders"))
    assert api.sent[-1][1] == ("⏸ <b>NEW ORDERS PAUSED</b>\n\n<code>All markets · paper</code>\n\n"
                               "<code>Existing exit orders remain active.</code>")
    for _, t, _ in api.sent:
        _no_markdown(t)


async def test_the_watcher_says_stopped_or_down_like_the_templates(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    ctl = Control(app, root=tmp_path)
    got: list[str] = []

    async def notify(text: str, critical: bool) -> None:
        got.append(text)

    w = Watcher(ctl, notify, Prefs(fills="off"))
    await w.tick()
    hb = tmp_path / app.heartbeat_for("paper")
    write_heartbeat(hb, mode="paper", stopped="quotes cancelled, bot stopped")   # a stop on purpose, process gone
    d = json.loads(hb.read_text())
    d["pid"] = 2 ** 22 + 12345
    hb.write_text(json.dumps(d))
    await w.tick()
    assert got[-1] == ("⏹ <b>PAPER STOPPED</b>\n\n<code>Quotes cancelled</code>\n\n"
                       "<code>Positions unchanged: AMD long $24.80</code>"), got
    _running_paper(tmp_path, app)
    await w.tick()                                     # started again (not from here): says so
    assert got[-1] == "🟢 <b>PAPER STARTED</b>\n\n<code>Heartbeat OK</code>"
    hb.unlink()                                        # the bot dies
    await w.tick()
    assert got[-1].startswith("🔴 <b>PAPER DOWN</b>\n\n<code>Heartbeat lost")
    assert got[-1].endswith("<code>Guardian / dead-man switch protecting orders</code>")


def test_this_run_shows_its_own_limits_and_a_finished_run() -> None:
    from bot.telegram.dashboard import _cfg, run_lines

    run = {"pnl": "-2.36", "limit": 10.0, "tp": 5.0, "volume": 48_200.0, "target": 100_000.0, "done": ""}
    assert run_lines(run) == ["-$2.36 / -$10.00 stop", "-$2.36 / +$5.00 take profit", "Volume $48.2k / $100.0k target"]
    assert run_lines({"pnl": "0", "limit": 10.0, "volume": 900.0}) == ["$0.00 / -$10.00 stop", "Volume $900"]
    assert _cfg("Mid 0 @ 40x", 110) == "Mid 0 · Neutral · 40x · $110 sizing"
    assert _cfg("touch 0bp @ 40x", 110) == "Mid 0 · Neutral · 40x · $110 sizing"      # a run from before the rename
    assert _cfg("Grid +3 Long @ 10x", None) == "Grid +3 · Long · 10x"


def test_a_finished_run_reads_run_done(tmp_path: Path) -> None:
    from bot.telegram.views import mode_state

    app = _app(tmp_path)
    _running_paper(tmp_path, app)
    ctl = Control(app, root=tmp_path)
    v = ctl.view("paper")
    v.snapshot = {**(v.snapshot or {}), "risk": {"all_stopped": "this run is done: volume target $100,000 reached"}}
    assert mode_state(v) == ("✅", "RUN DONE", ["This run is done: volume target $100,000 reached",
                                               "Start a new run: /run"])
    alert = plain_card("✅ RUN DONE\nBTC · Volume target $100,000 reached\nPnL -$8.20 · Volume $100,412\n\n"
                       "Flat · quotes cancelled · start a new run: /run")
    assert alert.startswith("✅ <b>RUN DONE</b>\n\n<code>BTC · Volume target $100,000 reached</code>")
    _no_markdown(alert)
