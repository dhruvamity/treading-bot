"""Backtest one setup on one market's recorded tape, with the running bot's own quoting (lbot/trade/strategy.py) and
stops (lbot/trade/guard.py).

What it models, and why (Lighter standard account):
- Decisions twice a second (the bot's loop). An order, a modify or a cancel takes effect `maker_us` later: Lighter's
  200 ms speed bump on maker orders plus the network. Until then the old order keeps filling.
- A post-only order that would cross the book when it lands is cancelled by Lighter (status canceled-post-only).
- Requests: one requote (both sides in one batch) is one request; the bot may send at most `quotes_per_min` of them
  in any rolling minute. A requote that does not fit is skipped: the orders stay where they are.
- Fills (queue model, with the recorded depth): an order that improves the best price has nothing ahead of it; an
  order that joins a price waits behind the size shown there when it landed (never more than is shown later); a
  taker transaction fills it for what that transaction printed at our price beyond the queue, plus everything it
  printed through our price (with our order there, the taker would have reached us first).
- Taker orders (stops, exits) land `taker_us` later and walk the recorded depth at that moment. Fees: Lighter's
  standard account charges none (the market's fees are used, 0 today).
- Funding is paid each hour on the position at the recorded rate. Liquidation below the maintenance margin.
- A gap in the recording over a minute pulls all quotes (the running bot would see a stale feed).
The recorded market does not react to our orders: a quote inside the spread is assumed not to be stepped in front of,
so setups that quote inside the spread are the least certain (docs/RESEARCH.md measures how much this matters).
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from lbot.scout.tape import DayTape
from lbot.trade import guard as G
from lbot.trade.sizing import Sizes
from lbot.trade.strategy import BP, BUY, SELL, Params, Plan, Quoter, Rules, View

US = 1_000_000
NEVER = 1 << 62


@dataclass(frozen=True)
class MarketRules:
    tick: float
    step: float
    min_base: float
    min_quote: float
    mmf: float = 0.012           # maintenance margin fraction
    maker_fee: float = 0.0
    taker_fee: float = 0.0

    def min_usd(self, px: float) -> float:
        return max(self.min_quote, self.min_base * px)


@dataclass(frozen=True)
class SimCfg:
    maker_us: int = 280_000       # 200 ms speed bump + ~80 ms network
    taker_us: int = 380_000
    step_us: int = 500_000        # the bot's loop (twice a second)
    quotes_per_min: int = 54
    exit_taker_after_s: float = 20.0
    cooldown_s: float = 60.0
    gap_s: float = 60.0
    warmup_s: float = 120.0
    slip_bps: float = 5.0         # extra cost for a taker order bigger than the recorded depth
    queue: bool = True            # False: only trades through our price fill us (a lower bound)
    inside_ahead: float = 0.0     # an order better than the best price: this share of the best level's size is
                                  # assumed ahead of it (0: nothing; 1: as if the others stepped up with us at once)
    markouts: tuple[int, ...] = (1, 10, 60)


@dataclass
class Result:
    market: str
    setup: str
    start_us: int
    end_us: int
    hours: float = 0.0
    quoting_s: float = 0.0
    maker_fills: int = 0
    maker_usd: float = 0.0
    taker_fills: int = 0
    taker_usd: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    pnl: float = 0.0              # marked at mid, less the cost of closing what is left at the end
    min_pnl: float = 0.0
    pos_stops: int = 0
    day_stops: int = 0
    killed: bool = False
    liquidated: bool = False
    requests: int = 0             # requote batches sent
    skipped: int = 0              # requotes the request budget did not allow
    rejects: int = 0              # post-only orders that would have crossed on arrival
    max_pos_usd: float = 0.0
    end_pos_usd: float = 0.0
    edge_bps: float = 0.0         # maker fills: price vs the mid at the fill (weighted by $)
    markout_bps: dict[str, float] = field(default_factory=dict)   # maker fills: gain vs the mid N s later
    hourly_usd: list[float] = field(default_factory=lambda: [0.0] * 24)
    hourly_pnl: list[float] = field(default_factory=lambda: [0.0] * 24)
    tail_pnl: float = 0.0         # the last `tail_s` of the window
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def volume(self) -> float:
        return self.maker_usd + self.taker_usd

    @property
    def cost_bps(self) -> float:
        """Dollars lost per dollar traded, in bp (negative = a profit)."""
        return -self.pnl / self.volume / BP if self.volume > 0 else 0.0


@dataclass
class _Order:
    side: int
    px: float
    qty: float
    tag: str
    live_from: int
    reduce_only: bool = False
    cancel_at: int = NEVER
    ahead: float = -1.0           # queue ahead at our price; -1 until it lands


class Window:
    """One market's tape over [start, end) prepared once and shared by every setup: per decision step, the best
    bid/offer, its sizes and whether the feed was alive; the trades grouped by taker transaction; the depth rows."""

    def __init__(self, tape: DayTape, start_us: int, end_us: int, cfg: SimCfg | None = None,
                 prev: DayTape | None = None) -> None:
        cfg = cfg or SimCfg()
        self.inside_ahead = cfg.inside_ahead
        self.market = tape.market
        self.start_us, self.end_us = start_us, end_us
        b, tr, dp, stt = tape.bbo, tape.trades, tape.depth, tape.stats
        if prev is not None and len(prev.bbo["ts"]):
            b = {k: np.concatenate([prev.bbo[k][-50_000:], b[k]]) for k in b}   # the book as the day opens
        self.bts, self.bbid, self.bask = b["ts"], b["bid"], b["ask"]
        self.bbsz, self.basz = b["bid_sz"], b["ask_sz"]
        w0 = max(start_us - int(cfg.warmup_s * US), int(self.bts[0]) if len(self.bts) else start_us)
        self.w0 = w0 - w0 % cfg.step_us
        self.step = cfg.step_us
        n = self.n = max(0, (end_us - self.w0) // cfg.step_us)
        t = self.t = self.w0 + np.arange(n, dtype=np.int64) * cfg.step_us
        k = np.searchsorted(self.bts, t, side="right") - 1
        have = k >= 0
        kk = np.clip(k, 0, None)
        self.bid = np.where(have, self.bbid[kk], np.nan) if len(self.bts) else np.full(n, np.nan)
        self.ask = np.where(have, self.bask[kk], np.nan) if len(self.bts) else np.full(n, np.nan)
        self.bsz = np.where(have, self.bbsz[kk], 0.0) if len(self.bts) else np.zeros(n)
        self.asz = np.where(have, self.basz[kk], 0.0) if len(self.bts) else np.zeros(n)
        # alive: some row (book or trade) within gap_s
        alive_src = np.sort(np.concatenate([self.bts, tr["ts"]])) if len(tr["ts"]) else self.bts
        ka = np.searchsorted(alive_src, t, side="right") - 1
        last = np.where(ka >= 0, alive_src[np.clip(ka, 0, None)] if len(alive_src) else 0, 0)
        self.ok = have & (self.bid > 0) & (self.ask > self.bid) & ((t - last) <= cfg.gap_s * US)
        self.mid = np.where(self.ok, (self.bid + self.ask) / 2, np.nan)
        # trades grouped by taker transaction, in time order
        self.tts, self.tpx, self.tsz, self.tbuy, self.tgrp = tr["ts"], tr["px"], tr["sz"], tr["buy"], tr["grp"]
        self.tidx = np.searchsorted(self.tts, np.append(t, t[-1] + cfg.step_us if n else 0), side="left")
        # depth rows
        self.dts = dp["ts"]
        self.dbp, self.dbs, self.dap, self.das = dp["bp"], dp["bs"], dp["ap"], dp["as_"]
        # funding: the recorded rate at each hour
        self.fts, self.frate = stt["ts"], stt["funding"]
        self.has_depth = len(self.dts) > 0
        self.didx = (np.searchsorted(self.dts, t, side="right") - 1).tolist() if self.has_depth else [-1] * n
        self._bsz_l, self._asz_l = self.bsz.tolist(), self.asz.tolist()
        self._bid_l, self._ask_l = self.bid.tolist(), self.ask.tolist()

    def shown_now(self, i: int, side: int, px: float, tick: float) -> float:
        """shown_at for decision step i, from the arrays prepared for that step."""
        best, best_sz = (self._bid_l[i], self._bsz_l[i]) if side == BUY else (self._ask_l[i], self._asz_l[i])
        if abs(px - best) <= tick / 2:
            return best_sz
        if (side == BUY and px > best) or (side == SELL and px < best):
            return self.inside_ahead * best_sz
        k = self.didx[i]
        if k < 0:
            return math.inf
        ps, ss = (self.dbp[k], self.dbs[k]) if side == BUY else (self.dap[k], self.das[k])
        j = np.flatnonzero(np.abs(ps - px) <= tick / 2)
        if len(j):
            return float(ss[j[0]])
        deepest = ps[ps > 0]
        if len(deepest) and ((side == BUY and px < deepest.min()) or (side == SELL and px > deepest.max())):
            return math.inf
        return 0.0

    def bbo_at(self, ts: int) -> tuple[float, float, float, float]:
        k = int(np.searchsorted(self.bts, ts, side="right")) - 1
        if k < 0:
            return math.nan, math.nan, 0.0, 0.0
        return float(self.bbid[k]), float(self.bask[k]), float(self.bbsz[k]), float(self.basz[k])

    def shown_at(self, side: int, px: float, ts: int, tick: float) -> float:
        """Size the book showed at `px` on `side` at `ts` (the best from the ticker, deeper levels from depth)."""
        b, a, bs, as_ = self.bbo_at(ts)
        best, best_sz = (b, bs) if side == BUY else (a, as_)
        if abs(px - best) <= tick / 2:
            return best_sz
        if (side == BUY and px > best) or (side == SELL and px < best):
            return self.inside_ahead * best_sz
        if not self.has_depth:
            return math.inf           # unknown: behind the best with no depth recorded
        k = int(np.searchsorted(self.dts, ts, side="right")) - 1
        if k < 0:
            return math.inf
        ps, ss = (self.dbp[k], self.dbs[k]) if side == BUY else (self.dap[k], self.das[k])
        j = np.flatnonzero(np.abs(ps - px) <= tick / 2)
        if len(j):
            return float(ss[j[0]])
        deepest = ps[ps > 0]
        if len(deepest) and ((side == BUY and px < deepest.min()) or (side == SELL and px > deepest.max())):
            return math.inf           # beyond the recorded levels: unknown
        return 0.0

    def taker_price(self, side: int, qty: float, ts: int, slip_bps: float) -> float:
        """The average price a taker order of `qty` gets at `ts`: the recorded depth, then `slip_bps` past it."""
        b, a, bs, as_ = self.bbo_at(ts)
        best = a if side == BUY else b
        if math.isnan(best):
            return math.nan
        levels: list[tuple[float, float]] = []
        if self.has_depth:
            k = int(np.searchsorted(self.dts, ts, side="right")) - 1
            if k >= 0:
                ps, ss = (self.dap[k], self.das[k]) if side == BUY else (self.dbp[k], self.dbs[k])
                levels = [(float(p), float(s)) for p, s in zip(ps, ss, strict=True) if p > 0 and s > 0]
                # the ticker is fresher at the top: never better than the best it shows
                levels = [(p, s) for p, s in levels if (p >= best if side == BUY else p <= best)]
        if not levels:
            levels = [(best, as_ if side == BUY else bs)]
        left, cost = qty, 0.0
        for p, s in levels:
            take = min(left, s)
            cost += take * p
            left -= take
            if left <= 0:
                break
        if left > 0:
            last = levels[-1][0]
            cost += left * last * (1 + side * slip_bps * BP)
        return cost / qty


class Sim:
    def __init__(self, p: Params, sz: Sizes, mr: MarketRules, cfg: SimCfg | None = None, name: str = "") -> None:
        self.p, self.sz, self.mr = p, sz, mr
        self.cfg = cfg or SimCfg()
        self.name = name or f"{p.mode} {p.spread:g}"

    def run(self, w: Window, *, tail_s: int = 6 * 3600) -> Result:
        p, sz, mr, cfg = self.p, self.sz, self.mr, self.cfg
        res = Result(w.market, self.name, w.start_us, w.end_us)
        if not w.n or not w.ok.any():
            res.notes.append("no data")
            return res
        tick, step = mr.tick, mr.step
        quoter = Quoter(p)
        guard = G.Guard(G.Limits(sz.pos_stop_usd, sz.daily_stop_usd, sz.kill_usd, cfg.exit_taker_after_s,
                                 cfg.cooldown_s))
        T, OK, MID = w.t.tolist(), w.ok.tolist(), w.mid.tolist()
        BID, ASK, BSZ, ASZ = w.bid.tolist(), w.ask.tolist(), w.bsz.tolist(), w.asz.tolist()
        TIDX = w.tidx.tolist()
        tts, tpx, tsz, tbuy, tgrp = w.tts, w.tpx, w.tsz, w.tbuy, w.tgrp
        ntr = len(tts)
        lat = cfg.maker_us
        orders: list[_Order] = []
        st: dict[str, Any] = {"pos": 0.0, "entry": None, "cash": 0.0, "since": 0}
        sent: list[int] = []                   # times of requote requests in the last minute
        fills: list[tuple[int, int, float, float, float]] = []   # (t, side, px, usd, mid at fill)
        start_eq: float | None = None
        tail_from = w.end_us - tail_s * US
        tail_eq0: float | None = None
        last_mid = math.nan
        last_fp: tuple[Any, ...] | None = None
        next_fund = (w.start_us // (3600 * US) + 1) * 3600 * US
        fts = w.fts.tolist() if len(w.fts) else []
        frate = w.frate.tolist() if len(w.frate) else []
        hour_eq: float | None = None
        hour_i = -1

        def book(side: int, px: float, qty: float, maker: bool, t: int, tag: str, mid: float) -> None:
            pos, entry = st["pos"], st["entry"]
            fee = qty * px * (mr.maker_fee if maker else mr.taker_fee)
            st["cash"] -= side * qty * px + fee
            res.fees += fee
            new = pos + side * qty
            if pos == 0 or (pos > 0) == (side > 0):
                entry = px if pos == 0 or entry is None else (entry * abs(pos) + px * qty) / (abs(pos) + qty)
            elif (new > 0) != (pos > 0) and abs(new) > step / 2:
                entry = px
            if abs(new) < step / 2:
                new, entry = 0.0, None
            if pos == 0 and new != 0:
                st["since"] = t
            st["pos"], st["entry"] = new, entry
            usd = qty * px
            h = int((t // (3600 * US)) % 24)
            res.hourly_usd[h] += usd
            if maker:
                res.maker_fills += 1
                res.maker_usd += usd
                fills.append((t, side, px, usd, mid))
            else:
                res.taker_fills += 1
                res.taker_usd += usd
            quoter.on_fill(side, px, tag, new, step)

        def taker(qty_signed: float, t: int) -> None:
            pos = st["pos"]
            side = BUY if qty_signed > 0 else SELL
            qty = abs(qty_signed)
            if (side == SELL and pos > 0) or (side == BUY and pos < 0):
                qty = min(qty, abs(pos))
            qty = math.floor(qty / step + 1e-9) * step
            if qty <= 0:
                return
            at = t + cfg.taker_us
            px = w.taker_price(side, qty, at, cfg.slip_bps)
            if math.isnan(px):
                return
            b, a, _, _ = w.bbo_at(at)
            book(side, px, qty, False, at, "taker", (b + a) / 2)

        def cancel_all(t: int) -> None:
            for o in orders:
                if o.cancel_at == NEVER:
                    o.cancel_at = t + lat

        def budget_ok(t: int) -> bool:
            cut = t - 60 * US
            while sent and sent[0] <= cut:
                sent.pop(0)
            return len(sent) < cfg.quotes_per_min

        def sync(want: list[tuple[int, float, float, str, bool]], i: int, t: int, quoting: bool) -> bool:
            """The order diff (lbot/trade/engine.py does the same): keep a live order within the tolerance and 20% of
            its size; else replace it (one batch per decision = one request). While quoting, a batch needs room in
            the requote budget, else it is skipped (False); exits and cancels while stopped use the reserve."""
            mid = MID[i]
            tol = max(2 * tick, p.tol_bps * BP * mid)
            active = [o for o in orders if o.cancel_at == NEVER]
            used: set[int] = set()
            changes: list[tuple[_Order | None, tuple[int, float, float, str, bool] | None]] = []
            for q in want:
                side, px, qty, tag, ro = q
                m = next((o for o in active if id(o) not in used and o.side == side and o.tag == tag), None)
                if m is not None:
                    used.add(id(m))
                    if abs(m.px - px) <= tol and abs(m.qty - qty) <= 0.2 * qty and m.reduce_only == ro:
                        continue
                changes.append((m, q))
            for o in active:
                if id(o) not in used:
                    changes.append((o, None))
            if not changes:
                return True
            if quoting:
                if not budget_ok(t):
                    res.skipped += 1
                    return False
                sent.append(t)
            res.requests += 1
            arr = None
            for old, q in changes:
                if old is not None:
                    old.cancel_at = t + lat
                if q is None:
                    continue
                side, px, qty, tag, ro = q
                if arr is None:
                    arr = w.bbo_at(t + lat)
                if (side == BUY and px >= arr[1]) or (side == SELL and px <= arr[0]):
                    res.rejects += 1
                    continue
                orders.append(_Order(side, px, qty, tag, t + lat, ro))
            return True

        for i in range(w.n):
            t = T[i]
            if OK[i]:
                last_mid = MID[i]
            if t < w.start_us:
                if OK[i]:
                    quoter.move_bps(t, MID[i])
                continue
            if not OK[i]:
                cancel_all(t)
            else:
                mid = MID[i]
                pos = st["pos"]
                # funding each hour, at the rate recorded then (a positive rate: longs pay shorts)
                if t >= next_fund:
                    k = bisect_right(fts, t) - 1
                    rate = frate[k] if k >= 0 and not math.isnan(frate[k]) else 0.0
                    pay = pos * mid * rate
                    st["cash"] -= pay
                    res.funding -= pay
                    next_fund += 3600 * US
                eq = st["cash"] + pos * mid
                if start_eq is None:
                    start_eq = eq
                    guard.restore(day=t // G.DAY_US, day_eq=sz.capital + eq, peak=sz.capital + eq, state="normal")
                if tail_eq0 is None and t >= tail_from:
                    tail_eq0 = eq
                hh = int(t // (3600 * US))
                if hh != hour_i:
                    if hour_eq is not None:
                        res.hourly_pnl[hour_i % 24] += eq - hour_eq
                    hour_i, hour_eq = hh, eq
                res.min_pnl = min(res.min_pnl, eq - start_eq)
                notional = abs(pos) * mid
                res.max_pos_usd = max(res.max_pos_usd, notional)
                if notional > 0 and sz.capital + eq <= notional * mr.mmf and not res.liquidated:
                    res.liquidated = res.killed = True
                    cancel_all(t)
                    taker(-pos, t)
                    guard.state = "killed"
                    continue
                d = guard.step(t, sz.capital + eq, pos, st["entry"], mid)
                want: list[tuple[int, float, float, str, bool]] = []
                plan_taker = False
                if d.action == G.QUOTE:
                    v = View(t, BID[i], ASK[i], BSZ[i], ASZ[i],
                             sum(o.qty for o in orders if o.side == BUY and o.cancel_at == NEVER and abs(o.px - BID[i]) < tick / 2),
                             sum(o.qty for o in orders if o.side == SELL and o.cancel_at == NEVER and abs(o.px - ASK[i]) < tick / 2),
                             pos, st["entry"], st["since"])
                    plan: Plan = quoter.plan(v, Rules(tick, step, mr.min_usd(mid), sz.order_usd, sz.cap_usd))
                    if plan.taker:
                        plan_taker = True
                        cancel_all(t)
                        taker(plan.taker, t)
                    else:
                        want = [(q.side, q.px, q.qty, q.tag, q.reduce_only) for q in plan.quotes
                                if q.qty * q.px >= mr.min_usd(mid) or q.reduce_only]
                        res.quoting_s += w.step / US
                elif d.action == G.EXIT:
                    side = SELL if pos > 0 else BUY
                    want = [(side, ASK[i] if side == SELL else BID[i], abs(pos), "exit", True)]
                elif d.action == G.TAKER:
                    cancel_all(t)
                    taker(-pos, t)
                if d.action in (G.QUOTE, G.EXIT) or orders:
                    fp = (tuple(want), BID[i], ASK[i], tuple((o.side, o.px, o.qty, o.cancel_at) for o in orders))
                    if fp != last_fp:
                        last_fp = fp if sync(want, i, t, d.action == G.QUOTE and not plan_taker) else None
                    orders[:] = [o for o in orders if o.cancel_at > t]
                # queue: set when the order lands, and never more ahead of us than the book shows now
                for o in orders:
                    if o.live_from <= t:
                        if o.ahead < 0:
                            o.ahead = w.shown_at(o.side, o.px, o.live_from, tick) if cfg.queue else math.inf
                        if o.ahead > 0 and cfg.queue:
                            o.ahead = min(o.ahead, w.shown_now(i, o.side, o.px, tick))
            # ---- this step's trades
            k, e = TIDX[i], TIDX[i + 1]
            if k == e or not orders:
                continue
            while k < e:
                g = tgrp[k]
                j = k + 1
                while j < ntr and tgrp[j] == g and tts[j] - tts[k] < 50_000:
                    j += 1
                ts = int(tts[k])
                ms = SELL if tbuy[k] else BUY          # the side a taker buy fills: our sells
                cands = [o for o in orders if o.side == ms and o.live_from <= ts < o.cancel_at and o.qty > 0]
                if cands:
                    ppx, psz = tpx[k:j], tsz[k:j]
                    cands.sort(key=lambda o: -o.px if ms == BUY else o.px)
                    taken = 0.0
                    for o in cands:
                        if o.ahead < 0:                 # landed: the queue it joined
                            o.ahead = w.shown_at(o.side, o.px, o.live_from, tick) if cfg.queue else math.inf
                        same = np.abs(ppx - o.px) <= tick / 2
                        at_px = float(psz[same].sum())
                        use = min(at_px, o.ahead)
                        o.ahead -= use
                        thru = float(psz[~same & (ppx < o.px)].sum() if ms == BUY else psz[~same & (ppx > o.px)].sum())
                        fq = min(o.qty, at_px - use + thru - taken)
                        if o.reduce_only:
                            fq = min(fq, abs(st["pos"]))
                        fq = math.floor(fq / step + 1e-9) * step
                        if fq <= 0:
                            continue
                        o.qty -= fq
                        taken += fq
                        b, a, _, _ = w.bbo_at(ts - 1)
                        book(o.side, o.px, fq, True, ts, o.tag, (b + a) / 2)
                k = j
            orders[:] = [o for o in orders if o.qty > step / 2]

        # ---- close: mark at mid, less the cost of crossing out of what is left
        pos = st["pos"]
        end_eq = st["cash"] + (pos * last_mid if not math.isnan(last_mid) else 0.0)
        if pos:
            k = w.n - 1
            half = float(w.ask[k] - w.bid[k]) / 2 if w.ok[k] else 0.0
            end_eq -= abs(pos) * (half + last_mid * mr.taker_fee)
            res.end_pos_usd = pos * last_mid
        if hour_eq is not None and hour_i >= 0:
            res.hourly_pnl[hour_i % 24] += end_eq - hour_eq
        res.pnl = end_eq - (start_eq or 0.0)
        res.tail_pnl = end_eq - (tail_eq0 if tail_eq0 is not None else end_eq)
        res.hours = float(w.ok[w.t >= w.start_us].sum()) * w.step / US / 3600
        res.pos_stops, res.day_stops = guard.pos_stops, guard.day_stops
        res.killed = res.killed or guard.state == "killed"
        self._markouts(res, fills, w)
        return res

    def _markouts(self, res: Result, fills: list[tuple[int, int, float, float, float]], w: Window) -> None:
        if not fills:
            return
        ts = np.array([f[0] for f in fills], np.int64)
        side = np.array([f[1] for f in fills], np.float64)
        px = np.array([f[2] for f in fills])
        usd = np.array([f[3] for f in fills])
        m0 = np.array([f[4] for f in fills])
        ok = ~np.isnan(m0)
        if ok.any():
            res.edge_bps = float(np.average(side[ok] * (m0[ok] - px[ok]) / px[ok] / BP, weights=usd[ok]))
        mids = (w.bbid + w.bask) / 2
        for h in self.cfg.markouts:
            k = np.searchsorted(w.bts, ts + h * US, side="right") - 1
            good = k >= 0
            mh = mids[np.clip(k, 0, None)]
            v = side * (mh - px) / px / BP
            good &= ~np.isnan(v)
            if good.any():
                res.markout_bps[f"{h}s"] = float(np.average(v[good], weights=usd[good]))
