"""Backtest the strategy menu on every recorded market and rank what to run now.

Every config runs on every market, one UTC day at a time (each day starts flat, with 2 h of warm-up), under the $100
account's risk rules (sim.Risk). Completed days are cached; the current day is re-run on each scan.

Leverage: each market is tested at its maximum Arcus leverage (1 / initialMarginFraction; BTC and ETH capped at 20x)
and, below that, at 20x, 10x, 5x and 2x. The leverage sets the size (sim.Risk.at_leverage): position up to capital x
leverage, orders of half the inventory cap. RWA perps use their off-hours maximum outside the underlying's session.

A candidate is GO only when all three checks pass:
- long window (up to the last 7 full days): average PnL/day >= -$0.25 (close to breakeven or better), at most one
  daily stop, never the $10 kill, and at least half the days not negative;
- short window (the last 24 h, re-run each scan): PnL >= -$0.25, and still at least 30% of its usual fills (the flow is
  still there), with the last 6 h not worse than -$0.50;
- market now (last 60 minutes of 1-min mids): not trending (efficiency ratio < 0.5), volatility not above 2x its
  usual level, spread not above 2x its usual level, and data fresh (< 5 minutes old).
GO candidates rank by maker volume per day (the goal: the most maker volume while at or near breakeven), then PnL.
One pick per market; the top three go to the owner for approval.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from bot.scout.sim import Config, MarketInfo, Result, Risk, S, Sim, SimParams, Window
from bot.scout.tape import US_DAY, TapeStore, day_start_us, day_str

SIM_VERSION = "6"          # bump when the simulator changes, so cached day results are recomputed
ALIVE_MARKET = "BTC-USD"   # busiest book: its rows show when the recorder was up
LEV_CAPS = {"BTC-USD": 20.0, "ETH-USD": 20.0}   # owner: the crypto majors never above 20x
LADDER = (20.0, 10.0, 5.0, 2.0)                 # tested below each market's maximum
HOLIDAYS_CSV = Path(__file__).resolve().parents[2] / "config" / "calendars" / "nyse_holidays.csv"
MENU: list[Config] = [
    *(Config(f"deep {d:g}bp", "mid", spacing_bps=d) for d in (1, 1.5, 2, 3, 5)),
    *(Config(f"deep {d:g}bp, no pause", "mid", spacing_bps=d, safety=False) for d in (1.5, 3)),
    Config("deep 3bp, skew", "mid", spacing_bps=3, kappa=1.0),
    *(Config(f"deep {d:g}bp x2", "mid", spacing_bps=d, levels=2, level_step_bps=3) for d in (2, 4)),
    *(Config(f"touch {d:g}bp", "mid", style="normal", spacing_bps=d) for d in (1, 3)),
    Config("improve touch", "mid", style="aggressive"),
    *(Config(f"grid {d:g}bp", "grid", spacing_bps=d, levels=3) for d in (10, 25)),
    *(Config(f"rgrid {d:g}bp", "rgrid", spacing_bps=d) for d in (5, 15)),
    Config("rsi signal", "signal"),
]
BY_NAME = {c.name: c for c in MENU}

# GO thresholds (see module docstring)
MIN_PNL_DAY = -0.25
MAX_DAY_STOPS = 1
MIN_TAIL_PNL = -0.50
MIN_FLOW_FRAC = 0.30
MAX_ER = 0.5
MAX_VOL_X = 2.0
MAX_SPREAD_X = 2.0
MAX_AGE_S = 300


def load_markets(path: Path) -> dict[str, MarketInfo]:
    data = json.loads(path.read_text())
    out = {}
    for m in data.get("markets", data):
        if m.get("status") != "ONLINE":
            continue
        out[m["marketDisplayName"]] = MarketInfo(float(m["tickSize"]), float(m["stepSize"]),
                                                 float(m["minOrderNotional"]), float(m["minOrderSize"]),
                                                 mmf=float(m.get("maintenanceMarginFraction") or 0))
    return out


def max_leverage(m: dict[str, Any]) -> tuple[float, float]:
    """(in-session, off-hours) maximum leverage for one market, after the owner's caps."""
    imf = float(m.get("initialMarginFraction") or 0.2)
    off = float(m.get("offHoursInitialMarginFraction") or imf)
    lev = min(1 / imf, LEV_CAPS.get(m["marketDisplayName"], math.inf))
    return round(lev, 2), round(min(1 / off, lev), 2)


def leverages(m: dict[str, Any]) -> list[tuple[float, float]]:
    """The maximum first, then the ladder below it."""
    lev, off = max_leverage(m)
    return [(lev, off)] + [(x, min(x, off)) for x in LADDER if x < lev - 1e-9]


def load_holidays(path: Path | None = None) -> list[str]:
    """NYSE holidays (no session that day). The project's config/ from the working directory, else the package's."""
    for p in ([path] if path else [Path("config/calendars/nyse_holidays.csv"), HOLIDAYS_CSV]):
        try:
            return [ln.split(",")[0] for ln in p.read_text().splitlines()[1:] if ln.strip()]
        except OSError:
            continue
    return []


def session_mask(spec: dict[str, Any] | None, holidays: list[str]) -> Any:
    """For an Arcus `regularTradingHours` spec: a function (µs timestamps) -> True while the underlying's session is
    open (weekdays, not a holiday). None for 24/7 markets."""
    if not spec:
        return None
    tz = ZoneInfo(spec.get("timezone") or "America/New_York")
    a, b = int(spec["startSecondsOfDay"]), int(spec["endSecondsOfDay"])
    overnight = bool(spec.get("isOvernight")) or b <= a
    hol = set(holidays)

    def at(d: dt.date, sec: int) -> int:
        d = d + dt.timedelta(days=sec // 86400)
        sec %= 86400
        return int(dt.datetime(d.year, d.month, d.day, sec // 3600, sec % 3600 // 60, sec % 60, tzinfo=tz)
                   .timestamp()) * 1_000_000

    def mask(t: np.ndarray) -> np.ndarray:
        out = np.zeros(len(t), bool)
        if not len(t):
            return out
        d = dt.datetime.fromtimestamp(int(t[0]) / 1e6, tz).date() - dt.timedelta(days=1)
        last = dt.datetime.fromtimestamp(int(t[-1]) / 1e6, tz).date()
        while d <= last:
            if d.weekday() < 5 and d.isoformat() not in hol:
                out |= (t >= at(d, a)) & (t < at(d, b + (86400 if overnight else 0)))
            d += dt.timedelta(days=1)
        return out
    return mask


def risk_key(r: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(r, sort_keys=True).encode()).hexdigest()[:10]


def label(setting: str, lev: float) -> str:
    return f"{setting} @ {lev:g}x"


def market_meta(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text())
    return {m["marketDisplayName"]: m for m in data.get("markets", data)}


# ------------------------------------------------------------------------------------------------ backtests
def _run_window(args: tuple[str, str, int, int, dict[str, Any], list[str], list[dict[str, Any]], dict[str, Any],
                            dict[str, Any] | None, list[str]]) -> dict[str, list[dict[str, Any]]]:
    """Every (risk, config) on one market-window; the window is prepared once. {risk_key: [results]}."""
    root, market, start, end, mi, names, risks, sp, rth_spec, holidays = args
    store = TapeStore(root)
    tape = store.load_range(market, start - 2 * 3600 * S, end)
    alive = store.load_range(ALIVE_MARKET, start - 2 * 3600 * S, end).bbo["ts"]
    w = Window(tape, start, end, alive_ts=alive, rth=session_mask(rth_spec, holidays))
    out: dict[str, list[dict[str, Any]]] = {}
    for r in risks:
        res = []
        for n in names:
            d = Sim(BY_NAME[n], Risk(**r), MarketInfo(**mi), SimParams(**sp)).run(w).as_dict()
            d["leverage"] = r["leverage"]
            res.append(d)
        out[risk_key(r)] = res
    return out


@dataclass
class Scanner:
    root: Path                        # data/scout
    risk: Risk = field(default_factory=Risk)   # capital and dollar stops; sizes come from each leverage
    sp: SimParams = field(default_factory=SimParams)
    workers: int = 6
    htf_days: int = 7
    ladder: bool = True               # False: each market at its maximum leverage only
    _alive_h: dict[str, float] = field(default_factory=dict)

    @property
    def store(self) -> TapeStore:
        return TapeStore(self.root / "tape")

    def cache_path(self, market: str, day: str, rkey: str) -> Path:
        return self.root / "cache" / f"v{SIM_VERSION}" / rkey / market / f"{day}.json"

    def risks_for(self, meta: dict[str, Any]) -> list[dict[str, Any]]:
        stops = {k: v for k, v in asdict(self.risk).items() if k in (
            "capital_usd", "daily_stop_usd", "pos_stop_usd", "kill_usd", "exit_taker_after_s", "cooldown_s")}
        levs = leverages(meta) if self.ladder else leverages(meta)[:1]
        return [asdict(Risk.at_leverage(lev, off, **stops)) for lev, off in levs]

    def full_days(self, market: str, now_us: int) -> list[str]:
        """Completed UTC days on which the recorder was up for at least 20 h and this market has data."""
        today = day_str(now_us)
        out = []
        for d in self.store.days(market):
            if d >= today:
                continue
            if d not in self._alive_h:
                self._alive_h[d] = self.store.load_day(ALIVE_MARKET, d).bbo_hours()
            if self._alive_h[d] >= 20:
                out.append(d)
        return out[-self.htf_days:]

    def backtest(self, markets: list[str], now_us: int, mis: dict[str, MarketInfo],
                 meta: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """{market: {risk_key: {"risk": risk, "days": {day: [results]}, "recent": [results]}}}. Days come from the
        cache when present."""
        jobs, where = [], []
        out: dict[str, dict[str, Any]] = {}
        names = [c.name for c in MENU]
        sp = asdict(self.sp)
        holidays = load_holidays()
        for m in markets:
            risks = self.risks_for(meta[m])
            out[m] = {risk_key(r): {"risk": r, "days": {}, "recent": []} for r in risks}
            rth = meta[m].get("regularTradingHours")
            for d in self.full_days(m, now_us):
                todo = []
                for r in risks:
                    cp = self.cache_path(m, d, risk_key(r))
                    if cp.exists():
                        out[m][risk_key(r)]["days"][d] = json.loads(cp.read_text())
                    else:
                        todo.append(r)
                if todo:
                    s = day_start_us(d)
                    jobs.append((str(self.store.root), m, s, s + US_DAY, asdict(mis[m]), names, todo, sp, rth, holidays))
                    where.append((m, d))
            end = now_us - now_us % S
            jobs.append((str(self.store.root), m, end - 24 * 3600 * S, end, asdict(mis[m]), names, risks, sp, rth,
                         holidays))
            where.append((m, "recent"))
        if jobs:
            with ProcessPoolExecutor(max_workers=self.workers) as ex:
                for (m, d), res in zip(where, ex.map(_run_window, jobs), strict=True):
                    for rk, rs in res.items():
                        if d == "recent":
                            out[m][rk]["recent"] = rs
                        else:
                            out[m][rk]["days"][d] = rs
                            cp = self.cache_path(m, d, rk)
                            cp.parent.mkdir(parents=True, exist_ok=True)
                            cp.write_text(json.dumps(rs))
        return out


# ------------------------------------------------------------------------------------------------ market now
def market_now(store: TapeStore, market: str, now_us: int, days: int = 7) -> dict[str, float]:
    """Regime of the last hour against the market's usual level (median of the same measures over `days`)."""
    tape = store.load_range(market, now_us - days * US_DAY, now_us)
    b = tape.bbo
    if len(b["ts"]) < 100:
        return {"age_s": math.inf}
    t = np.arange(now_us - days * US_DAY, now_us, 60 * S, dtype=np.int64)
    idx = np.searchsorted(b["ts"], t, side="right") - 1
    have = idx >= 0
    idx = np.clip(idx, 0, None)
    mid = np.where(have, (b["bid"][idx] + b["ask"][idx]) / 2, np.nan)
    spread = np.where(have, (b["ask"][idx] - b["bid"][idx]) / mid * 1e4, np.nan)
    last = mid[-61:]
    er = math.nan
    if not np.isnan(last).any() and len(last) == 61:
        path = np.abs(np.diff(last)).sum()
        er = float(abs(last[-1] - last[0]) / path) if path > 0 else 0.0
    ret = np.diff(np.log(mid))
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN windows for markets that just started recording
        hourly = [np.nanstd(ret[i:i + 60]) for i in range(0, len(ret) - 59, 60)]
        spread_now = float(np.nanmedian(spread[-60:]))
        spread_usual = float(np.nanmedian(spread[:-60])) if len(spread) > 60 else math.nan
    hourly = [h for h in hourly if not math.isnan(h) and h > 0]
    vol_now = hourly[-1] if hourly else math.nan
    vol_usual = float(np.median(hourly[:-1])) if len(hourly) > 1 else math.nan
    alive = store.load_range(ALIVE_MARKET, now_us - 3600 * S, now_us).bbo["ts"]
    last_seen = max(int(b["ts"][-1]), int(alive[-1]) if len(alive) else 0)
    return {"age_s": (now_us - last_seen) / S, "er": er, "vol_x": vol_now / vol_usual if vol_usual else math.nan,
            "spread_x": spread_now / spread_usual if spread_usual else math.nan, "spread_bps": spread_now,
            "move_1h_bps": float((last[-1] / last[0] - 1) * 1e4) if len(last) == 61 else math.nan}


# ------------------------------------------------------------------------------------------------ scoring
@dataclass
class Candidate:
    market: str
    config: str                 # "<setting> @ <leverage>x": what the owner approves
    go: bool
    reasons: list[str]
    days: int
    fills_day: float
    volume_day: float
    pnl_day: float
    worst_day: float
    day_stops: int
    positive_days: int
    recent_pnl: float
    recent_fills: int
    recent_volume: float
    tail_pnl: float
    taker_day: float
    actions_per_usd: float
    now: dict[str, float]
    setting: str = ""           # the menu entry (scan.BY_NAME)
    leverage: float = 0.0
    leverage_off: float = 0.0
    at_max: bool = False        # this is the market's maximum leverage
    order_usd: float = 0.0
    cap_usd: float = 0.0
    cap_off_usd: float = 0.0
    max_pos_usd: float = 0.0    # largest position the backtest reached
    liq_reduces: int = 0
    risk: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_market(market: str, bt: dict[str, Any], now: dict[str, float]) -> list[Candidate]:
    """bt: {risk_key: {"risk", "days", "recent"}} from Scanner.backtest (or one such entry for a single sizing)."""
    if "days" in bt:
        bt = {"": bt}
    out = []
    lev_max = max((e.get("risk", {}).get("leverage", 0.0) for e in bt.values()), default=0.0)
    for entry in bt.values():
        r = entry.get("risk") or {}
        out += _score(market, entry, now, r, at_max=r.get("leverage", 0.0) >= lev_max)
    return out


def _score(market: str, bt: dict[str, Any], now: dict[str, float], risk: dict[str, Any], at_max: bool
           ) -> list[Candidate]:
    out = []
    by_cfg: dict[str, list[dict[str, Any]]] = {}
    for _d, rs in sorted(bt["days"].items()):
        for r in rs:
            by_cfg.setdefault(r["config"], []).append(r)
    recent = {r["config"]: r for r in bt["recent"]}
    lev = float(risk.get("leverage", 0.0))
    for name in [c.name for c in MENU]:
        days = by_cfg.get(name, [])
        rec = recent.get(name)
        reasons = []
        if len(days) < 1:
            reasons.append("no full day of data yet")
        pnl = [d["pnl"] for d in days]
        n = max(1, len(days))
        fills_day = sum(d["maker_fills"] for d in days) / n
        vol_day = sum(d["maker_usd"] for d in days) / n
        pnl_day = sum(pnl) / n
        day_stops = sum(d["day_stops"] for d in days)
        pos_days = sum(1 for p in pnl if p >= 0)
        if days:
            if pnl_day < MIN_PNL_DAY:
                reasons.append(f"loses ${-pnl_day:.2f}/day over {len(days)} days")
            if day_stops > MAX_DAY_STOPS:
                reasons.append(f"hit the daily stop {day_stops} times")
            if any(d.get("liquidated") for d in days):
                reasons.append("liquidated")
            elif any(d["killed"] for d in days):
                reasons.append("hit the $10 kill")
            if pos_days < len(days) / 2:
                reasons.append(f"only {pos_days}/{len(days)} days not negative")
        if rec is None or rec["hours"] < 12:
            reasons.append("under 12 h of recent data")
        else:
            if rec["pnl"] < MIN_PNL_DAY:
                reasons.append(f"last 24 h lost ${-rec['pnl']:.2f}")
            if rec["tail_pnl"] < MIN_TAIL_PNL:
                reasons.append(f"last 6 h lost ${-rec['tail_pnl']:.2f}")
            if rec.get("liquidated") or rec.get("killed"):
                reasons.append("last 24 h hit the kill")
            if days and rec["maker_fills"] < MIN_FLOW_FRAC * fills_day:
                reasons.append(f"flow dried up: {rec['maker_fills']} fills in 24 h vs {fills_day:.0f}/day usual")
        if now.get("age_s", math.inf) > MAX_AGE_S:
            reasons.append("no fresh data")
        else:
            if now.get("er", 0) > MAX_ER:
                reasons.append(f"trending now (efficiency {now['er']:.2f}, {now.get('move_1h_bps', 0):+.0f} bps in 1 h)")
            if now.get("vol_x", 1) > MAX_VOL_X:
                reasons.append(f"volatility {now['vol_x']:.1f}x usual")
            if now.get("spread_x", 1) > MAX_SPREAD_X:
                reasons.append(f"spread {now['spread_x']:.1f}x usual")
        acts = sum(d["actions"] for d in days)
        filled = sum(d["maker_usd"] + d["taker_usd"] for d in days)
        out.append(Candidate(
            market=market, config=label(name, lev) if lev else name, go=not reasons, reasons=reasons, days=len(days),
            fills_day=fills_day, volume_day=vol_day, pnl_day=pnl_day, worst_day=min(pnl) if pnl else 0.0,
            day_stops=day_stops, positive_days=pos_days, recent_pnl=rec["pnl"] if rec else 0.0,
            recent_fills=rec["maker_fills"] if rec else 0, recent_volume=rec["maker_usd"] if rec else 0.0,
            tail_pnl=rec["tail_pnl"] if rec else 0.0, taker_day=sum(d["taker_usd"] for d in days) / n,
            actions_per_usd=acts / filled if filled else math.inf, now=now, setting=name, leverage=lev,
            leverage_off=float(risk.get("leverage_off", 0.0)), at_max=at_max,
            order_usd=float(risk.get("order_usd", 0.0)), cap_usd=float(risk.get("cap_usd", 0.0)),
            cap_off_usd=float(risk.get("cap_off_usd") or risk.get("cap_usd", 0.0)),
            max_pos_usd=max((d.get("max_pos_usd", 0.0) for d in days), default=0.0),
            liq_reduces=sum(d.get("liq_reduces", 0) for d in days), risk=risk))
    return out


def rank(cands: list[Candidate]) -> list[Candidate]:
    """GO first, by maker volume/day then PnL; one entry per market."""
    best: dict[str, Candidate] = {}
    for c in sorted(cands, key=lambda c: (not c.go, -c.volume_day if c.go else -c.pnl_day, -c.pnl_day)):
        best.setdefault(c.market, c)
    return sorted(best.values(), key=lambda c: (not c.go, -c.volume_day if c.go else -c.pnl_day, -c.pnl_day))


def best_at_max(cands: list[Candidate]) -> list[Candidate]:
    """Per market, the best setting at its maximum leverage (GO or not): what max leverage does, as asked."""
    best: dict[str, Candidate] = {}
    for c in sorted((c for c in cands if c.at_max and c.days),
                    key=lambda c: (not c.go, -c.volume_day if c.go else -c.pnl_day)):
        best.setdefault(c.market, c)
    return sorted(best.values(), key=lambda c: (not c.go, -c.volume_day if c.go else -c.pnl_day))


def scan(root: Path, *, now_us: int | None = None, markets: list[str] | None = None, workers: int = 6,
         risk: Risk | None = None, ladder: bool = True) -> dict[str, Any]:
    now_us = now_us or time.time_ns() // 1000
    mis = load_markets(root / "markets.json")
    meta = market_meta(root / "markets.json")
    sc = Scanner(root, risk=risk or Risk(), workers=workers, ladder=ladder)
    have = [m for m in sc.store.markets() if m in mis and (not markets or m in markets) and sc.store.days(m)]
    t0 = time.time()
    bt = sc.backtest(have, now_us, mis, meta)
    cands: list[Candidate] = []
    for m in have:
        cands += score_market(m, bt[m], market_now(sc.store, m, now_us))
    ranked = rank(cands)
    return {"ts_us": now_us, "took_s": round(time.time() - t0, 1), "risk": asdict(sc.risk),
            "leverage": {"policy": "max, then " + ", ".join(f"{x:g}x" for x in LADDER) if ladder else "max",
                         "caps": LEV_CAPS},
            "markets": len(have), "configs": len(MENU),
            "top": [c.as_dict() for c in ranked if c.go][:3],
            "ranked": [c.as_dict() for c in ranked],
            "at_max": [c.as_dict() for c in best_at_max(cands)],
            "all": [c.as_dict() for c in cands]}


def as_result(d: dict[str, Any]) -> Result:
    return Result(**d)


def table(res: dict[str, Any], limit: int = 25) -> str:
    """Plain-text ranking for the terminal: the best setting per market, then each market at its max leverage."""
    lines = [f"scan of {res['markets']} markets x {res['configs']} settings in {res['took_s']} s; "
             f"{len(res['top'])} pass all checks", ""]
    head = (f"{'':3}{'market':<12} {'setting':<30} {'order':>6} {'fills/d':>7} {'volume/d':>9} {'pnl/d':>7} "
            f"{'worst':>7} {'24h':>6}  why not")

    def rows(cs: list[dict[str, Any]]) -> list[str]:
        return [f"{i:>2} {c['market']:<12} {c['config']:<30} {c.get('order_usd', 0):>6,.0f} {c['fills_day']:>7.0f} "
                f"{c['volume_day']:>9,.0f} {c['pnl_day']:>+7.2f} {c['worst_day']:>+7.2f} {c['recent_pnl']:>+6.2f}  "
                f"{'GO' if c['go'] else '; '.join(c['reasons'])[:80]}" for i, c in enumerate(cs, 1)]
    lines += ["best per market (any leverage up to the max):", head]
    lines += rows([r for r in res["ranked"] if r["days"]][:limit])
    if res.get("at_max"):
        lines += ["", "each market at its MAXIMUM leverage:", head]
        lines += rows(res["at_max"][:limit])
    waiting = [r["market"] for r in res["ranked"] if not r["days"]]
    if waiting:
        lines += ["", f"still recording (under a full day of data): {', '.join(waiting)}"]
    return "\n".join(lines)
