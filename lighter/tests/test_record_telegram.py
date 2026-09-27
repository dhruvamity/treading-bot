import asyncio

from lbot.scout.record import trade_row
from lbot.telegram.bot import Bot


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

    from lbot.venue.market import Market
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
