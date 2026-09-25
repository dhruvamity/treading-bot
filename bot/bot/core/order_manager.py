"""Order manager (P2 task 3): desired book -> the fewest venue actions the budget allows.

`plan()` is PURE (inputs -> actions) so the same code runs in paper and live, and is property-tested:
- match live orders to desired ones by (side, tag); keep a live order if its price is within the requote
  tolerance (max(min_ticks, frac x half-spread) x governor multiplier) and size within 20%;
- otherwise cancel + place, or modify where the venue allows it (ArcusAdapter.use_modify) and the order's venue id
  is known: Arcus refused every modify sent by clientId alone (2026-09-25);
- unmatched live orders are cancelled; in-flight (unacked) orders are never touched twice. An order counts as in
  flight from just before its request is sent until the venue's first update for it, or IN_FLIGHT_MAX_S at most: on a
  fast venue the update can arrive before the request returns, and an order must never stay frozen because of that.
  An order the venue never acknowledged (still PENDING_NEW past that window) is left alone and a reconciliation is
  asked for: modifying it by clientId would only get "order could not be found", every tick;
- a modify or cancel the venue refuses is not retried on the next tick: the order is left alone for IN_FLIGHT_MAX_S
  and the adapter is asked to reconcile (the order may be gone, or its id unknown);
- a post-only order that would cross the CURRENT BBO is re-priced one tick behind the touch, never sent crossing;
- venue minimums (Arcus $5 opening and min size), max size and per-market open-order caps are enforced;
- actions are ordered cancels -> modifies -> places.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum

from bot.common.errors import PreTradeReject, RateLimited
from bot.common.ids import ClientIdFactory
from bot.common.logging import DecisionLog, Log
from bot.venues.base import TIF, Market, OrderRequest, OrderStatus, Side, Venue

log = Log("order_manager")
IN_FLIGHT_MAX_S = 5.0   # an order with no venue update after this long is managed again (a lost ack must not freeze it)


@dataclass(frozen=True, slots=True)
class DesiredOrder:
    side: Side
    price_ticks: int
    size_quantums: int
    tag: str
    post_only: bool = True
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class LiveOrderView:
    client_id: str
    side: Side
    price_ticks: int
    size_quantums: int  # remaining
    tag: str
    reduce_only: bool = False
    in_flight: bool = False
    has_venue_id: bool = True   # modify goes by the venue's order id; without it, requote with cancel + place


class ActionKind(StrEnum):
    PLACE = "place"
    MODIFY = "modify"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    reason: str
    desired: DesiredOrder | None = None
    client_id: str | None = None  # modify / cancel target


@dataclass(frozen=True, slots=True)
class PlanParams:
    min_ticks: int = 2
    tol_ticks: float = 2.0  # already includes max(min_ticks, frac*h) x governor multiplier
    size_frac: float = 0.20
    allow_modify: bool = True
    max_open_per_market: int = 30
    allow_places: bool = True  # False under CANCELS_ONLY budget mode
    allow_modifies: bool = True


@dataclass(frozen=True, slots=True)
class BBOTicks:
    bid: int | None
    ask: int | None


def _post_only_fix(d: DesiredOrder, bbo: BBOTicks) -> DesiredOrder | None:
    if not d.post_only:
        return d
    if d.side is Side.BUY and bbo.ask is not None and d.price_ticks >= bbo.ask:
        p = bbo.ask - 1
        return DesiredOrder(d.side, p, d.size_quantums, d.tag, d.post_only, d.reduce_only) if p > 0 else None
    if d.side is Side.SELL and bbo.bid is not None and d.price_ticks <= bbo.bid:
        return DesiredOrder(d.side, bbo.bid + 1, d.size_quantums, d.tag, d.post_only, d.reduce_only)
    return d


def meets_minimum(d: DesiredOrder, m: Market) -> bool:
    size = d.size_quantums * m.step_size
    if d.reduce_only and m.venue is Venue.ARCUS:
        return size >= m.min_size  # Arcus: reduce-only orders are exempt from the $5 notional minimum
    price = d.price_ticks * m.tick_size
    return size >= m.min_size and size * price >= m.min_notional


def plan(desired: Sequence[DesiredOrder], live: Sequence[LiveOrderView], m: Market, bbo: BBOTicks,
         p: PlanParams) -> list[Action]:
    cancels: list[Action] = []
    modifies: list[Action] = []
    places: list[Action] = []
    by_key: dict[tuple[Side, str], list[LiveOrderView]] = {}
    for lo in live:
        by_key.setdefault((lo.side, lo.tag), []).append(lo)
    used: set[str] = set()

    fixed: list[DesiredOrder] = []
    for d in desired:
        if d.size_quantums <= 0:
            continue
        if m.max_size is not None:
            max_q = int(m.max_size / m.step_size)
            if d.size_quantums > max_q:
                d = DesiredOrder(d.side, d.price_ticks, max_q, d.tag, d.post_only, d.reduce_only)
        fd = _post_only_fix(d, bbo)
        if fd is None or not meets_minimum(fd, m):
            continue
        fixed.append(fd)

    for d in fixed:
        cands = [lo for lo in by_key.get((d.side, d.tag), []) if lo.client_id not in used]
        if not cands:
            places.append(Action(ActionKind.PLACE, f"new level {d.tag}", desired=d))
            continue
        lo = min(cands, key=lambda x: abs(x.price_ticks - d.price_ticks))
        used.add(lo.client_id)
        if lo.in_flight:
            continue
        dp = abs(lo.price_ticks - d.price_ticks)
        ds = abs(lo.size_quantums - d.size_quantums) / max(1, d.size_quantums)
        if dp <= p.tol_ticks and ds <= p.size_frac and lo.reduce_only == d.reduce_only:
            continue  # within hysteresis: keep queue position and budget
        if p.allow_modify and p.allow_modifies and lo.has_venue_id and lo.reduce_only == d.reduce_only:
            modifies.append(Action(ActionKind.MODIFY, f"requote {d.tag}: dp={dp} ticks ds={ds:.0%}", desired=d,
                                   client_id=lo.client_id))
        elif p.allow_places:
            cancels.append(Action(ActionKind.CANCEL, f"replace {d.tag}", client_id=lo.client_id))
            places.append(Action(ActionKind.PLACE, f"replace {d.tag}", desired=d))
        # else: requotes frozen (CANCELS_ONLY) -> leave the resting order as it is

    for lo in live:
        if lo.client_id not in used and not lo.in_flight:
            cancels.append(Action(ActionKind.CANCEL, f"not in desired book ({lo.tag})", client_id=lo.client_id))

    if not p.allow_places:
        places = []
    if not p.allow_modifies:
        modifies = []
    open_after = len(live) - len(cancels) + len(places)
    if open_after > p.max_open_per_market:
        drop = open_after - p.max_open_per_market
        places = places[: max(0, len(places) - drop)]
    return cancels + modifies + places


# ------------------------------------------------------------------------------------------------------
# Executor
# ------------------------------------------------------------------------------------------------------
@dataclass
class SyncResult:
    actions: list[Action] = field(default_factory=list)
    rejected: list[tuple[Action, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class OrderManager:
    """Owns client ids and turns plans into adapter calls through the risk engine (pre-trade checks)."""

    def __init__(self, *, adapters: dict[Venue, object], state: object, risk: object, governor: object,
                 decisions: DecisionLog, ids: dict[Venue, ClientIdFactory], session: str = "",
                 account_index: dict[Venue, int] | None = None, now_fn: Callable[[], int] | None = None) -> None:
        self.adapters = adapters
        self.state = state
        self.risk = risk
        self.governor = governor
        self.decisions = decisions
        self.ids = ids
        self.session = session
        self.account_index = account_index or {}
        self.in_flight: dict[str, int] = {}   # client id -> when its request was sent (us)
        self.now_fn = now_fn
        self._resync_at: dict[Venue, int] = {}
        self.backoff_until: dict[tuple[Venue, str], int] = {}   # (venue, "order" | "cancel") -> us: a 429's retry hint

    def _dec(self, kind: str, reason: str, **kw: object) -> None:
        self.decisions.record(kind, reason, ts_us=self.now_fn() if self.now_fn else None, **kw)  # type: ignore[arg-type]

    def _now(self) -> int:
        t = self.now_fn() if self.now_fn else 0
        return t or int(time.time() * 1e6)

    def is_in_flight(self, client_id: str) -> bool:
        sent = self.in_flight.get(client_id)
        if sent is None:
            return False
        if self._now() - sent > IN_FLIGHT_MAX_S * 1e6:
            self.in_flight.pop(client_id, None)
            log.warning("in_flight_expired", data={"client_id": client_id})
            return False
        return True

    def _mark(self, client_ids: Sequence[str]) -> None:
        now = self._now()
        for c in client_ids:
            self.in_flight[c] = now

    def _rate_limited(self, venue: Venue, pool: str, e: Exception) -> None:
        """A 429 on an order write: wait the venue's retryAfterMs (docs: rate-limits, "Handling rate limits") before
        sending on that pool again, instead of retrying every tick."""
        if isinstance(e, RateLimited):
            wait_us = max(1000, int(e.retry_after_ms)) * 1000
            pools = ("order", "cancel") if e.reason in (None, "ip") else (pool,)   # the IP bucket blocks both
            for p in pools:
                self.backoff_until[(venue, p)] = max(self.backoff_until.get((venue, p), 0), self._now() + wait_us)
            log.warning("rate_limited", venue=venue.value, reason=str(e.reason), data={"pool": pool,
                                                                                       "retry_after_ms": e.retry_after_ms})

    def backing_off(self, venue: Venue, pool: str) -> bool:
        return self._now() < self.backoff_until.get((venue, pool), 0)

    def resync(self, venue: Venue, why: str) -> None:
        """Ask the runner to reconcile this venue now (its loop checks every 5 s); at most once per IN_FLIGHT_MAX_S."""
        now = self._now()
        if now - self._resync_at.get(venue, 0) < IN_FLIGHT_MAX_S * 1e6:
            return
        self._resync_at[venue] = now
        ad = self.adapters.get(venue)
        if ad is not None:
            ad.resync_requested = True  # type: ignore[attr-defined]
        log.warning("resync_requested", venue=venue.value, reason=why)

    def new_client_id(self, venue: Venue) -> str:
        return self.ids[venue].arcus()

    def live_view(self, venue: Venue, m: Market) -> list[LiveOrderView]:
        out = []
        for o in self.state.open_orders(venue, m.base):  # type: ignore[attr-defined]
            r = o.req
            if r.tif not in (TIF.POST_ONLY, TIF.GTT):
                continue
            rem = o.remaining
            busy = self.is_in_flight(r.client_id)
            if not busy and o.status is OrderStatus.PENDING_NEW:   # no ack yet: let reconciliation say what it is
                busy = True
                self.resync(venue, f"no venue ack for {r.client_id}")
            out.append(LiveOrderView(r.client_id, r.side, int(r.price / m.tick_size), int(rem / m.step_size), r.tag,
                                     r.reduce_only, busy, o.venue_order_id is not None))
        return out

    def to_request(self, venue: Venue, m: Market, d: DesiredOrder, reason: str, client_id: str | None = None) -> OrderRequest:
        price = m.round_price(Decimal(d.price_ticks) * m.tick_size, is_bid=d.side is Side.BUY)
        return OrderRequest(venue=venue, base=m.base, side=d.side, price=price, size=Decimal(d.size_quantums) * m.step_size,
                            tif=TIF.POST_ONLY if d.post_only else TIF.GTT, reduce_only=d.reduce_only,
                            client_id=client_id or self.new_client_id(venue), tag=d.tag, reason=reason,
                            account_index=self.account_index.get(venue))

    async def sync(self, venue: Venue, m: Market, desired: Sequence[DesiredOrder], bbo: BBOTicks, params: PlanParams,
                   *, why: str) -> SyncResult:
        res = SyncResult()
        if self.backing_off(venue, "order"):   # the order pool (or the IP bucket) said wait: leave resting orders be
            params = replace(params, allow_places=False, allow_modifies=False)
        actions = plan(desired, self.live_view(venue, m), m, bbo, params)
        if self.backing_off(venue, "cancel"):
            actions = [a for a in actions if a.kind is not ActionKind.CANCEL]
        if not actions:
            return res
        adapter = self.adapters[venue]
        cancels = [a for a in actions if a.kind is ActionKind.CANCEL]
        modifies = [a for a in actions if a.kind is ActionKind.MODIFY]
        places = [a for a in actions if a.kind is ActionKind.PLACE]
        if cancels:
            ids = [a.client_id for a in cancels if a.client_id]
            for a in cancels:
                self._dec("cancel", f"{why}: {a.reason}", venue=venue.value, market=m.base,
                                      session=self.session, client_id=a.client_id)
            self._mark(ids)   # before sending: the venue's update may arrive before the request returns
            try:
                await adapter.cancel(ids)  # type: ignore[attr-defined]
                res.actions += cancels
            except Exception as e:   # refused: leave them for IN_FLIGHT_MAX_S and let reconciliation say what is left
                res.errors.append(f"cancel: {e}")
                self._rate_limited(venue, "cancel", e)
                self.resync(venue, f"cancel refused: {str(e)[:120]}")
        for a in modifies:
            assert a.desired is not None and a.client_id is not None
            req = self.to_request(venue, m, a.desired, f"{why}: {a.reason}", a.client_id)
            try:
                self.risk.check(req, m, modify=True)  # type: ignore[attr-defined]
            except PreTradeReject as e:
                res.rejected.append((a, str(e)))
                self._dec("reject_pretrade", str(e), venue=venue.value, market=m.base,
                                      session=self.session, tag=req.tag, side=req.side.value)
                continue
            self._dec("modify", req.reason, venue=venue.value, market=m.base, session=self.session,
                                  client_id=a.client_id, price=str(req.price), size=str(req.size))
            try:
                await adapter.modify(a.client_id, req.price, req.size)  # type: ignore[attr-defined]
                self.state.on_modify_intent(a.client_id, req.price, req.size, req.reason)  # type: ignore[attr-defined]
                res.actions.append(a)
            except Exception as e:   # refused (e.g. not found): no retry every tick; reconcile instead
                res.errors.append(f"modify {a.client_id}: {e}")
                self._rate_limited(venue, "order", e)
                self._mark([a.client_id])
                self.resync(venue, f"modify refused: {str(e)[:120]}")
        reqs: list[OrderRequest] = []
        for a in places:
            assert a.desired is not None
            req = self.to_request(venue, m, a.desired, f"{why}: {a.reason}")
            try:
                self.risk.check(req, m)  # type: ignore[attr-defined]
            except PreTradeReject as e:
                res.rejected.append((a, str(e)))
                self._dec("reject_pretrade", str(e), venue=venue.value, market=m.base,
                                      session=self.session, tag=req.tag, side=req.side.value)
                continue
            reqs.append(req)
            self.state.on_intent(req, self.session)  # type: ignore[attr-defined]
            self._dec("place", req.reason, venue=venue.value, market=m.base, session=self.session,
                                  client_id=req.client_id, side=req.side.value, price=str(req.price),
                                  size=str(req.size), tif=req.tif.value)
        if reqs:
            self._mark([r.client_id for r in reqs])   # before sending, as for cancels
            try:
                await adapter.place(reqs)  # type: ignore[attr-defined]
                res.actions += places
            except Exception as e:
                res.errors.append(f"place: {e}")
                self._rate_limited(venue, "order", e)
                for r in reqs:
                    self.state.event("place_failed", venue=venue.value, client_id=r.client_id, err=str(e)[:200])  # type: ignore[attr-defined]
        self._record_budget(venue, len(places), len(modifies), len(cancels))
        return res

    def _record_budget(self, venue: Venue, n_place: int, n_modify: int, n_cancel: int) -> None:
        ag = self.governor.for_arcus(self.account_index.get(venue, 0))  # type: ignore[attr-defined]
        if n_place + n_modify:
            ag.record_actions(n_place + n_modify, "place")
        if n_cancel:
            ag.record_actions(n_cancel, "cancel")

    def on_order_update(self, client_id: str, terminal_or_acked: bool) -> None:
        if terminal_or_acked:
            self.in_flight.pop(client_id, None)

    async def ioc(self, req: OrderRequest, m: Market) -> None:
        """An IOC exit, cut or flatten: passes risk checks, never post-only, logged with its reason."""
        self.risk.check(req, m)  # type: ignore[attr-defined]
        self.state.on_intent(req, self.session)  # type: ignore[attr-defined]
        self._dec("ioc", req.reason, venue=req.venue.value, market=m.base, session=self.session,
                              client_id=req.client_id, side=req.side.value, price=str(req.price), size=str(req.size))
        await self.adapters[req.venue].place([req])  # type: ignore[attr-defined]
