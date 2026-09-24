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
