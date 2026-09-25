"""C3: order-manager property tests - never a crossing post-only order, never above per-market caps, minimal actions."""

from __future__ import annotations

import random
from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from bot.core.order_manager import ActionKind, BBOTicks, DesiredOrder, LiveOrderView, PlanParams, plan
from bot.venues.base import Side, Venue
from tests.helpers import fixture_markets

M = fixture_markets()[Venue.ARCUS]["BTC"]  # tick 0.1, step 1e-8, min 0.0001 BTC / $5
MID = 860_000  # ticks (= $86,000)
Q = 12_000  # quantums (0.00012 BTC ~ $10.3)


def d(side: Side, px: int, tag: str, q: int = Q) -> DesiredOrder:
    return DesiredOrder(side, px, q, tag)


def lo(cid: str, side: Side, px: int, tag: str, q: int = Q, in_flight: bool = False) -> LiveOrderView:
    return LiveOrderView(cid, side, px, q, tag, False, in_flight)


def test_keep_within_hysteresis_and_modify_outside() -> None:
    bbo = BBOTicks(MID - 1, MID + 1)
    live = [lo("a", Side.BUY, MID - 50, "b0"), lo("b", Side.SELL, MID + 50, "a0")]
    same = plan([d(Side.BUY, MID - 51, "b0"), d(Side.SELL, MID + 50, "a0")], live, M, bbo, PlanParams(tol_ticks=2))
    assert same == []
    moved = plan([d(Side.BUY, MID - 60, "b0"), d(Side.SELL, MID + 50, "a0")], live, M, bbo, PlanParams(tol_ticks=2))
    assert [a.kind for a in moved] == [ActionKind.MODIFY] and moved[0].client_id == "a"


def test_unmatched_live_cancelled_new_placed_and_order() -> None:
    bbo = BBOTicks(MID - 1, MID + 1)
    acts = plan([d(Side.BUY, MID - 30, "b1")], [lo("x", Side.BUY, MID - 50, "b0")], M, bbo, PlanParams())
    assert [a.kind for a in acts] == [ActionKind.CANCEL, ActionKind.PLACE]


def test_post_only_never_crosses() -> None:
    bbo = BBOTicks(MID - 1, MID + 1)
    acts = plan([d(Side.BUY, MID + 5, "b0"), d(Side.SELL, MID - 5, "a0")], [], M, bbo, PlanParams())
    for a in acts:
        assert a.desired is not None
        if a.desired.side is Side.BUY:
            assert a.desired.price_ticks < MID + 1
        else:
            assert a.desired.price_ticks > MID - 1


def test_minimums_and_in_flight() -> None:
    bbo = BBOTicks(MID - 1, MID + 1)
    tiny = plan([d(Side.BUY, MID - 10, "b0", q=5_000)], [], M, bbo, PlanParams())  # 0.00005 BTC < min size
    assert tiny == []
    inflight = plan([d(Side.BUY, MID - 90, "b0")], [lo("a", Side.BUY, MID - 10, "b0", in_flight=True)], M, bbo,
                    PlanParams())
    assert inflight == []  # never act twice on an unacked order


def test_cancels_only_mode_keeps_resting_quotes() -> None:
    bbo = BBOTicks(MID - 1, MID + 1)
    p = PlanParams(allow_places=False, allow_modifies=False)
    acts = plan([d(Side.BUY, MID - 90, "b0")], [lo("a", Side.BUY, MID - 10, "b0"), lo("z", Side.SELL, MID + 9, "a9")],
                M, bbo, p)
    assert [(a.kind, a.client_id) for a in acts] == [(ActionKind.CANCEL, "z")]


@settings(max_examples=300, deadline=None)
@given(st.integers(0, 10_000))
def test_random_sequences_properties(seed: int) -> None:
    rng = random.Random(seed)
    live: list[LiveOrderView] = []
    nid = 0
    p = PlanParams(tol_ticks=rng.choice([1, 2, 5]), max_open_per_market=rng.choice([4, 10, 30]))
    for _ in range(20):
        mid = MID + rng.randint(-200, 200)
        bbo = BBOTicks(mid - rng.randint(1, 3), mid + rng.randint(1, 3))
        n = rng.randint(0, 6)
        desired = []
        for i in range(n):
            side = Side.BUY if i % 2 == 0 else Side.SELL
            off = rng.randint(-5, 60)
            px = mid - off if side is Side.BUY else mid + off
            desired.append(d(side, px, f"{side.value[0]}{i // 2}", Q + rng.randint(-100, 3000)))
        acts = plan(desired, live, M, bbo, p)
        # 1) post-only never crosses the current BBO
        for a in acts:
            if a.kind in (ActionKind.PLACE, ActionKind.MODIFY):
                assert a.desired is not None
                if a.desired.side is Side.BUY:
                    assert a.desired.price_ticks < (bbo.ask or 10**12)
                else:
                    assert a.desired.price_ticks > (bbo.bid or -1)
        # 2) per-market open cap respected
        cancels = {a.client_id for a in acts if a.kind is ActionKind.CANCEL}
        places = [a for a in acts if a.kind is ActionKind.PLACE]
        assert len(live) - len(cancels) + len(places) <= max(p.max_open_per_market, len(live) - len(cancels))
        # 3) minimality: never more actions than (live + desired), and no action on a kept order
        assert len(acts) <= len(live) + len(desired) * 2
        assert len({a.client_id for a in acts if a.client_id}) == len([a for a in acts if a.client_id])
        # apply
        new_live = [x for x in live if x.client_id not in cancels]
        mods = {a.client_id: a.desired for a in acts if a.kind is ActionKind.MODIFY}
        new_live = [LiveOrderView(x.client_id, x.side, mods[x.client_id].price_ticks, mods[x.client_id].size_quantums,
                                  x.tag) if x.client_id in mods else x for x in new_live]  # type: ignore[union-attr]
        for a in places:
            assert a.desired is not None
            nid += 1
            new_live.append(LiveOrderView(f"c{nid}", a.desired.side, a.desired.price_ticks, a.desired.size_quantums,
                                          a.desired.tag))
        live = new_live
        # 4) idempotence: planning the same desired book again changes nothing that was just synced
        again = plan(desired, live, M, bbo, p)
        assert all(a.kind is not ActionKind.MODIFY for a in again)


def test_max_size_clip() -> None:
    from dataclasses import replace

    m = replace(M, max_size=D("0.0002"))
    acts = plan([d(Side.BUY, MID - 10, "b0", q=50_000)], [], m, BBOTicks(MID - 1, MID + 1), PlanParams())
    assert acts[0].desired is not None and acts[0].desired.size_quantums == 20_000


# ------------------------------------------------------------------------------------------------ sync races
class _Race:
    """An adapter whose venue update for an order is handled BEFORE the request returns (what a fast venue does:
    the WebSocket ack beats the REST response). The first live QQQ run froze every order this way."""

    def __init__(self, state: object) -> None:
        self.state = state
        self.om: object = None
        self.sent: list[str] = []

    def _ack(self, cid: str, status: object) -> None:
        from bot.venues.base import OrderState, OrderStatus

        o = self.state.orders[cid]  # type: ignore[attr-defined]
        st = OrderState(cid, "v" + cid, status, D(0), None, None, 0, Venue.ARCUS, o.req.base, o.req.side,  # type: ignore[arg-type]
                        o.req.price, o.req.size, o.req.tif, o.req.reduce_only, o.req.tag)
        assert isinstance(status, OrderStatus)
        self.state.on_update(st)  # type: ignore[attr-defined]
        self.om.on_order_update(cid, True)  # type: ignore[attr-defined]

    async def place(self, reqs: list[object]) -> list[object]:
        from bot.venues.base import OrderStatus

        for r in reqs:
            self.sent.append(f"place {r.client_id}")  # type: ignore[attr-defined]
            self._ack(r.client_id, OrderStatus.OPEN)  # type: ignore[attr-defined]
        return []

    async def modify(self, cid: str, price: D, size: D) -> None:
        self.sent.append(f"modify {cid}")

    async def cancel(self, ids: list[str]) -> None:
        from bot.venues.base import OrderStatus

        for c in ids:
            self.sent.append(f"cancel {c}")
            self._ack(c, OrderStatus.CANCELED)


def _om(tmp_path: object, adapter_cls: type = _Race) -> tuple[object, object, list[int]]:
    from pathlib import Path
    from types import SimpleNamespace

    from bot.common.ids import ClientIdFactory
    from bot.core.order_manager import OrderManager
    from bot.core.state import StateStore

    state = StateStore(Path(str(tmp_path)) / "s.sqlite")
    ad = adapter_cls(state)
    clock = [1_000_000_000]
    gov = SimpleNamespace(for_arcus=lambda ai: SimpleNamespace(record_actions=lambda n, k: None))
    om = OrderManager(adapters={Venue.ARCUS: ad}, state=state, risk=SimpleNamespace(check=lambda *a, **k: None),
                      governor=gov, decisions=SimpleNamespace(record=lambda *a, **k: None),  # type: ignore[arg-type]
                      ids={Venue.ARCUS: ClientIdFactory("mid", 1)}, now_fn=lambda: clock[0])
    ad.om = om
    return om, ad, clock


async def test_an_ack_that_beats_the_response_does_not_freeze_the_order(tmp_path: object) -> None:
    om, ad, _ = _om(tmp_path)
    bbo = BBOTicks(MID - 1, MID + 1)
    p = PlanParams(tol_ticks=2)
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 20, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    cid = ad.sent[0].split()[1]  # type: ignore[attr-defined]
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 80, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    assert ad.sent[-1] == f"modify {cid}"  # type: ignore[attr-defined]  # requoted, not left resting
    await om.sync(Venue.ARCUS, M, [], bbo, p, why="t")  # type: ignore[attr-defined]
    assert ad.sent[-1] == f"cancel {cid}"  # type: ignore[attr-defined]  # and cancelled when no longer wanted


class _Silent(_Race):
    """A venue that never sends an update for the order (a lost ack)."""

    async def place(self, reqs: list[object]) -> list[object]:
        self.sent += [f"place {r.client_id}" for r in reqs]  # type: ignore[attr-defined]
        return []


async def test_a_lost_ack_is_reconciled_not_modified_blindly(tmp_path: object) -> None:
    """No venue ack: after the in-flight window the order is still left alone (modifying an order that may never
    have landed only gets "order could not be found", every tick) and a reconciliation is asked for. Once the venue
    lists it, it is managed as usual."""
    from bot.core.order_manager import IN_FLIGHT_MAX_S
    from bot.venues.base import OrderState, OrderStatus

    om, ad, clock = _om(tmp_path, _Silent)
    bbo = BBOTicks(MID - 1, MID + 1)
    p = PlanParams(tol_ticks=2)
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 20, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    cid = ad.sent[0].split()[1]  # type: ignore[attr-defined]
    clock[0] += int((IN_FLIGHT_MAX_S + 1) * 1e6)
    for _ in range(3):
        await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 80, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    assert ad.sent == [f"place {cid}"] and getattr(ad, "resync_requested", False)  # type: ignore[attr-defined]
    o = ad.state.orders[cid]  # type: ignore[attr-defined]
    ad.state.on_update(OrderState(cid, "v1", OrderStatus.OPEN, D(0), None, None, 0, Venue.ARCUS, o.req.base,  # type: ignore[attr-defined]
                                  o.req.side, o.req.price, o.req.size, o.req.tif, False, o.req.tag))   # reconciled
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 80, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    assert ad.sent[-1] == f"modify {cid}"  # type: ignore[attr-defined]


class _Refuses(_Race):
    """A venue that acks the order but refuses every modify (the live "order could not be found")."""

    async def modify(self, cid: str, price: D, size: D) -> None:
        self.sent.append(f"modify {cid}")
        raise RuntimeError("Order could not be found")


async def test_a_refused_modify_is_not_retried_every_tick(tmp_path: object) -> None:
    from bot.core.order_manager import IN_FLIGHT_MAX_S

    om, ad, clock = _om(tmp_path, _Refuses)
    bbo = BBOTicks(MID - 1, MID + 1)
    p = PlanParams(tol_ticks=2)
    await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 20, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    for _ in range(10):                                  # ten ticks, one second apart
        clock[0] += 1_000_000
        await om.sync(Venue.ARCUS, M, [d(Side.BUY, MID - 80, "b0")], bbo, p, why="t")  # type: ignore[attr-defined]
    mods = [x for x in ad.sent if x.startswith("modify")]  # type: ignore[attr-defined]
    assert len(mods) == 2 and getattr(ad, "resync_requested", False)   # once, then once more after the back-off
    assert len(mods) <= 10 / IN_FLIGHT_MAX_S
