"""The running bot: twice a second it reads the market and the account, steps the stops (lighter_bot/trade/guard.py), asks the
setup for its quotes (lighter_bot/trade/strategy.py: the backtest's own code), turns the difference between the wanted and
the live orders into one batch, and sends it if the request budget allows.

Files (state/, per mode so paper and live never mix):
- run-<mode>.json      the run: market, setup, leverage, limits, when it started, its volume and PnL, the stops' state
                       (so a restart continues the same run)
- status-<mode>.json   what the dashboard and Telegram show, rewritten every 2 s
- fills-<mode>.jsonl   every fill
- control-<mode>.json  commands from Telegram and the CLI: stop, close, pause, unpause, resume
- heartbeat-<mode>     touched every second
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lighter_bot.log import Log
from lighter_bot.trade import guard as G
from lighter_bot.trade.exchange import Change, Exchange, Fill, Order
from lighter_bot.trade.sizing import Sizes, Stops, sizes, target_capital
from lighter_bot.trade.strategy import BUY, SELL, Quote, Quoter, Rules, Setup, View

log = Log("engine")
US = 1_000_000
RENEW_S = 120.0      # a live quote is replaced this long before its expiry (lighter_bot/trade/live.py: QUOTE_EXPIRY_MS)


@dataclass
class RunSpec:
    """One run, as the owner (or a list's pick) asked for it."""

    market: str
    setup: str                      # "Mid +1 Long"
    leverage: float
    mode: str = "paper"             # paper | live
    capital: float | None = None    # None: from the account (paper: the paper capital)
    stops: tuple[float, float, float] = (2.0, 5.0, 25.0)
    sl: float | None = None         # the run's loss limit in $
    tp: float | None = None
    vol: float | None = None
    source: str = "you"             # you | list name
    started: float = field(default_factory=time.time)


@dataclass
class RunState:
    spec: RunSpec
    start_equity: float | None = None
    volume: float = 0.0
    maker_volume: float = 0.0
    fills: int = 0
    guard: dict[str, Any] = field(default_factory=dict)
    day: str = ""
    day_volume: float = 0.0
    day_fills: int = 0
    done: str = ""

    def save(self, p: Path) -> None:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1))
        os.replace(tmp, p)

    @classmethod
    def load(cls, p: Path) -> RunState | None:
        try:
            d = json.loads(p.read_text())
            spec = RunSpec(**{**d["spec"], "stops": tuple(d["spec"]["stops"])})
            return cls(spec, **{k: v for k, v in d.items() if k != "spec"})
        except (OSError, ValueError, KeyError, TypeError):
            return None


def utc_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


class Engine:
    def __init__(self, ex: Exchange, run: RunState, state_dir: Path, *, frac: float = 1.0,
                 max_capital: float | None = None, order_max: float | None = None, period_s: float = 0.5) -> None:
        self.ex = ex
        self.run = run
        self.spec = run.spec
        self.m = ex.market
        self.setup = Setup(*self._parse(self.spec.setup))
        self.params = self.setup.params()
        self.quoter = Quoter(self.params)
        self.dir = state_dir
        self.mode = self.spec.mode
        self.frac, self.max_capital, self.order_max = frac, max_capital, order_max
        self.sizes: Sizes | None = None
        self.guard: G.Guard | None = None
        self.paused = False
        self.stop_after_close = False
        self._stop = asyncio.Event()
        self.why = "starting"
        self.quote_s = 0.0
        self.total_s = 0.0
        self.blocked: dict[str, float] = {}
        self.day_eq: float | None = None
        self._last_status = 0.0
        self.last_error = ""
        self.sized_day = ""
        self.period = period_s
        ex.fill_cbs.append(self.on_fill)

    @staticmethod
    def _parse(text: str) -> tuple[str, float, str]:
        from lighter_bot.trade.strategy import parse
        s = parse(text)
        return s.mode, s.spread, s.bias

    # ---------------------------------------------------------------- paths
    def path(self, name: str) -> Path:
        return self.dir / f"{name}-{self.mode}.json"

    # ---------------------------------------------------------------- sizing
    def resize(self, equity: float) -> None:
        cap = self.spec.capital if self.spec.capital else target_capital(equity, frac=self.frac,
                                                                            max_capital=self.max_capital)
        if self.spec.capital:
            cap = min(cap, equity) if equity > 0 else cap
        st = Stops(*self.spec.stops)
        self.sizes = sizes(cap, min(self.spec.leverage, self.m.max_leverage), st, order_max=self.order_max)
        sz = self.sizes
        kill = max(sz.kill_usd, self.spec.sl or 0.0)        # a run stop above the kill lifts the kill to it
        daily = max(sz.daily_stop_usd, self.spec.sl or 0.0)
        lim = G.Limits(sz.pos_stop_usd, daily, kill, run_loss_usd=self.spec.sl, run_profit_usd=self.spec.tp,
                       run_volume_usd=self.spec.vol)
        if self.guard is None:
            self.guard = G.Guard(lim)
            g = self.run.guard
            if g:
                self.guard.restore(day=int(g.get("day", -1)), day_eq=g.get("day_eq"),
                                   peak=float(g.get("peak") or equity), state=str(g.get("state", "normal")))
        else:
            self.guard.lim = lim
        log.info("sized", market=self.m.symbol, label=sz.label())

    # ---------------------------------------------------------------- fills
    def on_fill(self, f: Fill) -> None:
        r = self.run
        now = time.time()
        if utc_day(now) != r.day:
            r.day, r.day_volume, r.day_fills = utc_day(now), 0.0, 0
        r.volume += f.usd
        r.day_volume += f.usd
        r.fills += 1
        r.day_fills += 1
        if f.maker:
            r.maker_volume += f.usd
        self.quoter.on_fill(f.side, f.px, f.tag, self.ex.acct.pos, self.m.step)
        bbo = self.ex.feed.bbo()
        rec = {"t": round(f.t, 3), "market": self.m.symbol, "side": "buy" if f.side == BUY else "sell", "px": f.px,
               "qty": f.qty, "usd": round(f.usd, 2), "maker": f.maker, "tag": f.tag, "fee": f.fee,
               "mid": (bbo[0] + bbo[1]) / 2 if bbo else None}
        with open(self.dir / f"fills-{self.mode}.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    # ---------------------------------------------------------------- the order diff
    def diff(self, want: list[Quote], orders: list[Order], now: float | None = None) -> list[Change]:
        """Keep a live order within the tolerance (and 20% of its size); modify it otherwise; cancel what is not wanted
        (and anything the bot did not place); leave orders still in flight alone. A live order close to its expiry is
        replaced, wanted price or not: Lighter drops it by itself at the expiry, and a modify cannot move that."""
        now = time.time() if now is None else now
        mid = (self.ex.feed.bbo() or (0, 0, 0, 0))
        tol = max(2 * self.m.tick, self.params.tol_bps * 1e-4 * (mid[0] + mid[1]) / 2)
        live = [o for o in orders if o.state in ("open", "sent")]
        used: set[int] = set()
        out: list[Change] = []
        for q in want:
            match = next((o for o in live if o.cid not in used and o.side == q.side and o.tag == q.tag), None)
            if match is None:
                out.append(Change("new", q))
                continue
            used.add(match.cid)
            if match.state == "sent":
                continue            # not acknowledged yet: never modify an order Lighter has not confirmed
            renew = bool(match.expires) and match.expires - now < RENEW_S
            if abs(match.px - q.px) <= tol and abs(match.qty - q.qty) <= 0.2 * q.qty and \
                    match.reduce_only == q.reduce_only and not renew:
                continue
            if match.reduce_only != q.reduce_only or renew:
                out += [Change("cancel", cid=match.cid), Change("new", q)]
            else:
                out.append(Change("modify", q, match.cid))
        for o in live:
            if o.cid not in used and o.state == "open":
                out.append(Change("cancel", cid=o.cid))
        return out

    # ---------------------------------------------------------------- control
    def read_control(self) -> None:
        p = self.path("control")
        if not p.exists():
            return
        try:
            cmds = json.loads(p.read_text())
            p.unlink()
        except (OSError, ValueError):
            return
        for c in cmds if isinstance(cmds, list) else [cmds]:
            cmd = c.get("cmd")
            log.info("control", cmd=cmd)
            if cmd == "stop":
                self._stop.set()
            elif cmd == "close":
                self.stop_after_close = True
                if self.guard:
                    self.guard.state, self.guard.exit_since = "exit_run", int(time.time() * US)
                    self.guard.why = "closing: asked from Telegram or the CLI"
            elif cmd == "pause":
                self.paused = True
            elif cmd == "unpause":
                self.paused = False
            elif cmd == "resume" and self.guard:
                self.guard.resume()

    # ---------------------------------------------------------------- one second
    async def step(self, now: float) -> None:
        ex = self.ex
        ex.tick(now)
        up = getattr(ex, "upkeep", None)
        if up is not None:
            await up(now)
        self.read_control()
        bbo = ex.feed.bbo()
        eq = ex.acct.equity
        if bbo is None or not ex.feed.fresh() or eq is None:
            self.why = "no fresh book" if eq is not None else "no account data yet"
            await self._cancel_quotes()
            return
        bid, ask, bsz, asz = bbo
        mid = (bid + ask) / 2
        if self.sizes is None or utc_day(now) != self.sized_day:
            self.resize(eq)                 # at start and at 00:00 UTC: sizes follow the account
            self.sized_day = utc_day(now)
        if utc_day(now) != self.run.day:    # today's counters start at 00:00 UTC, fills or not
            self.run.day, self.run.day_volume, self.run.day_fills = utc_day(now), 0.0, 0
        if self.run.start_equity is None:
            self.run.start_equity = eq
        g = self.guard
        assert g is not None and self.sizes is not None
        pos, entry = ex.acct.pos, ex.acct.entry
        run_pnl = eq - self.run.start_equity
        d = g.step(int(now * US), eq, pos, entry, mid, run_pnl=run_pnl, run_vol=self.run.volume)
        self.day_eq = g.day_eq
        orders = ex.live_orders()
        own_b = sum(o.qty for o in orders if o.side == BUY and abs(o.px - bid) < self.m.tick / 2)
        own_a = sum(o.qty for o in orders if o.side == SELL and abs(o.px - ask) < self.m.tick / 2)
        want: list[Quote] = []
        kind = "reserve"
        if d.action == G.QUOTE and not self.paused:
            v = View(int(now * US), bid, ask, bsz, asz, own_b, own_a, pos, entry)
            sz = self.sizes
            plan = self.quoter.plan(v, Rules(self.m.tick, self.m.step, self.m.min_order_usd(mid), sz.order_usd,
                                             sz.cap_usd))
            if plan.taker:
                await self._cancel_quotes()
                await ex.taker(plan.taker)
                return
            want = [q for q in plan.quotes if q.reduce_only or q.qty * q.px >= self.m.min_order_usd(mid)]
            kind = "quote"
            self.why = ""
        elif d.action in (G.EXIT, G.QUOTE) and pos:     # an exit, or paused: work the position off at the touch
            side = SELL if pos > 0 else BUY
            want = [Quote(side, ask if side == SELL else bid, abs(pos), "exit", True)]
            self.why = d.why or "paused by you: closing orders only"
        elif d.action == G.TAKER:
            await self._cancel_quotes()
            await ex.taker(-pos)
            self.why = d.why
            return
        else:
            self.why = d.why or ("paused by you" if self.paused else g.state)
        if self.stop_after_close and g.state in ("done", "normal") and not pos:
            self._stop.set()
        if g.state == "done" and not pos:
            self.run.done = self.run.done or g.why
        changes = self.diff(want, orders, now)
        if changes:
            await ex.send(changes, kind)
        dt = self.period
        self.total_s += dt
        if self.why:
            self.blocked[self.why.split(":")[0]] = self.blocked.get(self.why.split(":")[0], 0.0) + dt
        else:
            self.quote_s += dt

    async def _cancel_quotes(self) -> None:
        cancels = [Change("cancel", cid=o.cid) for o in self.ex.live_orders() if o.state == "open"]
        if cancels:
            await self.ex.send(cancels, "reserve")

    # ---------------------------------------------------------------- status
    def status(self, now: float) -> dict[str, Any]:
        ex, sz, g = self.ex, self.sizes, self.guard
        bbo = ex.feed.bbo()
        mid = (bbo[0] + bbo[1]) / 2 if bbo else None
        eq = ex.acct.equity
        r = self.run
        return {
            "t": now, "mode": self.mode, "market": self.m.symbol, "setup": self.setup.name, "leverage": self.spec.leverage,
            "source": self.spec.source, "started": self.spec.started,
            "state": g.state if g else "starting", "why": self.why, "paused": self.paused,
            "equity": eq, "free": ex.acct.free, "pos": ex.acct.pos, "entry": ex.acct.entry,
            "pos_usd": ex.acct.pos * mid if mid else 0.0, "mid": mid, "bbo": bbo,
            "day_pnl": (eq - g.day_eq) if (g and eq is not None and g.day_eq is not None) else None,
            "run_pnl": (eq - r.start_equity) if (eq is not None and r.start_equity is not None) else None,
            "run_volume": r.volume, "run_fills": r.fills, "day_volume": r.day_volume, "day_fills": r.day_fills,
            "limits": {"sl": self.spec.sl, "tp": self.spec.tp, "vol": self.spec.vol}, "done": r.done,
            "sizes": asdict(sz) if sz else None,
            "orders": [{"side": "buy" if o.side == BUY else "sell", "px": o.px, "qty": o.qty, "state": o.state,
                        "tag": o.tag} for o in ex.live_orders()],
            "quoting_pct": round(100 * self.quote_s / self.total_s, 1) if self.total_s else 0.0,
            "blocked": {k: round(v) for k, v in self.blocked.items()},
            "requests_last_min": ex.budget.used(), "rejects_10m": sum(1 for t, _ in ex.rejects if t > now - 600),
            "pos_stops": g.pos_stops if g else 0, "day_stops": g.day_stops if g else 0,
        }

    def save(self, now: float) -> None:
        g = self.guard
        if g:
            self.run.guard = {"day": g.day, "day_eq": g.day_eq, "peak": g.peak if math.isfinite(g.peak) else None,
                              "state": g.state}
        self.run.save(self.path("run"))
        st = self.path("status")
        tmp = st.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status(now), default=str))
        os.replace(tmp, st)
        (self.dir / f"heartbeat-{self.mode}").write_text(str(now))

    # ---------------------------------------------------------------- the loop
    async def run_loop(self, seconds: float | None = None) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(s, self._stop.set)
        end = time.time() + seconds if seconds else None
        try:
            await self.ex.start()
            await self.ex.set_leverage(min(self.spec.leverage, self.m.max_leverage))
            log.info("run_started", market=self.m.symbol, setup=self.setup.name, mode=self.mode,
                     leverage=self.spec.leverage)
            while not self._stop.is_set():
                now = time.time()
                try:
                    await self.step(now)
                except Exception as e:     # a bad second must not leave quotes behind: cancel and go on
                    self.last_error = f"{type(e).__name__}: {e}"
                    log.error("step_error", err=self.last_error)
                    with contextlib.suppress(Exception):
                        await self.ex.cancel_all()
                if now - self._last_status >= 2:
                    self.save(now)
                    self._last_status = now
                if end and now >= end:
                    break
                await asyncio.sleep(max(0.02, self.period - (time.time() - now)))
        finally:
            self.why = "stopped"
            await self.ex.stop()
            with contextlib.suppress(Exception):
                self.save(time.time())
            log.info("run_stopped", market=self.m.symbol, volume=round(self.run.volume, 2))


def send_control(state_dir: Path, mode: str, cmd: str) -> None:
    """Queue a command for the running bot (it reads them within a second)."""
    p = state_dir / f"control-{mode}.json"
    cur: list[dict[str, Any]] = []
    with contextlib.suppress(OSError, ValueError):
        v = json.loads(p.read_text())
        cur = v if isinstance(v, list) else [v]
    cur.append({"cmd": cmd, "at": time.time()})
    p.write_text(json.dumps(cur))


__all__ = ["Engine", "RunSpec", "RunState", "send_control"]
