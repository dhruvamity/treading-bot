"""`arbitrage livetest` against a stand-in for the Lighter leg (no network, no key): the sequence, what it reports,
that it touches nothing it did not place, and that it ends flat with no orders."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from arbitrage.exec import legtest as lt
from arbitrage.exec.venue import BUY, OrderInfo, Spec, Top


class Leg:
    """What exec/lighter.py's LighterTrade shows the test, and Lighter's own REST reads behind it."""

    def __init__(self, *, stops_listed: int = 2, far_limit: float = 0.015) -> None:
        self.account, self.market = 4242, SimpleNamespace(market_id=7, step=0.001, min_base=0.001, min_quote=10.0)
        self.rest = self
        self.bid, self.ask, self.pos, self.equity, self.n = 100.00, 100.02, 0.0, 50.0, 0
        self.infos: dict[str, OrderInfo] = {}
        self.book: dict[str, dict[str, Any]] = {}
        self.sent: list[str] = []
        self.stops_listed, self.far_limit, self.now = stops_listed, far_limit, 1_791_600_000.0

    # ---- time
    async def sleep(self, s: float) -> None:
        self.now += s
        for oid, o in list(self.book.items()):
            if o.get("_expiry") and o["_expiry"] <= self.now:
                del self.book[oid]
                self.infos[oid].open, self.infos[oid].note = False, "canceled-expired"

    def clock(self) -> float:
        return self.now

    # ---- Lighter's REST
    async def active_orders(self, account: int, market: int | None = None) -> dict[str, Any]:
        return {"orders": list(self.book.values())}

    async def account_(self) -> dict[str, Any]:
        row = {"market_id": 7, "position": str(abs(self.pos)), "sign": 1 if self.pos >= 0 else -1}
        rows = [row] if self.pos else []
        return {"accounts": [{"total_asset_value": str(self.equity), "positions": rows}]}

    # ---- the adapter
    async def start(self, symbol: str) -> Spec:
        return Spec(0.01, 0.001, 0.001, 10.0)

    async def stop(self) -> None:
        self.sent.append("stop")

    async def top(self, symbol: str) -> Top:
        return Top(self.bid, self.ask)

    async def position(self, symbol: str) -> float:
        return self.pos

    def _new(self, side: int, size: float, price: float) -> str:
        self.n += 1
        oid = str(self.n)
        self.infos[oid] = OrderInfo(oid, side, price, size)
        return oid

    async def maker(self, symbol: str, side: int, size: float, price: float, reduce_only: bool) -> str:
        self.sent.append("maker")
        oid = self._new(side, size, price)
        if abs(price / self.bid - 1) > self.far_limit:
            self.infos[oid].open, self.infos[oid].note = False, "21734: too far from the mark price"
        else:
            self.book[oid] = {"client_order_index": int(oid), "type": "limit", "status": "open",
                              "order_expiry": int((self.now + 330) * 1000), "_expiry": self.now + 330}
        return oid

    async def taker(self, symbol: str, side: int, size: float, worst: float, reduce_only: bool) -> str:
        self.sent.append("taker")
        oid = self._new(side, size, worst)
        px = self.ask if side == BUY else self.bid
        self.pos = round(self.pos + side * size, 6)
        self.equity -= 0.01 * size
        o = self.infos[oid]
        o.filled, o.avg_px, o.open, o.note = size, px, False, "filled"
        return oid

    async def cancel(self, symbol: str, order_id: str) -> None:
        self.sent.append("cancel")
        if self.book.pop(order_id, None) is not None:
            self.infos[order_id].open, self.infos[order_id].note = False, "canceled"

    async def order(self, symbol: str, order_id: str) -> OrderInfo | None:
        return self.infos.get(order_id)

    async def cancel_all(self, symbol: str) -> None:
        self.sent.append("cancel_all")
        for oid in list(self.book):
            self.book.pop(oid)
            if oid in self.infos:
                self.infos[oid].open = False

    async def set_stops(self, symbol: str, position: float, stop: float, take: float) -> bool:
        self.sent.append("stops")
        for kind in ("stop-loss", "take-profit")[:self.stops_listed]:
            self.n += 1
            self.book[str(self.n)] = {"client_order_index": self.n, "type": kind, "status": "pending"}
        return self.stops_listed == 2


def make(**kw: Any) -> tuple[lt.LegTest, Leg, list[str]]:
    v = Leg(**kw)
    async def account(index: int) -> dict[str, Any]:
        return await v.account_()

    v.rest = SimpleNamespace(active_orders=v.active_orders, account=account)   # type: ignore[assignment]
    said: list[str] = []
    return lt.LegTest(v, "SPY", say=said.append, sleep=v.sleep, clock=v.clock), v, said


async def test_the_lighter_leg_passes_every_step_and_ends_flat() -> None:
    t, v, _said = make()
    rep = await t.run()
    r = {a: b for a, b, _ in rep.steps}
    notes = {a: c for a, _, c in rep.steps}
    assert not rep.failed, rep.failed
    for name in ("connect", "maker order", "replaced as the engine does", "cancel", "taker order opens a position",
                 "stop and take-profit", "cancel-all", "taker order closes it", "end"):
        assert r[name] == lt.PASS, (name, notes.get(name))
    assert "330 s to live" in notes["maker order"]          # the expiry that clears a dead program's order
    assert rep.clean and v.pos == 0 and not v.book and v.sent[-1] == "stop"
    assert 0 < rep.equity_start - rep.equity_end < 0.05                    # the spread on one minimum order, twice
    assert "Ended flat with no orders." in rep.text() and "4242" in rep.text()


async def test_it_touches_nothing_on_an_account_that_already_holds_something() -> None:
    t, v, _said = make()
    v.pos = 0.5
    rep = await t.run()
    assert "Nothing was sent" in rep.aborted and v.sent == ["stop"] and v.pos == 0.5 and rep.clean


async def test_a_stop_pair_lighter_lists_only_half_of_is_a_failure_and_the_expiry_is_seen() -> None:
    t, v, _said = make(stops_listed=1)
    t.expiry = True
    rep = await t.run()
    r = {a: b for a, b, _ in rep.steps}
    assert r["stop and take-profit"] == lt.FAIL and r["expiry seen by the adapter"] == lt.PASS
    assert rep.clean and v.pos == 0 and not v.book                         # whatever happened, nothing is left
    assert "canceled-expired" in {a: c for a, _, c in rep.steps}["expiry seen by the adapter"]
    assert "Lighter leg on SPY" in lt.plan_text("SPY", True, 1.0) and "6 minutes" in lt.plan_text("SPY", True, 1.0)
