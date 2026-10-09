"""`lighter livetest` against a stand-in for Lighter (no network, no real key): the whole sequence, what it reports,
that it touches nothing it did not place, and that it always ends flat with no orders."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest

from lighter_bot import livetest as lt
from lighter_bot.trade.live import LiveExchange
from lighter_bot.venue import consts as C
from lighter_bot.venue import signer
from lighter_bot.venue.market import Market
from lighter_bot.venue.nonce import ClientIds, Nonces
from lighter_bot.venue.rest import ApiError

M = Market(market_id=7, symbol="SPY", price_decimals=2, size_decimals=3, min_base=0.001, min_quote=10.0,
           imf_min=200, imf_default=5000, mmf=120, last_price=100.0, day_volume_usd=5e7)
ACCT = 4242


class Venue:
    """Lighter, as far as the test needs it: a book that does not move, our orders, our position, the scheduled
    cancel-all. It answers the REST calls and pushes the account-stream frames the real one would."""

    def __init__(self, ex, *, far_limit=0.015, flat_row=False, stream_flat=True, fill_touch=False, cross_fills=False,
                 min_cancel_ms=300_000, equity=6.0, slip_max=0.02, bleed=0.0, forgets=False, late_s=0.0, elsewhere=0):
        self.ex, self.bid, self.ask = ex, 100.00, 100.02
        self.far_limit, self.flat_row, self.stream_flat = far_limit, flat_row, stream_flat
        self.fill_touch, self.cross_fills, self.min_cancel_ms, self.slip_max = fill_touch, cross_fills, min_cancel_ms, slip_max
        self.equity, self.bleed = equity, bleed
        self.forgets, self.late_s, self.elsewhere = forgets, late_s, elsewhere   # the scheduled cancel-all going wrong
        self.orders: dict[int, dict] = {}
        self.pos = 0.0
        self.now = time.time()
        self.scheduled: float | None = None
        self.asked = 0                       # scheduled cancel-alls that arrived
        self.sent: list[int] = []            # every transaction type that arrived
        self.leverage: list[int] = []
        self.tid = 0
        self.auth = "token"

    # ---- time
    async def sleep(self, s: float) -> None:
        self.now += s
        if self.scheduled is not None and self.now * 1000 >= self.scheduled + self.late_s * 1000:
            self.scheduled = None
            self._cancel_everything()
        await asyncio.sleep(0)

    def clock(self) -> float:
        return self.now

    # ---- frames
    def _order_frame(self, o: dict) -> None:
        self.ex.on_msg({"channel": f"account_orders:{M.market_id}", "type": "update/account_orders",
                        "orders": {str(M.market_id): [dict(o)]}})

    def _cancel_everything(self) -> None:
        for cid in list(self.orders):
            self._order_frame({**self.orders.pop(cid), "status": "canceled"})

    def _trade(self, cid: int, buy: bool, qty: float, px: float, maker: bool) -> None:
        self.tid += 1
        self.pos += qty if buy else -qty
        self.equity -= (self.ask - self.bid) / 2 * qty + self.bleed
        t = {"trade_id": self.tid, "price": str(px), "size": str(qty), "timestamp": int(time.time() * 1000),
             "is_maker_ask": (not buy) if maker else buy, "bid_account_id": ACCT if buy else 9,
             "ask_account_id": 9 if buy else ACCT, "bid_client_id": cid if buy else 0, "ask_client_id": 0 if buy else cid,
             "maker_fee": 0, "taker_fee": 0}
        msg = {"channel": f"account_all:{ACCT}", "type": "update/account_all", "trades": {str(M.market_id): [t]}}
        if abs(self.pos) > 1e-9 or self.stream_flat:
            msg["positions"] = {str(M.market_id): {"position": str(abs(round(self.pos, 6))), "sign": 1 if self.pos >= 0 else -1,
                                                   "avg_entry_price": str(px)}}
        self.ex.on_msg(msg)

    # ---- REST
    async def account(self, index: int):
        rows = []
        if abs(self.pos) > 1e-9 or self.flat_row:
            rows.append({"market_id": M.market_id, "position": str(abs(round(self.pos, 6))),
                         "sign": 1 if self.pos >= 0 else -1, "avg_entry_price": "100.01"})
        return {"accounts": [{"total_asset_value": str(self.equity), "available_balance": str(self.equity),
                              "positions": rows, "cancel_all_time": int(self.scheduled or 0),
                              "total_order_count": len(self.orders) + self.elsewhere}]}

    async def tx(self, tx_hash: str):
        return {"hash": tx_hash, "status": 3, "event_info": '{"ae":"did not run"}'}

    async def active_orders(self, account: int, market: int | None = None):
        return {"orders": [dict(o) for o in self.orders.values()]}

    async def close(self) -> None:
        return None

    async def send(self, txs, *, kind="reserve", wait=True):
        fields = [(t.tx_type, signer.tx_fields(t)) for t in txs]
        mid = (self.bid + self.ask) / 2
        for typ, f in fields:                                   # refuse the request whole, as Lighter's API does
            if typ == C.TX_CREATE_ORDER:
                px, trig = f["Price"] / 100, f["TriggerPrice"] / 100
                if f["Type"] == C.ORDER_LIMIT and abs(px / mid - 1) > self.far_limit:
                    raise ApiError(400, C.ERR_TOO_FAR_FROM_MARK, "limit order price is too far from the mark price")
                if f["Type"] in (C.ORDER_STOP_LOSS, C.ORDER_TAKE_PROFIT) and abs(px / trig - 1) > self.slip_max:
                    raise ApiError(400, 21735, "SL/TP order price is too far from the trigger price")
            elif typ == C.TX_CANCEL_ORDER and f["Index"] not in self.orders:
                raise ApiError(400, 21727, "order not found")
            elif typ == C.TX_CANCEL_ALL and f["TimeInForce"] == C.CANCEL_ALL_SCHEDULED \
                    and f["Time"] - self.now * 1000 < self.min_cancel_ms:
                raise ApiError(400, 21714, "invalid cancel all time")
        for typ, f in fields:
            self.sent.append(typ)
            if typ == C.TX_UPDATE_LEVERAGE:
                self.leverage.append(f["InitialMarginFraction"])
            elif typ == C.TX_CANCEL_ALL:
                if f["TimeInForce"] == C.CANCEL_ALL_NOW:
                    self._cancel_everything()
                else:
                    self.asked += f["TimeInForce"] == C.CANCEL_ALL_SCHEDULED
                    self.scheduled = f["Time"] if f["TimeInForce"] == C.CANCEL_ALL_SCHEDULED and not self.forgets \
                        else None
            elif typ == C.TX_MODIFY_ORDER:
                o = self.orders[f["Index"]]
                o["price"] = f"{f['Price'] / 100:.2f}"
                self._order_frame(o)
            elif typ == C.TX_CANCEL_ORDER:
                self._order_frame({**self.orders.pop(f["Index"]), "status": "canceled"})
            elif typ == C.TX_CREATE_ORDER:
                self._create(f)
        return {"code": 200, "tx_hash": [f"h{i}" for i in range(len(txs))]} if len(txs) > 1 else {"code": 200}

    def _create(self, f: dict) -> None:
        cid, buy, px, qty = f["ClientOrderIndex"], not f["IsAsk"], f["Price"] / 100, f["BaseAmount"] / 1000
        o = {"client_order_index": cid, "order_index": 2**48 + cid, "price": f"{px:.2f}", "is_ask": not buy,
             "remaining_base_amount": f"{qty:.3f}", "status": "open", "reduce_only": bool(f["ReduceOnly"]),
             "type": {0: "limit", 1: "market", 2: "stop-loss", 4: "take-profit"}[f["Type"]],
             "trigger_price": f"{f['TriggerPrice'] / 100:.2f}"}
        if f["Type"] in (C.ORDER_STOP_LOSS, C.ORDER_TAKE_PROFIT):
            self.orders[cid] = {**o, "status": "pending"}
            return self._order_frame(self.orders[cid])
        reduces = (self.pos > 0 and not buy) or (self.pos < 0 and buy)
        if f["ReduceOnly"] and not reduces:
            return self._order_frame({**o, "status": "canceled-reduce-only"})
        if f["Type"] == C.ORDER_MARKET:
            self._order_frame({**o, "status": "filled", "remaining_base_amount": "0"})
            return self._trade(cid, buy, qty, self.ask if buy else self.bid, maker=False)
        crosses = px >= self.ask if buy else px <= self.bid
        if crosses and not self.cross_fills:
            return self._order_frame({**o, "status": "canceled-post-only"})
        at_touch = abs(px - (self.bid if buy else self.ask)) < 0.005
        if crosses or (at_touch and self.fill_touch):
            self._order_frame({**o, "status": "filled", "remaining_base_amount": "0"})
            return self._trade(cid, buy, qty, px, maker=not crosses)
        self.orders[cid] = o
        self._order_frame(o)


class Feed:
    def __init__(self, v):
        self.v = v

    def bbo(self):
        return self.v.bid, self.v.ask, 5.0, 5.0

    async def start(self): ...
    async def stop(self): ...


def make(cfg, tmp_path, **kw):
    priv, _ = signer.generate_key()
    c = replace(cfg, creds=replace(cfg.creds, private_key=priv, api_key_index=5))
    ex = LiveExchange(M, None, c, ACCT, ClientIds(tmp_path / "ids"), Nonces(tmp_path / "n"))
    v = Venue(ex, **kw)
    ex.feed, ex.rest, ex.started_ms = Feed(v), v, 0

    async def start() -> None:
        await ex.reconcile()
        await ex.cancel_all()

    async def stop() -> None:
        return None

    ex.start, ex.stop, ex._auth = start, stop, lambda: "token"
    said: list[str] = []
    t = lt.LiveTest(ex, say=said.append, sleep=v.sleep, clock=v.clock, wait_fill_s=4.0)
    return t, v, said


def results(rep) -> dict[str, str]:
    return {s.name: s.result for s in rep.steps}


def test_the_smallest_order_clears_the_minimum_with_room():
    assert lt.min_qty(0.001, 10.0, 0.001, 100.0) == pytest.approx(0.11)          # $10 minimum at $100: 0.1, plus 10%
    assert lt.min_qty(0.0002, 10.0, 0.00001, 84000.0) == pytest.approx(0.00022)  # BTC: the base minimum binds
    assert lt.min_qty(0.001, 10.0, 0.001, 100.0) * 100.0 * (1 - lt.FAR[0]) >= 10.0   # still an order 2% away


def test_the_whole_sequence_passes_and_ends_flat_with_no_orders(cfg, tmp_path):
    t, v, _said = make(cfg, tmp_path)
    rep = asyncio.run(t.run())
    r = results(rep)
    assert not rep.failed, [(s.name, s.detail) for s in rep.failed]
    assert rep.clean and not rep.aborted and v.pos == 0 and not v.orders and v.scheduled is None
    for name in ("connect", "leverage 5x", "limit order", "modify", "cancel", "cancel-all (this market)", "batch of two",
                 "post-only that would cross", "buy with a taker order", "stop and take-profit orders",
                 "cancel-all removes the stops", "sell to close: flat", "reduce-only order with no position",
                 "leverage 50x", "sell short with a taker order", "buy to close: flat", "dead man's switch", "end"):
        assert r[name] == lt.PASS, (name, r.get(name))
    assert r["buy as maker"] == lt.INFO and r["batch with a bad member"] == lt.INFO
    assert t.far == 0.01                                    # 2% was refused as too far from the mark, 1% rests
    assert v.leverage == [M.leverage_fraction(5), M.leverage_fraction(50)]
    notes = {s.name: s.detail for s in rep.steps}
    assert "refused whole" in notes["batch with a bad member"]
    assert "1% past the trigger" in notes["stop and take-profit orders"]       # the arbitrage's 5% was refused here
    assert "no longer lists the market at all" in notes["sell to close: flat"]
    assert "scheduled 330 s ahead" in notes["dead man's switch"] and "by itself" in notes["dead man's switch"]
    assert 0 < rep.equity_start - rep.equity_end < 0.05     # the spread on four minimum orders
    text = rep.text()
    assert "Ended flat with no orders." in text and "| dead man's switch | PASS |" in text
    assert "Sig" not in text and str(ACCT) in text


def test_it_touches_nothing_on_an_account_that_already_has_an_order_or_a_position(cfg, tmp_path):
    t, v, _said = make(cfg, tmp_path)
    v.orders[99] = {"client_order_index": 0, "order_index": 99, "price": "90.00", "is_ask": False,
                    "remaining_base_amount": "1.000", "status": "open"}        # the owner's own order
    rep = asyncio.run(t.run())
    assert "already has" in rep.aborted and "Nothing was sent" in rep.aborted
    assert v.sent == [] and 99 in v.orders and rep.clean                       # not one transaction; the order stays
    t2, v2, _ = make(cfg, tmp_path / "b")
    v2.pos = 0.5
    rep2 = asyncio.run(t2.run())
    assert "a position" in rep2.aborted and v2.sent == [] and v2.pos == 0.5


def test_a_maker_fill_at_the_touch_is_reported_as_one(cfg, tmp_path):
    t, v, _ = make(cfg, tmp_path, fill_touch=True)
    t.dms = False
    rep = asyncio.run(t.run())
    r = results(rep)
    assert r["buy as maker"] == lt.PASS and r["sell to close as maker"] == lt.PASS and "buy with a taker order" not in r
    assert r["dead man's switch"] == lt.SKIP and rep.clean and not rep.failed and v.pos == 0


def test_a_post_only_order_that_trades_is_a_failure_and_is_closed(cfg, tmp_path):
    t, v, _ = make(cfg, tmp_path, cross_fills=True)
    t.dms = False
    rep = asyncio.run(t.run())
    assert results(rep)["post-only that would cross"] == lt.FAIL
    assert rep.clean and v.pos == 0 and not v.orders          # whatever it did, it left nothing behind


def test_it_stops_and_closes_when_the_account_is_down_more_than_it_may_lose(cfg, tmp_path):
    t, v, _said = make(cfg, tmp_path, bleed=0.8)                # every fill costs 80 cents here
    t.max_loss = 1.0
    rep = asyncio.run(t.run())
    assert "more than the $1.00" in rep.aborted and rep.clean and v.pos == 0 and not v.orders
    assert "sell short with a taker order" not in results(rep)                 # it never got that far


def dms_alone(cfg, tmp_path, **kw):
    t, v, said = make(cfg, tmp_path, **kw)
    t.dms_only = True
    rep = asyncio.run(t.run())
    return rep, v, said, {s.name: s.detail for s in rep.steps}


def test_the_dead_mans_switch_alone_places_one_far_order_and_trades_nothing(cfg, tmp_path):
    rep, v, _said, notes = dms_alone(cfg, tmp_path)
    assert [s.name for s in rep.steps] == ["connect", "dead man's switch", "end"] and not rep.failed and rep.clean
    assert C.TX_UPDATE_LEVERAGE not in v.sent and v.tid == 0 and v.pos == 0 and not v.orders and v.scheduled is None
    assert "by itself after 33" in notes["dead man's switch"]
    assert "dead man's switch only" in lt.plan_text(M, 5.0, 1.0, True, True)


def test_a_scheduled_cancel_all_lighter_answers_ok_to_but_does_not_hold_fails_at_once(cfg, tmp_path):
    rep, v, said, notes = dms_alone(cfg, tmp_path, forgets=True)
    assert results(rep)["dead man's switch"] == lt.FAIL and "holds NO scheduled time" in notes["dead man's switch"]
    assert "did not run" in notes["dead man's switch"]          # what Lighter says it did with the transaction
    assert not any("waiting up to" in x for x in said)          # no 11 minutes spent on a time that is not there
    assert rep.clean and not v.orders


def test_a_scheduled_cancel_all_that_fires_late_or_never_fails_and_says_which(cfg, tmp_path):
    rep, v, _said, notes = dms_alone(cfg, tmp_path, late_s=150.0)
    assert results(rep)["dead man's switch"] == lt.FAIL and "LATE" in notes["dead man's switch"]
    assert rep.clean and not v.orders
    rep2, v2, _said2, notes2 = dms_alone(cfg, tmp_path / "b", late_s=9e9)
    assert results(rep2)["dead man's switch"] == lt.FAIL and "STILL THERE 30" in notes2["dead man's switch"]
    assert rep2.clean and not v2.orders and v2.scheduled is None      # the clean-up cancelled it and withdrew the time


def test_the_dead_mans_switch_is_not_tried_when_other_markets_have_orders(cfg, tmp_path):
    rep, v, _said, notes = dms_alone(cfg, tmp_path, elsewhere=2)
    assert results(rep)["dead man's switch"] == lt.SKIP and "2 open order(s) in other markets" in notes["dead man's switch"]
    assert C.TX_CREATE_ORDER not in v.sent and v.asked == 0      # no order, and no scheduled cancel-all asked for


def test_a_venue_that_keeps_listing_a_closed_market_also_passes(cfg, tmp_path):
    t, _v, _ = make(cfg, tmp_path, flat_row=True, stream_flat=False)
    t.dms = False
    rep = asyncio.run(t.run())
    notes = {s.name: s.detail for s in rep.steps}
    assert not rep.failed and "still lists the market with 0" in notes["sell to close: flat"]
    assert "stream said flat NO" in notes["sell to close: flat"] and "after a reconcile the bot holds +0" in notes[
        "sell to close: flat"]


def test_the_cheapest_liquid_market_is_picked():
    ms = {"BTC": replace(M, symbol="BTC", min_base=0.0002, last_price=84000.0, day_volume_usd=9e8),
          "SPY": M, "DUST": replace(M, symbol="DUST", min_quote=1.0, day_volume_usd=1e3),
          "OFF": replace(M, symbol="OFF", active=False, day_volume_usd=9e9)}
    assert lt.pick_market(ms).symbol == "SPY"                 # not the illiquid one, not the dearer one, not the shut one
    with pytest.raises(ValueError):
        lt.pick_market({"OFF": ms["OFF"]})
    assert "LIVE TEST on SPY" in lt.plan_text(M, 5.0, 1.0, True) and "$11.00" in lt.plan_text(M, 5.0, 1.0, False)
