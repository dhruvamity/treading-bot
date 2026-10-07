"""Autopilot (/auto): the bot picks, starts, switches and stops setups by itself, within a budget.

Why: with a small account the question is not which setup, but when to
spend the day's loss budget. The same BTC Mid 0 costs 0.6 bp in a calm weekend hour and 2.7 bp in a wild weekday
one; spending a weekly pot only where the playbook predicts a low cost gave 30-55% more volume for the same money than
running from any fixed hour until the budget was gone (September, out of sample).

Every minute:
1. The pot: `budget_day` dollars are added at 00:00 UTC; unspent money carries over, up to POT_DAYS days of it, so a
   quiet weekend can use what a busy weekday did not. Each run's result (its PnL, kept by the engine per run) comes
   out of the pot; a run that makes money puts it back.
2. Each playbook market (arcus/scout/playbook.py) gets its regime now (arcus/scout/regime.py). A market is left out
   during an event window or up to EVENT_AHEAD_S before one (CPI, NFP, FOMC for every market; a stock's earnings for
   that stock: arcus/core/calendar.py, earnings fetched daily by arcus/core/earnings.py), for COOL_S after a shock, while
   its data is stale, or while Arcus has it offline.
3. The pick: the setup with the most volume per hour whose predicted cost (this session, this regime) is within
   `max_cost_bp`, and at least MIN_VOL_H an hour.
4. Nothing running: start the pick (max leverage, sized from the account, run stop = the pot, at most RUN_CAP_DAYS
   days of budget). Running: keep it while its own predicted cost stays within max_cost_bp x HYSTERESIS; stop when it
   does not, when an event or shock comes, or when the pot is gone; switch when another setup gives SWITCH_X times
   the volume and the run has lasted MIN_HOLD_S. After a stop it rests REST_S before starting again.
With `auto_cost` (the default) the ceiling is the playbook's tuned one for the budget: each day the playbook replays
this rule hour by hour over the last 4 weeks at every ceiling and keeps the one that bought the most volume (a small
budget buys most at a low ceiling, waiting for the cheapest hours; a large one needs a higher ceiling to be spent).
The owner's own /run, /stop, /closeall or /flatten turns the autopilot off.
LIVE needs BOT_PILOT_LIVE=1, a typed code in Telegram to turn on, and a passing doctor before every start.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arcus.common.logging import Log
from arcus.common.time import KOLKATA
from arcus.core.calendar import MACRO_WINDOW_S, SINGLE_STOCK_WINDOW_S, TradingCalendar
from arcus.scout import playbook as pbk
from arcus.scout import regime as rg
from arcus.scout import sessions
from arcus.scout.tape import TapeStore

log = Log("autopilot")
FILE = "autopilot.json"
TICK_S = 60.0
POT_DAYS = 7.0
RUN_CAP_DAYS = 3.0
MIN_POT = 0.50
MIN_VOL_H = 2_000.0
HYSTERESIS = 1.15
SWITCH_X = 1.5
MIN_HOLD_S = 20 * 60
REST_S = 10 * 60
EVENT_AHEAD_S = 15 * 60   # flat 15 min before the bot's own event window (30 min) opens
STALE_S = 10 * 60
START_GRACE_S = 180
DEFAULT_BUDGET = 5.0
DEFAULT_MAX_COST = 1.5      # bp in the backtest; live BTC has cost about 0.8x the backtest (note 2026-09-26)
COST_RANGE = (0.8, 3.0)
MACRO = ("cpi", "nfp", "fomc")
S = 1_000_000


@dataclass
class Settings:
    on: bool = False
    mode: str = "paper"               # paper | live
    budget_day: float = DEFAULT_BUDGET
    max_cost_bp: float = DEFAULT_MAX_COST
    auto_cost: bool = True            # the playbook's tuned ceiling for this budget (False: max_cost_bp)
    since: float = 0.0
    by: str = ""


def path(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def load(state_dir: Path | str) -> dict[str, Any]:
    try:
        d = json.loads(path(state_dir).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state_dir: Path | str, st: dict[str, Any]) -> None:
    p = path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    tmp.replace(p)


def settings_of(st: dict[str, Any]) -> Settings:
    known = {k: v for k, v in (st.get("settings") or {}).items() if k in Settings.__dataclass_fields__}
    return Settings(**known)


def is_on(state_dir: Path | str) -> bool:
    return settings_of(load(state_dir)).on


def validate(s: Settings) -> Settings:
    if s.mode not in ("paper", "live"):
        raise ValueError("mode is paper or live")
    if not 0.5 <= s.budget_day <= 1000:
        raise ValueError("budget must be between $0.50 and $1,000 a day")
    if not COST_RANGE[0] <= s.max_cost_bp <= COST_RANGE[1]:
        raise ValueError(f"cost ceiling must be between {COST_RANGE[0]:g} and {COST_RANGE[1]:g} bp")
    return s


def configure(state_dir: Path | str, **kw: Any) -> Settings:
    """Change settings (on, mode, budget_day, max_cost_bp, auto_cost); checked here, saved at once."""
    st = load(state_dir)
    s = settings_of(st)
    for k, v in kw.items():
        if k not in Settings.__dataclass_fields__:
            raise ValueError(f"unknown autopilot setting {k}")
        setattr(s, k, v)
    validate(s)
    if kw.get("on") and not (st.get("settings") or {}).get("on"):
        s.since = time.time()
        st.setdefault("pot", 0.0)
        st["refilled"] = None   # the first tick adds one day's money (days while it was off are not saved up)
    st["settings"] = asdict(s)
    save(state_dir, st)
    return s


def turn_off(state_dir: Path | str, why: str) -> bool:
    """Called when the owner takes over (/run, /stop, /closeall, /flatten). True if it was on."""
    st = load(state_dir)
    s = settings_of(st)
    if not s.on:
        return False
    s.on = False
    st["settings"] = asdict(s)
    st["off_why"] = why
    save(state_dir, st)
    return True


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------ pure rules
def refill(st: dict[str, Any], s: Settings, now: float) -> dict[str, Any] | None:
    """Add the day's budget at the first tick of each UTC day; returns yesterday's summary (None: same day)."""
    today = _day(now)
    last = st.get("refilled")
    if last == today:
        return None
    days = max(1, (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days) if last else 1
    before = float(st.get("pot") or 0.0)
    st["pot"] = min(before + days * s.budget_day, POT_DAYS * s.budget_day)
    st["refilled"] = today
    summary = {"day": last, **(st.get("days") or {}).get(last or "", {}), "pot_before": before, "pot": st["pot"]}
    keep = sorted(st.get("days") or {})[-35:]
    st["days"] = {k: st["days"][k] for k in keep}
    return summary


def ceiling(s: Settings, pb: dict[str, Any]) -> float:
    """The cost ceiling in force: the owner's fixed one, else the playbook's tuned one for this budget (the ceiling
    that bought the most volume with this much a day over the last 4 weeks), else the owner's default."""
    if s.auto_cost:
        c = pbk.ceiling_for(pb, s.budget_day)
        if c is not None:
            return c
    return s.max_cost_bp


def event_block(cal: TradingCalendar, market: str, now_us: int, ahead_s: int = EVENT_AHEAD_S) -> str | None:
    """Why a market must stay flat now: inside an event window, or one starting within ahead_s."""
    base = market.split("-")[0].upper()
    for e in cal.events:
        if e.kind in MACRO:
            w = MACRO_WINDOW_S * S
        elif e.kind == "earnings" and e.symbol == base:
            w = SINGLE_STOCK_WINDOW_S * S
        else:
            continue
        if e.ts_us - w - ahead_s * S <= now_us <= e.ts_us + w:
            when = dt.datetime.fromtimestamp(e.ts_us / S, dt.UTC)
            name = f"{base} earnings" if e.kind == "earnings" else e.kind.upper()
            return f"{name} {when:%a %H:%M} UTC ({when.astimezone(KOLKATA):%H:%M} IST)"
    return None


def choose(opts: list[dict[str, Any]], max_cost_bp: float, min_vol_h: float = MIN_VOL_H) -> dict[str, Any] | None:
    ok = [o for o in opts if o["cost_bp"] <= max_cost_bp and o["vol_h"] >= min_vol_h]
    return max(ok, key=lambda o: (o["vol_h"], -o["cost_bp"])) if ok else None


@dataclass
class Decision:
    action: str                    # idle | start | keep | switch | stop
    reason: str
    target: dict[str, Any] | None = None


def decide(*, running: dict[str, Any] | None, now: float, pick: dict[str, Any] | None,
           current: dict[str, Any] | None, blocked_running: str | None, pot_left: float, max_cost_bp: float,
           last_stop: float = 0.0) -> Decision:
    """current: the running setup's own estimate now (None: out of the playbook or its market is left out)."""
    if pot_left < MIN_POT:
        return Decision("stop" if running else "idle", f"budget used (${max(0.0, pot_left):.2f} left)")
    if running:
        if blocked_running:
            return Decision("stop", blocked_running)
        if current is None or current["cost_bp"] > max_cost_bp * HYSTERESIS:
            why = (f"{running['market'].split('-')[0]} {running['setup']} now {current['cost_bp']:.2f} bp > "
                   f"{max_cost_bp:.2f}" if current else f"{running['market'].split('-')[0]} left out")
            if pick and (pick["market"], pick["setup"]) != (running["market"], running["setup"]):
                return Decision("switch", why, pick)
            return Decision("stop", why)
        if pick and (pick["market"], pick["setup"]) != (running["market"], running["setup"]) \
                and pick["vol_h"] >= SWITCH_X * max(current["vol_h"], 1.0) \
                and now - float(running.get("since") or now) >= MIN_HOLD_S:
            return Decision("switch", f"{pick['vol_h'] / max(current['vol_h'], 1.0):.1f}x the volume", pick)
        return Decision("keep", "within the ceiling")
    if pick is None:
        return Decision("idle", "no setup within the cost ceiling")
    if now - last_stop < REST_S:
        return Decision("idle", "resting after the last stop")
    return Decision("start", "cheapest volume now", pick)


# ------------------------------------------------------------------------------------------------ the loop
@dataclass
class Look:
    """One market this minute."""
    market: str
    regime: rg.Regime | None
    blocked: str | None
    options: list[dict[str, Any]] = field(default_factory=list)


class Autopilot:
    def __init__(self, root: Path, pilot: Any, *, calendar: TradingCalendar | None = None,
                 recent: Callable[[str], list[tuple[int, float]]] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = root                                   # the project root
        self.pilot = pilot
        self.state_dir = root / pilot.control.app.state_dir
        self.scout = root / "data" / "scout"
        self.calendar = calendar or TradingCalendar()
        self.recent = recent
        self.clock = clock
        self._usual: dict[tuple[str, str], rg.Usual] = {}
        self.busy = False

    @property
    def store(self) -> TapeStore:
        return TapeStore(self.scout / "tape")

    def event(self, text: str, *, alert: bool = False, **data: Any) -> dict[str, Any]:
        e: dict[str, Any] = self.pilot.event("auto_alert" if alert else "auto", text, **data)
        return e

    def usual(self, market: str, now: float) -> rg.Usual:
        k = (market, _day(now))
        if k not in self._usual:
            self._usual = {kk: v for kk, v in self._usual.items() if kk[1] == k[1]}
            self._usual[k] = rg.usual(self.store, market, int(now * S))
        return self._usual[k]

    def look(self, pb: dict[str, Any], now: float, offline: set[str]) -> list[Look]:
        sess = sessions.session_of(now)
        out = []
        regs: dict[str, rg.Regime | None] = {}
        for m in (pb.get("markets") or {}):
            blocked = event_block(self.calendar, m, int(now * S))
            reg = None
            try:
                reg = rg.now(self.store, m, int(now * S), self.usual(m, now), self.recent(m) if self.recent else None)
            except Exception as e:   # a market with no data now: left out this minute
                blocked = blocked or f"no regime ({type(e).__name__})"
            if m in offline:
                blocked = blocked or "Arcus has it offline"
            regs[m] = reg
            cool = float((self._st.get("cool") or {}).get(m) or 0)
            if reg is not None and reg.shock:
                cool = max(cool, now + rg.COOL_S)
                self._st.setdefault("cool", {})[m] = cool
            if cool > now:
                blocked = blocked or f"shock: resting until {sessions.utc(cool)} UTC"
            opts = pbk.options(pb, m, sess, reg.bucket) if reg is not None else []
            out.append(Look(m, reg, blocked, opts))
        alive = min((r.age_s for r in regs.values() if r is not None), default=math.inf)
        if alive > STALE_S:   # the recorder has written nothing for every market: no fresh regime anywhere
            for lk in out:
                lk.blocked = lk.blocked or ("no market data" if math.isinf(alive) else
                                            f"no market data for {alive / 60:.0f} min")
        return out

    # ---------------------------------------------------------------- one tick
    async def tick(self) -> Decision | None:
        st = self._st = load(self.state_dir)
        s = settings_of(st)
        now = self.clock()
        summary = refill(st, s, now) if s.on or st.get("run") else None
        run = st.get("run")
        if run:
            self._account(st, run, now)
        if summary and summary.get("day"):
            self._daily(summary, s)
        if not s.on:   # off (the owner took over): a run it started is still booked once it ends
            save(self.state_dir, st)
            return None
        act = self.pilot.active()
        if act and act.get("by") != "autopilot" and float(act.get("since") or 0) > s.since \
                and self.pilot.control.alive(act["mode"]):   # e.g. `arcus pilot approve` from a shell
            turn_off(self.state_dir, f"you started {act['market']} yourself ({act.get('by')})")
            self.event(f"🤖 AUTOPILOT OFF\nYou started {act['market']} yourself\n/auto to turn it on again")
            return None
        live = s.mode == "live"
        if live and os.environ.get("BOT_PILOT_LIVE") != "1":
            configure(self.state_dir, on=False)
            self.event("🤖 AUTOPILOT OFF\nLIVE is off on this server (BOT_PILOT_LIVE=1 in .env)", alert=True)
            return None
        pb = pbk.load(self.scout)
        if not pb.get("markets"):
            st["last"] = {"ts": now, "action": "idle", "reason": "no playbook yet (the scout builds it)"}
            save(self.state_dir, st)
            return Decision("idle", "no playbook yet")
        scan = self.pilot.latest_scan() or {}
        looks = self.look(pb, now, set(scan.get("offline") or []))
        opts = [o for lk in looks if not lk.blocked for o in lk.options]
        lam = ceiling(s, pb)
        pick = choose(opts, lam)
        run = st.get("run")
        pot_left = float(st.get("pot") or 0.0) + (min(0.0, float(run.get("pnl") or 0)) if run else 0.0)
        current = blocked_running = None
        if run:
            lk = next((x for x in looks if x.market == run["market"]), None)
            blocked_running = lk.blocked if lk else "left the playbook"
            current = next((o for o in (lk.options if lk else []) if o["setup"] == run["setup"]), None)
        d = decide(running=run, now=now, pick=pick, current=current, blocked_running=blocked_running,
                   pot_left=pot_left, max_cost_bp=lam, last_stop=float(st.get("last_stop") or 0))
        st["last"] = {"ts": now, "action": d.action, "reason": d.reason, "pick": pick, "ceiling": lam, "session":
                      sessions.session_of(now), "markets": {lk.market: {"regime": lk.regime.as_dict() if lk.regime
                                                                        else None, "blocked": lk.blocked}
                                                            for lk in looks}}
        save(self.state_dir, st)
        if d.action in ("start", "switch", "stop"):
            await self._act(d, s, live, pot_left)
        return d

    def _account(self, st: dict[str, Any], run: dict[str, Any], now: float, *, settled: bool = False,
                 title: str = "") -> None:
        """Keep the run's PnL and volume current; when its process has gone (settled: the caller waited for it),
        book them against the pot."""
        ctl = self.pilot.control
        pnl = _num(ctl._kv_get(run["mode"], f"run_pnl:{run['run_id']}"))
        vol = _num(ctl._kv_get(run["mode"], f"run_vol:{run['run_id']}"))
        run["pnl"], run["volume"] = pnl, vol
        if not settled and (now - float(run.get("since") or now) < START_GRACE_S or ctl.alive(run["mode"])):
            return
        why = run.get("why") or ((ctl.view(run["mode"]).snapshot or {}).get("risk") or {}).get("all_stopped")
        self._book(st, run, now, title or "RUN ENDED", why or "")

    def _book(self, st: dict[str, Any], run: dict[str, Any], now: float, title: str, why: str) -> None:
        pnl, vol = float(run.get("pnl") or 0), float(run.get("volume") or 0)
        st["pot"] = min(float(st.get("pot") or 0) + pnl, POT_DAYS * settings_of(st).budget_day)
        day = st.setdefault("days", {}).setdefault(_day(now), {"volume": 0.0, "pnl": 0.0, "runs": 0})
        day["volume"] += vol
        day["pnl"] += pnl
        day["runs"] += 1
        st["run"] = None
        st["last_stop"] = now
        bp = -pnl / vol * 1e4 if vol else 0.0
        self.event(f"🤖 {title}\n{run['market'].split('-')[0]} · {run['setup']}\n"
                   f"{_money(pnl)} · ${vol:,.0f} volume ({bp:.2f} bp)\n" + (f"{why}\n" if why else "") +
                   f"Pot ${st['pot']:.2f}")

    async def _act(self, d: Decision, s: Settings, live: bool, pot_left: float) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            st = load(self.state_dir)
            run = st.get("run")
            if d.action == "stop":
                await self.pilot.close(by="autopilot")
                st = load(self.state_dir)
                if st.get("run"):
                    st["run"]["why"] = d.reason
                    self._account(st, st["run"], self.clock(), settled=True, title="AUTOPILOT STOPPED")
                else:
                    self.event(f"🤖 AUTOPILOT STOPPED\n{d.reason}")
                save(self.state_dir, st)
                return
            t = d.target
            assert t is not None
            limit = round(min(pot_left, RUN_CAP_DAYS * s.budget_day), 2)
            c = {**self.pilot.find(t["market"], t["setup"], t.get("leverage") or "max"), "max_loss_usd": limit}
            if live:
                ok, rep = await self._doctor(c)
                if not ok:
                    st["cool"] = {**(st.get("cool") or {}), t["market"]: self.clock() + 30 * 60}
                    save(self.state_dir, st)
                    self.event(f"🤖 AUTOPILOT CANNOT START\n{t['market']} · {t['setup']}\n{rep[:300]}\n"
                               "Tries another market; this one again in 30 min", alert=True)
                    return
            await self.pilot.deploy(c, live=live, by="autopilot")
            st = load(self.state_dir)
            old = st.get("run")
            if old:   # a switch: the old run has closed and exited
                self._account(st, old, self.clock(), settled=True, title="RUN ENDED (switching)")
            a = self.pilot.active() or {}
            st["run"] = {"run_id": a.get("run_id"), "mode": "live" if live else "paper", "market": t["market"],
                         "setup": t["setup"], "since": self.clock(), "limit": limit, "pnl": 0.0, "volume": 0.0,
                         "pred": {k: t[k] for k in ("vol_h", "cost_bp", "hours")}}
            save(self.state_dir, st)
            where = sessions.TITLES[sessions.session_of(self.clock())]
            reg = ((st.get("last") or {}).get("markets") or {}).get(t["market"], {}).get("regime") or {}
            self.event(f"🤖 AUTOPILOT {'SWITCHED' if run else 'STARTED'} · {'LIVE' if live else 'PAPER'}\n"
                       f"{t['market'].split('-')[0]} · {t['setup']} · {c['leverage']:g}x\n"
                       f"{where} · {reg.get('bucket', '?')} market ({float(reg.get('ratio') or 0):.1f}x usual)\n"
                       f"Expect ${t['vol_h'] / 1e3:,.0f}k/h at {t['cost_bp']:.2f} bp (backtest)\n"
                       f"Run stop -${limit:.2f} · pot ${pot_left:.2f}\n{d.reason}")
        except Exception as e:
            log.error("autopilot_act_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)
            self.event(f"🤖 AUTOPILOT ERROR\n{d.action}: {str(e)[:300]}", alert=True)
            st = load(self.state_dir)
            st["last_stop"] = self.clock()
            save(self.state_dir, st)
        finally:
            self.busy = False

    async def _doctor(self, c: dict[str, Any]) -> tuple[bool, str]:
        ctl = self.pilot.control
        p = self.pilot.write_session(c, live=True, path=self.state_dir / "autopilot_check.yaml")
        ok, rep = await ctl.doctor(str(p), replacing=ctl.alive("live"))
        return bool(ok), str(rep)

    def _daily(self, summary: dict[str, Any], s: Settings) -> None:
        vol, pnl = float(summary.get("volume") or 0), float(summary.get("pnl") or 0)
        bp = f" ({-pnl / vol * 1e4:.2f} bp)" if vol else ""
        self.event(f"🤖 AUTOPILOT · {summary['day']}\n${vol:,.0f} volume · {_money(pnl)}{bp} · "
                   f"{int(summary.get('runs') or 0)} runs\nPot ${summary['pot']:.2f} (+${s.budget_day:.2f} today)\n"
                   f"Cost ceiling {ceiling(s, pbk.load(self.scout)):.2f} bp")

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception as e:
                log.error("autopilot_tick_failed", reason=type(e).__name__, data={"err": str(e)[:300]}, exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), TICK_S)


def _num(v: str | None) -> float:
    try:
        x = float(v) if v not in (None, "") else 0.0
        return x if math.isfinite(x) else 0.0
    except ValueError:
        return 0.0


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


# ------------------------------------------------------------------------------------------------ views
def status_lines(st: dict[str, Any], pb: dict[str, Any], now: float | None = None) -> list[str]:
    """What /auto shows: settings, the pot, what it is doing and why, today and the week."""
    now = now or time.time()
    s = settings_of(st)
    out = [f"{'ON' if s.on else 'OFF'} · {s.mode.upper()}",
           f"Budget ${s.budget_day:.2f}/day · pot ${float(st.get('pot') or 0):.2f} (holds {POT_DAYS:g} days)",
           f"Cost ceiling {ceiling(s, pb):.2f} bp backtest (~{ceiling(s, pb) * 0.8:.1f} bp live)"
           f"{' · tuned daily for this budget' if s.auto_cost and pbk.ceiling_for(pb, s.budget_day) else ''}"]
    run = st.get("run")
    if run:
        out.append(f"Running: {run['market'].split('-')[0]} · {run['setup']} since "
                   f"{sessions.utc(float(run['since']))} UTC · {_money(float(run.get('pnl') or 0))} · "
                   f"${float(run.get('volume') or 0):,.0f}")
    last = st.get("last") or {}
    if last and s.on:
        out.append(f"Now: {sessions.TITLES.get(str(last.get('session')), '?')} · {last.get('action')}: "
                   f"{last.get('reason')}")
        for m, x in sorted((last.get("markets") or {}).items()):
            r = x.get("regime") or {}
            out.append(f"  {m.split('-')[0]:5s} {x['blocked'] or r.get('bucket', '?')}"
                       f"{'' if x['blocked'] or not r.get('ratio') else f' {float(r['ratio']):.1f}x'}")
    if not s.on and st.get("off_why"):
        out.append(f"Turned off: {st['off_why']}")
    today = (st.get("days") or {}).get(_day(now))
    if today:
        out.append(f"Today: ${today['volume']:,.0f} · {_money(today['pnl'])} · {today['runs']} runs")
    week = [v for k, v in (st.get("days") or {}).items() if k > _day(now - 7 * 86400)]
    if week:
        vol, pnl = sum(v["volume"] for v in week), sum(v["pnl"] for v in week)
        out.append(f"7 days: ${vol:,.0f} · {_money(pnl)}" + (f" ({-pnl / vol * 1e4:.2f} bp)" if vol else ""))
    return out


def plan_lines(pb: dict[str, Any], max_cost_bp: float, now: float | None = None, hours: float = 24.0) -> list[str]:
    """The next day's sessions and what the autopilot would run in each at a usual market."""
    out = []
    for p in pbk.week_plan(pb, max_cost_bp, now or time.time(), hours):
        k = p["pick"]
        what = f"{k['market'].split('-')[0]} {k['setup']} ~${k['vol_h'] / 1e3:,.0f}k/h {k['cost_bp']:.2f} bp" if k \
            else "wait (nothing within the ceiling)"
        out.append(f"{sessions.ist(p['from'])} IST {sessions.TITLES[p['session']]}: {what}")
    return out
