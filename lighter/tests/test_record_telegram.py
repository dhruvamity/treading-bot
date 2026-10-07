import asyncio

from lighter_bot.scout.record import trade_row
from lighter_bot.telegram.bot import Bot


def test_trade_row_sides():
    t = {"trade_id": 5, "tx_hash": "ab", "price": "10", "size": "2", "is_maker_ask": True, "bid_account_id": 1,
         "ask_account_id": 2, "transaction_time": 1_790_000_000_000_000, "type": "trade"}
    r = trade_row(t)
    assert r[3] is True and (r[5], r[6]) == (1, 2)          # the taker bought; taker 1 (bid), maker 2 (ask)
    t["is_maker_ask"] = False
    r = trade_row(t)
    assert r[3] is False and (r[5], r[6]) == (2, 1)


class FakeApi:
    def __init__(self):
        self.sent = []

    async def send(self, chat, text, markup=None):
        self.sent.append((text, markup))
        return {"message_id": 1}

    async def edit(self, *a, **k):
        return None


def test_run_line_opens_the_form(cfg):
    import json

    from lighter_bot.venue.market import Market
    m = Market(1, "BTC", 1, 5, 0.0002, 10.0, 200, 5000, 120, last_price=84000.0, day_volume_usd=1e7)
    (cfg.data_dir / "markets.json").write_text(json.dumps({"BTC": m.as_dict()}))
    api = FakeApi()
    bot = Bot(cfg, api)
    asyncio.run(bot.cmd_run(1, "BTC smart +1 20x sl=10 vol=100k"))
    f = bot.forms[1]
    assert (f.market, f.mode, f.spread, f.lev, f.sl, f.vol) == ("BTC", "smart", 1.0, "20", "10", 100_000)
    spec = bot.spec_of(f, "paper")
    assert spec.setup == "Smart +1" and spec.leverage == 20 and spec.sl == 10
    assert "Not backtested" in api.sent[-1][0]


# ---------------------------------------------------------------- the panel inside the one Telegram bot
class WireApi:
    """What would go to Telegram, after the panel's relabelling."""

    def __init__(self):
        from lighter_bot.telegram.embed import PanelApi
        self.sent = []
        outer = self

        class Api(PanelApi):
            def __init__(self):
                pass

            async def call(self, method, **params):
                outer.sent.append((method, params))
                return {"message_id": 7}
        self.api = Api()


def test_the_panel_says_lighter_and_its_commands_and_buttons_come_back_to_it(cfg):
    from lighter_bot.telegram.embed import Panel, relabel, reroute

    assert relabel("📊 <b>Status</b>\n\n<code>/status · /closeall</code> bid/ask lighter/.env") == \
        "<b>LIGHTER</b> · 📊 <b>Status</b>\n\n<code>/l_status · /l_closeall</code> bid/ask lighter/.env"
    assert relabel("/set daily_stop 6, then /settings") == "<b>LIGHTER</b> · /l_set daily_stop 6, then /l_settings"
    kb = reroute({"inline_keyboard": [[{"text": "Top 3", "callback_data": "list:most"}]]})
    assert kb["inline_keyboard"][0][0]["callback_data"] == "l list:most" and reroute(None) is None

    w = WireApi()
    p = Panel(cfg, "token", 55, {9}, api=w.api)
    assert p.bot.cfg.telegram_chat == "55" and p.bot.cfg.telegram_users == (9,)
    asyncio.run(p.text(55, 9, "/help"))
    method, sent = w.sent[-1]
    assert method == "sendMessage" and sent["text"].startswith("<b>LIGHTER</b> · ")
    assert "/l_top3" in sent["text"] and "/l_closeall" in sent["text"] and " /top3" not in sent["text"]
    data = [btn["callback_data"] for row in sent["reply_markup"]["inline_keyboard"] for btn in row]
    assert data and all(d.startswith("l ") for d in data)

    asyncio.run(p.text(55, 9, "/closeall"))                 # a control asks first; its buttons come back here too
    data = [btn["callback_data"] for row in w.sent[-1][1]["reply_markup"]["inline_keyboard"] for btn in row]
    assert data[0].startswith("l ok:") and data[1] == "l nop"
    assert asyncio.run(p.button(55, 7, data[0][2:])) == "no run is going"
    assert asyncio.run(p.button(55, 7, "f:go:paper")) == "the form expired: /l_run again"


def test_a_read_only_bot_answers_lighter_questions_and_refuses_its_controls(cfg):
    from lighter_bot.telegram.embed import Panel

    w = WireApi()
    p = Panel(cfg, "token", 55, api=w.api)
    asyncio.run(p.text(55, 9, "/status", read_only=True))
    assert "Read-only" not in w.sent[-1][1]["text"]
    n = len(w.sent)
    asyncio.run(p.text(55, 9, "/closeall", read_only=True))
    assert len(w.sent) == n + 1 and "Read-only" in w.sent[-1][1]["text"] and not p.bot.confirms
    assert asyncio.run(p.button(55, 7, "ok:abc", read_only=True)).startswith("read-only")
    assert asyncio.run(p.button(55, 7, "nop", read_only=True)) == "cancelled"
    assert not p.waits_for_code(55)


def test_the_credentials_come_from_the_bots_one_env_file_and_a_lighter_file_wins(tmp_path, monkeypatch):
    from lighter_bot.config import load_env

    for k in ("LIGHTER_ADDRESS", "LBOT_LIVE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LIGHTER_ACCOUNT_INDEX"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "arcus").mkdir()
    (tmp_path / "lighter").mkdir()
    (tmp_path / "arcus" / ".env").write_text("ARCUS_API_PRIVATE_KEY=never-read-here\nLIGHTER_ADDRESS=0xone\n"
                                           "LBOT_LIVE=1\nTELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=5\n")
    e = load_env(tmp_path / "lighter" / ".env")
    e.pop("LBOT_NO_SPAWN", None)                                           # the tests' own guard, from the environment
    assert e == {"LIGHTER_ADDRESS": "0xone", "LBOT_LIVE": "1", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "5"}
    (tmp_path / "lighter" / ".env").write_text("LIGHTER_ADDRESS=0xtwo\nLBOT_LIVE=\n")
    e = load_env(tmp_path / "lighter" / ".env")
    assert e["LIGHTER_ADDRESS"] == "0xtwo" and e["LBOT_LIVE"] == "1"      # an empty line there changes nothing
    monkeypatch.setenv("LIGHTER_ADDRESS", "0xenv")
    assert load_env(tmp_path / "lighter" / ".env")["LIGHTER_ADDRESS"] == "0xenv"
