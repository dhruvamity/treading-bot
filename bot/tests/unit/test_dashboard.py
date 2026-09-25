"""The live dashboard: today's volume, today's PnL and the capital's P/L, in Telegram (/dashboard) and the terminal."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot.common.config import AppConfig
from bot.core.balances import DAY, BalanceLog
from bot.core.heartbeat import write_heartbeat
from bot.core.state import StateStore
from bot.telegram import dashboard
from bot.telegram.api import TelegramError
from bot.telegram.bot import DASH_FILE
from bot.telegram.control import Control, _today_start_us
from tests.unit.test_telegram import OWNER, FakeAPI, _app, _bot, msg, press


def _fill(db: Path, ts: float, price: float, size: float, *, maker: bool = True, fee: float = 0.0,
          base: str = "QQQ") -> None:
    con = sqlite3.connect(db)
    with con:
        con.execute("INSERT INTO fills (trade_id, venue, base, client_id, side, price, size, fee, is_maker, liquidation,"
                    " ts_us, tag, session) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"t{ts}{price}", "arcus", base, "c", "buy", str(price), str(size), str(fee), int(maker), 0,
                     int(ts * 1e6), "", "s"))
    con.close()


def _running(tmp_path: Path, app: AppConfig, mode: str, *, account: dict[str, Any] | None,
             started_s_ago: float = 7200, day_pnl: str = "0.25") -> Path:
    """A bot 'running' in another process (fresh heartbeat, live foreign pid) with a published status."""
    hb = tmp_path / app.heartbeat_for(mode)
    write_heartbeat(hb, mode=mode)
    d = json.loads(hb.read_text())
    d["pid"] = os.getppid()
    hb.write_text(json.dumps(d))
    db = tmp_path / app.state_db_for(mode)
    st = StateStore(db)
    now = time.time()
    snap = {"ts_us": int(now * 1e6), "mode": mode, "started_us": int((now - started_s_ago) * 1e6),
            "markets": [{"venue": "arcus", "market": "QQQ", "position": "0.1", "mark": "600", "net": "0.4",
                         "quoting": True, "why": ""}],
            "sessions": [{"session": "pilot", "market": "QQQ", "venue": "arcus", "pnl": "0.4", "day_pnl": day_pnl,
                          "capital": "28", "size_capital": "28"}],
            "risk": {"all_stopped": None, "safe_mode": {}, "venue_stopped_day": {}, "operator_paused": []},
            "account": {"arcus": account} if account else {}}
    st.kv_set("status", json.dumps(snap))
    st.close()
    return db


def _acct(equity: float, deposits: float, age_s: float = 5.0) -> dict[str, Any]:
    return {"equity": equity, "free": equity / 2, "net_deposits": deposits, "ts_us": int((time.time() - age_s) * 1e6)}


def _deploy(tmp_path: Path, mode: str = "live") -> None:
    (tmp_path / "state" / "pilot.json").write_text(json.dumps({"active": {
        "market": "QQQ-USD", "config": "deep 2bp x2 @ 10x", "mode": mode, "since": time.time() - 7200,
        "backtest": {"volume_day": 5369.0, "pnl_day": 0.32}}}))


def test_live_today_volume_pnl_and_capital(tmp_path: Path) -> None:
    app = _app(tmp_path)
    db = _running(tmp_path, app, "live", account=_acct(50.42, 50.00))
    _deploy(tmp_path)
    now = time.time()
    day0 = _today_start_us(now) / 1e6
    _fill(db, day0 - 60, 600, 1.0)                                    # yesterday: not today's volume
    _fill(db, max(day0 + 1, now - 60), 600, 0.1)                     # $60 maker
    _fill(db, max(day0 + 2, now - 30), 500, 0.1, maker=False, fee=0.01)   # $50 taker
    log = BalanceLog(tmp_path / "state" / "balances.jsonl")
    log.record(source="scout", equity=50.00, net_deposits=50.00, ts=day0 - 600)   # the balance at 00:00 UTC

    d = dashboard.collect(Control(app, root=tmp_path), now=now)
    assert d.mode == "live" and d.running and d.market == "QQQ-USD"
    assert d.volume == pytest.approx(110) and d.maker_volume == pytest.approx(60) and d.fills == 2
    assert d.fees == pytest.approx(0.01)
    assert d.day_pnl == pytest.approx(0.42) and d.day_pnl_note == ""
    assert d.capital_pnl == pytest.approx(0.42) and d.equity == pytest.approx(50.42)
    assert d.account_from == "the bot" and d.account_age_s is not None and d.account_age_s < 30
    assert [(p["market"], p["size"], p["mark"]) for p in d.positions] == [("QQQ", 0.1, 600.0)]
    t_from = max(day0, now - 7200)
    if now - t_from >= dashboard.PACE_AFTER_S:
        assert d.pace_day == pytest.approx(110 / (now - t_from) * DAY)

    html = dashboard.render(d)
    for part in ("📊 <b>QQQ-USD</b> · <b>LIVE</b>", "<i>deep 2bp x2 @ 10x", "Volume <b>$110</b> · 2 fills",
                 "2% of $5,369/day backtest", "PnL <b>+$0.42</b> · +0.84%", "Long <b>0.1 QQQ</b>",
                 "Equity <b>$50.42</b> · deposited $50.00", "P/L <b>+$0.42</b> · +0.84%", "every 10 s",
                 "<blockquote><b>Today</b>", "<blockquote><b>Position</b>", "<blockquote><b>Capital</b>"):
        assert part in html, (part, html)
    assert "<pre>" not in html and html.count("<blockquote>") == html.count("</blockquote>") == 3
    text = dashboard.render(d, html=False, frame="once")
    assert "<b>" not in text and "│ TODAY" in text and "│ CAPITAL" in text and "every 10 s" not in text


def test_bar_and_sparkline() -> None:
    assert dashboard.bar(0.0) == "─" * 12 and dashboard.bar(0.5) == "━" * 6 + "─" * 6 and dashboard.bar(3) == "━" * 12
    assert dashboard.spark([0.0, 1.0]) == ""                             # too few points to draw
    assert dashboard.spark([0.0, 0.5, 1.0]) == "▁▅█"
    assert dashboard.spark([1.0, 1.0, 1.0]) == "▁▁▁"
    long = dashboard.spark([float(x) for x in range(100)])
    assert len(long) == dashboard.SPARK_POINTS and long[0] == "▁" and long[-1] == "█"


def test_position_card_shows_entry_mark_and_stop(tmp_path: Path) -> None:
    app = _app(tmp_path)
    db = _running(tmp_path, app, "live", account=_acct(49.77, 50.00))
    st = StateStore(db)
    snap = json.loads(st.kv_get("status") or "{}")
    snap["markets"] = [{"venue": "arcus", "market": "QQQ", "position": "-0.1503", "mark": "745.80", "quoting": True},
                       {"venue": "arcus", "market": "QQQ", "position": "0", "mark": None, "quoting": True}]
    snap["sessions"][0]["stops"] = {"position": 0.28, "daily": 0.56, "kill": 2.8}
    st.kv_set("status", json.dumps(snap))
    st.close()
    con = sqlite3.connect(db)
    with con:
        con.execute("INSERT OR REPLACE INTO positions (venue, base, size, entry) VALUES ('arcus','QQQ','-0.1503','745.69')")
    con.close()
    BalanceLog(tmp_path / "state" / "balances.jsonl").record(
        source="bot", equity=50.00, net_deposits=50.00, ts=_today_start_us(time.time()) / 1e6 - 60)
    html = dashboard.render(dashboard.collect(Control(app, root=tmp_path)))
    assert html.count("Short <b>0.1503 QQQ</b> ≈ $112.09") == 1           # the market listed twice shows once
    assert "745.69 → 745.80 · <b>-$0.02</b> · stop -$0.28" in html
    assert "PnL <b>-$0.23</b> · -0.46% · 41% of day stop" in html


def test_deposits_never_count_as_profit(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "live", account=_acct(60.42, 60.00))    # $10 deposited today, +$0.42 traded
    now = time.time()
    BalanceLog(tmp_path / "state" / "balances.jsonl").record(
        source="bot", equity=50.00, net_deposits=50.00, ts=_today_start_us(now) / 1e6 - 60)
    d = dashboard.collect(Control(app, root=tmp_path), now=now)
    assert d.day_pnl == pytest.approx(0.42) and d.capital_pnl == pytest.approx(0.42)


def test_freshest_account_reading_wins(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "live", account=_acct(49.73, 50.00, age_s=600))   # a bot that stopped reading 10 min ago
    now = time.time()
    own = {"equity": 50.23, "free": 10.0, "net_deposits": 50.00, "ts": now - 2, "from": "the dashboard"}
    d = dashboard.collect(Control(app, root=tmp_path), now=now, extra=own)
    assert d.equity == 50.23 and d.account_from == "the dashboard"
    d = dashboard.collect(Control(app, root=tmp_path), now=now)
    assert d.equity == 49.73 and d.account_age_s is not None and d.account_age_s > dashboard.FRESH_S


def test_first_day_measures_from_the_first_reading(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "live", account=_acct(49.84, 50.00))
    now = time.time()
    first = max(_today_start_us(now) / 1e6 + 1, now - 60)
    BalanceLog(tmp_path / "state" / "balances.jsonl").record(source="scout", equity=50.00, net_deposits=50.00,
                                                               ts=first)
    d = dashboard.collect(Control(app, root=tmp_path), now=now)
    assert d.day_pnl == pytest.approx(-0.16) and "first reading today" in d.day_pnl_note


def test_week_change_only_with_a_week_of_history(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "live", account=_acct(31.0, 30.0))
    now = time.time()
    log = BalanceLog(tmp_path / "state" / "balances.jsonl")
    log.record(source="scout", equity=29.0, net_deposits=29.0, ts=now - 3 * DAY)
    d = dashboard.collect(Control(app, root=tmp_path), now=now)
    assert d.changes == {}                                       # 3 days of history: the all-time P/L says it all
    log.record(source="scout", equity=29.5, net_deposits=29.0, ts=now - 8 * DAY)   # appended out of order
    log.path.write_text("\n".join(sorted(log.path.read_text().splitlines(), key=lambda ln: json.loads(ln)["ts"]))
                        + "\n")
    d = dashboard.collect(Control(app, root=tmp_path), now=now)
    assert d.changes == {"7 d": pytest.approx(1.0 - 0.5)}


def test_paper_uses_its_own_account_not_the_real_one(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "paper", account=_acct(101.5, 100.0), day_pnl="0.75")
    BalanceLog(tmp_path / "state" / "balances.jsonl").record(source="scout", equity=50.00, net_deposits=50.00)
    d = dashboard.collect(Control(app, root=tmp_path))
    assert d.mode == "paper" and d.equity == 101.5 and d.capital_pnl == pytest.approx(1.5)
    assert d.day_pnl == pytest.approx(0.75) and "own count" in d.day_pnl_note
    assert "Capital (paper)" in dashboard.render(d) and "started with $100.00" in dashboard.render(d)


def test_no_bot_shows_the_real_account_not_an_old_paper_bot(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "paper", account=_acct(101.5, 100.0))
    hb = tmp_path / app.heartbeat_for("paper")
    d = json.loads(hb.read_text())
    d["ts_us"] = int((time.time() - 3 * DAY) * 1e6)                  # stopped days ago
    hb.write_text(json.dumps(d))
    os.utime(hb, (time.time() - 3 * DAY, time.time() - 3 * DAY))
    BalanceLog(tmp_path / "state" / "balances.jsonl").record(source="scout", equity=49.84, net_deposits=50.00)
    d2 = dashboard.collect(Control(app, root=tmp_path))
    assert d2.mode is None and not d2.running and d2.equity == 49.84
    assert d2.capital_pnl == pytest.approx(-0.16)
    assert "No trading bot running" in dashboard.render(d2)


# ------------------------------------------------------------------------------------------------ Telegram
class PinAPI(FakeAPI):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_edit: str | None = None

    async def call(self, method: str, **kw: Any) -> Any:
        self.calls.append((method, kw))
        return {"username": "bot_test_bot"}

    async def edit(self, chat_id: Any, message_id: int, text: str, *, keyboard: Any = None) -> None:
        if self.fail_edit:
            raise TelegramError(self.fail_edit)
        self.edits.append((chat_id, message_id, text))
        self.last_keyboard = keyboard


async def test_telegram_dashboard_updates_stops_and_resumes(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _running(tmp_path, app, "live", account=_acct(50.42, 50.00))
    bot, _, _ = _bot(tmp_path, app)
    api = PinAPI()
    bot.api = api  # type: ignore[assignment]
    state = tmp_path / "state" / DASH_FILE

    await bot.handle(msg("/dashboard"))
    first = len(api.sent)
    assert "Volume" in api.sent[-1][1] and api.sent[-1][2] == [[("⏹ Stop updating", "dashstop")]]
    assert json.loads(state.read_text())["message_id"] == first
    assert ("pinChatMessage", {"chat_id": OWNER, "message_id": first, "disable_notification": True}) in api.calls

    await bot._dash_tick()                                          # the 10 s refresh edits that same message
    assert api.edits[-1][:2] == (OWNER, first) and "every 10 s" in api.edits[-1][2]

    await bot.handle(msg("/dashboard"))                             # a newer one retires the older
    assert json.loads(state.read_text())["message_id"] == len(api.sent) != first
    retired = [e for e in api.edits if e[1] == first][-1][2]
    assert "Stopped at" in retired and "newer dashboard" in retired
    assert ("unpinChatMessage", {"chat_id": OWNER, "message_id": first}) in api.calls

    await bot.handle(press("dashstop"))
    assert not state.exists() and "Stopped at" in api.edits[-1][2]
    assert api.last_keyboard == [[("▶️ Update live again", "dashresume")]]
    n = len(api.edits)
    await bot._dash_tick()
    assert len(api.edits) == n                                      # stopped: no more edits

    await bot.handle(press("dashresume"))                           # the button's own message (id 7) goes live
    assert json.loads(state.read_text())["message_id"] == 7 and "every 10 s" in api.edits[-1][2]

    api.fail_edit = "editMessageText: 400 Bad Request: message to edit not found"   # the owner deleted it
    await bot._dash_tick()
    assert not state.exists()


async def test_telegram_dashboard_reads_the_account_at_most_once_a_minute(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path)
    bot, _, _ = _bot(tmp_path, app)
    reads: list[str] = []

    async def fake_snapshot(url: str, idx: int = 0) -> dict[str, float]:
        reads.append(url)
        return {"equity": 49.84, "free": 6.68, "net_deposits": 50.00}

    monkeypatch.setattr("bot.scout.capital.account_snapshot", fake_snapshot)
    monkeypatch.setattr("bot.common.config.load_arcus_config",
                        lambda p: SimpleNamespace(rest=SimpleNamespace(mainnet="https://rest")))
    text = await bot._dash_text()
    assert reads == ["https://rest"] and "$49.84" in text and "-$0.16" in text and "balance 0s old" in text
    await bot._dash_text()
    assert len(reads) == 1                                          # cached for a minute
    assert bot._dash_account is not None
    bot._dash_account["ts"] -= 120
    await bot._dash_text()
    assert len(reads) == 2


def test_menu_help_and_commands_list_the_dashboard() -> None:
    from bot.telegram.views import COMMANDS, HELP, menu_keyboard

    assert COMMANDS[0][0] == "dashboard" and "/dashboard" in HELP
    assert menu_keyboard()[0] == [("📺 Live dashboard", "dashboard")]


def test_cli_dashboard_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from bot import cli

    app = _app(tmp_path)
    _running(tmp_path, app, "live", account=_acct(50.42, 50.00))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_app", lambda: app)
    monkeypatch.setattr(cli, "load_arcus_config", lambda: SimpleNamespace(rest=SimpleNamespace(mainnet="x")))
    cli.cmd_dashboard(SimpleNamespace(once=True))
    out = capsys.readouterr().out
    assert "TODAY" in out and "P/L +$0.42" in out and "<b>" not in out


def test_status_is_a_card_and_lists_a_market_once(tmp_path: Path) -> None:
    from bot.telegram.views import orders_text, positions_text, status_text

    app = _app(tmp_path)
    db = _running(tmp_path, app, "live", account=_acct(49.77, 50.00))
    st = StateStore(db)
    snap = json.loads(st.kv_get("status") or "{}")
    snap["markets"] = [{"venue": "arcus", "market": "QQQ", "position": "-0.1503", "mark": "745.80", "quoting": True},
                       {"venue": "arcus", "market": "QQQ", "position": "0", "mark": None, "quoting": True}]
    st.kv_set("status", json.dumps(snap))
    st.close()
    con = sqlite3.connect(db)
    with con:
        for k, (side, px) in enumerate((("buy", "744.36"), ("sell", "745.69"), ("buy", "745.37"))):
            con.execute("INSERT INTO orders (client_id, venue, base, side, price, size, status, tag) "
                        "VALUES (?,?,?,?,?,?,?,?)", (f"c{k}", "arcus", "QQQ", side, px, "0.15", "OPEN", f"t{k}"))
    con.close()
    v = Control(app, root=tmp_path).view("live")
    text = status_text([v])
    assert "<pre>" not in text and "<blockquote>" in text and text.count("QQQ short $112.09") == 1, text
    assert "3 open orders" in text and "🟢 <b>LIVE</b> · running · up 2h00m" in text
    assert text.count("QQQ") == 1 and "<pre>" not in positions_text(v) + orders_text(v)
    book = orders_text(v)
    assert book.index("SELL") < book.index("745.37") < book.index("744.36")     # asks on top, then bids high to low
