"""The paper auto-trader: the three strategies and the noise breakout (#7 of the third list, intraday2.py), every
trading day, on the venues' own prices. No order is sent.

Every minute it brings each market's one-minute history up to date from the venue it would trade on (Lighter's
mark price; Arcus's oracle price), rebuilds today the way the backtest does (strategy.days), and replays the strategy
from the day's first bar with the backtest's own fill rules (sim.Book). What the replay holds now is the paper
position; a trade that has ended is written once to state/paper_trades.jsonl. Paper results are therefore exactly
the backtest's rules applied to prices nobody had seen when the rules were fixed: the forward test.

Real orders are not part of this program yet (nothing here can send one).

    daytrade run                     # all three, until stopped (a server job: never leave it running on a laptop)
    daytrade run --seconds 300       # a bounded check
    daytrade status
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import STATE, fetch, ops
from . import bars as B
from .core import MARKETS, Strategy
from .sim import Book, Trade
from .strategies import EmaPullback, LondonSweep, OpeningRange

HISTORY_DAYS = 30                     # one-minute history for the levels, the EMAs (EMA200 of 15m bars) and the noise
                                      # breakout's 14 earlier sessions


@dataclass
class Slot:
    """One strategy on one venue's prices."""
    strategy: Strategy
    venue: str

    @property
    def key(self) -> str:
        return f"{self.strategy.name}@{self.venue}"

    @property
    def data(self) -> str:
        return MARKETS[self.strategy.market].venue_data[self.venue]


def default_slots(tf: int = 5) -> list[Slot]:
    """The main version of each strategy (fixed before any backtest), on Lighter: no fees, and the only venue that
    lists spot gold. The noise breakout on QQQ is the one intraday rule whose backtest made money after Lighter's
    costs (reports/intraday2/REPORT.md); here it gets its forward test."""
    from .intraday2 import NoiseBreakout

    return [Slot(OpeningRange("SPY", tf), "lighter"), Slot(EmaPullback("QQQ", tf), "lighter"),
            Slot(LondonSweep(tf=tf), "lighter"), Slot(NoiseBreakout("QQQ"), "lighter")]


def update_bars(slot: Slot) -> B.Bars:
    """Bring the venue's history up to date (only what is new is fetched) and return the last HISTORY_DAYS of it."""
    sym = slot.strategy.market
    if slot.venue == "lighter":
        fetch.lighter(sym)
    else:
        fetch.arcus(sym)
    b = B.load(slot.data)
    return b.between(int(time.time()) - HISTORY_DAYS * B.DAY, int(time.time()) + 60) if len(b) else b


def replay_today(slot: Slot, bars: B.Bars) -> tuple[Book | None, int | None]:
    """Today's day as the backtest builds it, replayed through the backtest's fills. None when today is not a
    trading day for this strategy (yet)."""
    s, m = slot.strategy, MARKETS[slot.strategy.market]
    days = s.days(bars)
    if not days:
        return None, None
    day = days[-1]
    tz = m.tz
    today = int(B.local(np.array([int(time.time())]), tz)[0]) // B.DAY
    if day.key != today:
        return None, None
    book = Book(m.venues[slot.venue], m.tick)
    s.start_day(day)
    t, o, h, lo, c = day.t.tolist(), day.o.tolist(), day.h.tolist(), day.l.tolist(), day.c.tolist()
    for k in range(len(t)):
        book.bar(day.key, t[k], o[k], h[k], lo[k], c[k])
        book.after(t[k], c[k], s.on_bar(k, book.pos, book.n))
    return book, day.key


def trade_row(slot: Slot, tr: Trade) -> dict:
    f = lambda s: time.strftime("%Y-%m-%d %H:%M", time.gmtime(s))   # noqa: E731
    return {"slot": slot.key, "day": B.day_str(tr.day), "side": "long" if tr.side > 0 else "short", "tag": tr.tag,
            "entry_utc": f(tr.t_entry), "entry": round(tr.entry, 4), "stop": round(tr.stop0, 4),
            "target": round(tr.target, 4) if tr.target else None, "exit_utc": f(tr.t_exit),
            "exit": round(tr.exit, 4), "reason": tr.reason, "r": round(tr.r, 4), "r_gross": round(tr.r_gross, 4),
            "stop_bp": round(tr.dist * 1e4, 2), "bp": round(tr.r * tr.dist * 1e4, 2)}


class Runner:
    def __init__(self, slots: list[Slot], state_dir: Path = STATE, mode: str = "paper", swing: list | None = None):
        self.slots, self.dir, self.mode = slots, state_dir, mode
        self.swing = swing or []                       # swing_bot: the daily SPY/QQQ rules (15:59 NY) and hourly gold
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.dir / f"{mode}_trades.jsonl"
        self.status_path = self.dir / f"{mode}_status.json"
        self.log_path = self.dir / f"{mode}_events.jsonl"
        self.seen = self._seen()
        self.last: dict[str, str] = {}
        self.stop = False

    def _seen(self) -> set[str]:
        out = set()
        if self.trades_path.exists():
            for line in self.trades_path.read_text().splitlines():
                with contextlib.suppress(ValueError, KeyError):
                    d = json.loads(line)
                    out.add(f"{d['slot']}|{d['entry_utc']}")
        return out

    def event(self, kind: str, **kw) -> None:
        row = {"utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "event": kind, **kw}
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"{row['utc']} {kind} " + " ".join(f"{k}={v}" for k, v in kw.items()), flush=True)

    def tick(self) -> dict:
        status = {"mode": self.mode, "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "slots": {}}
        for slot in self.slots:
            try:
                bars = update_bars(slot)
                book, day = replay_today(slot, bars)
            except Exception as e:      # a venue read failing: say so, try again next minute
                status["slots"][slot.key] = {"error": f"{type(e).__name__}: {e}"[:200]}
                self.event("error", slot=slot.key, error=f"{type(e).__name__}: {e}"[:200])
                continue
            last_bar = time.strftime("%H:%M", time.gmtime(int(bars.t[-1]))) if len(bars) else "-"
            if book is None:
                status["slots"][slot.key] = {"state": "not a trading day / not started", "last_bar_utc": last_bar}
                continue
            for tr in book.done:
                k = f"{slot.key}|{time.strftime('%Y-%m-%d %H:%M', time.gmtime(tr.t_entry))}"
                if k not in self.seen:
                    self.seen.add(k)
                    row = trade_row(slot, tr)
                    with self.trades_path.open("a") as fh:
                        fh.write(json.dumps(row) + "\n")
                    self.event("trade closed", **{x: row[x] for x in ("slot", "side", "entry", "exit", "reason", "r")})
            if book.pos is not None:
                p = book.pos
                state = (f"{'LONG' if p.side > 0 else 'SHORT'} from {p.entry:.2f} stop {p.stop:.2f}"
                         f"{f' target {p.target:.2f}' if p.target else ''}{' (half taken)' if p.partial_done else ''}")
            elif book.pend is not None:
                o = book.pend
                state = (f"{o.kind} {'buy' if o.side > 0 else 'sell'} {o.price:.2f} stop {o.stop:.2f}"
                         f"{f' target {o.target:.2f}' if o.target else ''}")
            else:
                state = f"flat, {book.n} trade(s) today"
            if self.last.get(slot.key) != state:
                self.last[slot.key] = state
                self.event("state", slot=slot.key, now=state)
            status["slots"][slot.key] = {"state": state, "day": B.day_str(day), "trades_today": book.n,
                                         "last_bar_utc": last_bar}
        for m in self.swing:
            try:
                st, rows = m.update()
            except Exception as e:      # a venue read failing: say so, try again next minute
                status["slots"][f"{m.sym.lower()}-daily@lighter"] = {"error": f"{type(e).__name__}: {e}"[:200]}
                self.event("error", slot=f"{m.sym.lower()}-daily", error=f"{type(e).__name__}: {e}"[:200])
                continue
            for row in rows:
                k = f"{row['slot']}|{row['entry_utc']}"
                if k not in self.seen:
                    self.seen.add(k)
                    with self.trades_path.open("a") as fh:
                        fh.write(json.dumps(row) + "\n")
                    self.event("trade closed", **{x: row[x] for x in ("slot", "side", "entry", "exit", "reason", "bp")})
            for k, v in st.items():
                if self.last.get(k) != v["state"]:
                    self.last[k] = v["state"]
                    self.event("state", slot=k, now=v["state"])
            status["slots"].update(st)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1))
        tmp.replace(self.status_path)
        return status

    def run(self, seconds: float | None = None) -> None:
        fetch.QUIET = True
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        (self.dir / f"{self.mode}.pid").write_text(str(os.getpid()))
        end = time.time() + seconds if seconds else None
        self.event("start", slots=",".join(s.key for s in self.slots))
        try:
            while not self.stop and (end is None or time.time() < end):
                self.tick()
                nxt = (int(time.time()) // 60 + 1) * 60 + 5          # 5 s after the next minute closes
                while not self.stop and time.time() < nxt and (end is None or time.time() < end):
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.event("stop")
            (self.dir / f"{self.mode}.pid").unlink(missing_ok=True)


def summary(mode: str = "paper", state_dir: Path = STATE) -> str:
    lines = []
    st = state_dir / f"{mode}_status.json"
    if st.exists():
        d = json.loads(st.read_text())
        pid = state_dir / f"{mode}.pid"
        alive = pid.exists() and ops.alive(int(pid.read_text() or 0))
        lines.append(f"{mode.upper()} {'running' if alive else 'NOT running'} · last look {d['utc']} UTC")
        for k, v in d["slots"].items():
            lines.append(f"  {k}: {v.get('state') or v.get('error')}  (last bar {v.get('last_bar_utc', '-')} UTC)")
    else:
        lines.append(f"{mode.upper()}: never started")
    tp = state_dir / f"{mode}_trades.jsonl"
    rows = [json.loads(x) for x in tp.read_text().splitlines()] if tp.exists() else []
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["slot"], []).append(r)
    for k, rs in by.items():
        # a day trade has R (its stop's distance) and bp; a daily rule's trade has no stop, so bp only
        won = sum((x["bp"] if "bp" in x else x["r"]) > 0 for x in rs)
        parts = [f"{len(rs)} trades", f"{won} won"]
        if any(x.get("stop_bp") or "bp" not in x for x in rs):
            parts.append(f"{sum(x['r'] for x in rs):+.2f}R in all")
        if all("bp" in x for x in rs):
            parts.append(f"{sum(x['bp'] for x in rs) / len(rs):+.1f} bp a trade")
        lines.append(f"  {k}: " + ", ".join(parts))
    return "\n".join(lines)
