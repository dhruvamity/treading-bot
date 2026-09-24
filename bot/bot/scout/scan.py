"""Backtest the strategy menu on every recorded market and rank what to run now.

Every config runs on every market, one UTC day at a time (each day starts flat, with 2 h of warm-up), at the capital
the bot trades with (the account's equity, bucketed; bot/common/sizing.py) and its stops as % of that capital.
Completed days are cached per capital bucket; the current day is re-run on each scan.

Leverage: each market is tested at its maximum Arcus leverage (1 / initialMarginFraction; BTC and ETH capped at 20x)
and, below that, at 20x, 10x, 5x and 2x. The leverage sets the size (sim.Risk.for_capital): position up to capital x
leverage, orders of half the inventory cap. RWA perps use their off-hours maximum outside the underlying's session.
Two limits: a leverage whose off-hours order would fall under 1.2x the Arcus minimum order needs more capital and is
skipped; and one order never exceeds the market's liquidity ceiling (the 99th percentile of taker-order notional over
the recorded days), past which the sizes stop growing and the stops apply to the capital actually used.

A candidate is GO only when all three checks pass (percentages are of the capital the sizes use):
- long window (up to the last 7 full days): average PnL/day >= -0.25% (close to breakeven or better), at most one
  daily stop, never the kill, at least half the days not negative, and at least 5 fills a day; a market trading for
  under 21 days (listed recently, or first seen after the recorder started) needs 3 full days, and a market's first
  recorded day counts only if it covers 20 h;
- short window (the last 24 h, re-run each scan): PnL >= -0.25%, and still at least 30% of its usual fills (the flow is
  still there), with the last 6 h not worse than -0.50%;
- market now (last 60 minutes of 1-min mids): not trending (efficiency ratio < 0.5), volatility not above 2x its
  usual level, spread not above 2x its usual level, and data fresh (< 5 minutes old).
GO candidates rank by maker volume per day (the goal: the most maker volume while at or near breakeven), then PnL.
One pick per market; the top three go to the owner for approval.
"""

from __future__ import annotations

import contextlib
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

from bot.common.sizing import Pct, bucket, min_capital, venue_min_usd
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

# GO thresholds (see module docstring; the PnL ones are % of capital, in sizing.Pct)
NEW_LISTING_DAYS = 21      # Arcus addedTimestamp this recent: a new listing ...
NEW_LISTING_MIN_DAYS = 3   # ... needs this many full days before it can be GO (listing-week flow is unusual)
MAX_DAY_STOPS = 1
MIN_FILLS_DAY = 5
MIN_FLOW_FRAC = 0.30
MAX_ER = 0.5
MAX_VOL_X = 2.0
MAX_SPREAD_X = 2.0
MAX_AGE_S = 300


def load_markets(path: Path) -> dict[str, MarketInfo]:
    """The ONLINE markets' trading parameters. A listing with a field missing or not yet set is skipped (and so not
    scanned) rather than failing the whole scan."""
    data = json.loads(path.read_text())
    out = {}
    for m in data.get("markets", data):
        if m.get("status") != "ONLINE":
            continue
        try:
            out[m["marketDisplayName"]] = MarketInfo(float(m["tickSize"]), float(m["stepSize"]),
                                                     float(m.get("minOrderNotional") or 5),
                                                     float(m.get("minOrderSize") or m["stepSize"]),
                                                     mmf=float(m.get("maintenanceMarginFraction") or 0))
        except (KeyError, TypeError, ValueError):
            continue
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


INFO_KEYS = ("min_capital_usd", "liq_ceiling_usd")   # risk fields the simulation does not read


def risk_key(r: dict[str, Any]) -> str:
    """Cache key of a sizing: only the fields that change a backtest (a price-dependent minimum must not force the
    cached days to be recomputed every hour)."""
    sim = {k: v for k, v in r.items() if k not in INFO_KEYS}
    return hashlib.sha1(json.dumps(sim, sort_keys=True).encode()).hexdigest()[:10]


def label(setting: str, lev: float) -> str:
    return f"{setting} @ {lev:g}x"


def market_meta(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text())
    return {m["marketDisplayName"]: m for m in data.get("markets", data)}


def venue_min(mi: MarketInfo, meta: dict[str, Any]) -> float:
    """The Arcus minimum order in USD at the last known price."""
    px = float(meta.get("markPrice") or meta.get("oraclePrice") or meta.get("lastTradePrice") or 0)
    return venue_min_usd(mi.min_notional, mi.min_size, px)


def taker_orders(trades: dict[str, np.ndarray]) -> np.ndarray:
    """Notional of each taker order (the prints sharing one sequenceNumber); prints without one count alone."""
    if not len(trades["ts"]):
        return np.zeros(0)
    n = trades["px"] * trades["sz"]
    seq = trades["seq"]
    order = np.argsort(seq, kind="stable")
    s2, n2 = seq[order], n[order]
    u, idx = np.unique(s2, return_index=True)
    sums = np.add.reduceat(n2, idx)
    if u[0] == 0:
        sums = np.concatenate([sums[1:], n2[s2 == 0]])
    return np.asarray(sums, float)


def liquidity(trades: dict[str, np.ndarray]) -> dict[str, float]:
    """One day's taker flow: the 99th percentile taker order (the liquidity ceiling for one of our orders), the
    number of taker orders and the traded notional."""
    a = taker_orders(trades)
    return {"p99": float(np.percentile(a, 99)) if len(a) else 0.0, "takers": len(a),
            "volume": float(a.sum()) if len(a) else 0.0}


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
    capital: float = 100.0            # what the sizes and stops are taken on (bucketed; sizing.bucket)
    pct: Pct = field(default_factory=Pct)
    risk: Risk = field(default_factory=Risk)   # exit timing (exit_taker_after_s, cooldown_s)
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

    def risks_for(self, meta: dict[str, Any], mi: MarketInfo, order_max: float | None = None
                  ) -> list[dict[str, Any]]:
        timing = {"exit_taker_after_s": self.risk.exit_taker_after_s, "cooldown_s": self.risk.cooldown_s}
        levs = leverages(meta) if self.ladder else leverages(meta)[:1]
        vmin = venue_min(mi, meta)
        if order_max:   # a ceiling below two minimum orders would only distort the sizes
            order_max = max(order_max, bucket(2 * 1.2 * vmin) or order_max)
        return [asdict(Risk.for_capital(self.capital, lev, off, pct=self.pct, order_max=order_max,
                                        min_capital=round(min_capital(vmin, off), 2), **timing)) for lev, off in levs]

    def order_max(self, market: str, days: list[str]) -> float | None:
        """The liquidity ceiling for one order: the median of the daily 99th-percentile taker orders over the full
        days, bucketed so it stays put from scan to scan. Cached per completed day."""
        p99 = []
        for d in days:
            cp = self.root / "cache" / "liq" / market / f"{d}.json"
            try:
                p99.append(json.loads(cp.read_text())["p99"])
                continue
            except (OSError, ValueError, KeyError):
                pass
            liq = liquidity(self.store.load_day(market, d).trades)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(liq))
            p99.append(liq["p99"])
        p99 = [x for x in p99 if x > 0]
        return bucket(float(np.median(p99))) if p99 else None

    def full_days(self, market: str, now_us: int) -> list[str]:
        """Completed UTC days on which the recorder was up for at least 20 h and this market has data. The market's
        first recorded day counts only if its own data covers 20 h of it: a listing that went live at 15:00 UTC (or
        was first recorded then) has a partial day, and averaging it as a full one would skew every per-day number."""
        today = day_str(now_us)
        days = self.store.days(market)
        out = []
        for d in days:
            if d >= today:
                continue
            if d not in self._alive_h:
                self._alive_h[d] = self.store.load_day(ALIVE_MARKET, d).bbo_hours()
            if self._alive_h[d] < 20:
                continue
            if d == days[0]:
                ts = self.store.load_day(market, d).bbo["ts"]
                if not len(ts) or day_start_us(d) + US_DAY - int(ts.min()) < 20 * 3600 * S:
                    continue
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
            days = self.full_days(m, now_us)
            every = self.risks_for(meta[m], mis[m], self.order_max(m, days))
            out[m] = {risk_key(r): {"risk": r, "days": {}, "recent": [],
                                    "skip": f"needs ${r['min_capital_usd']:,.2f} of capital at {r['leverage']:g}x (Arcus "
                                            "minimum order)" if r["used_usd"] < r["min_capital_usd"] else ""}
                      for r in every}
            risks = [r for r in every if not out[m][risk_key(r)]["skip"]]
            if not risks:
                continue
            rth = meta[m].get("regularTradingHours")
            for d in days:
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
    capital_usd: float = 0.0    # the capital scanned at
    used_usd: float = 0.0       # the capital the sizes use (less when the market's liquidity ceiling binds)
    min_capital_usd: float = 0.0
    too_small: bool = False     # the capital is under min_capital_usd at this leverage: not backtested
    risk: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def listed_days_ago(meta: dict[str, Any], now_us: int, first_seen: str | None = None,
                    recorder_first: str | None = None) -> float | None:
    """How long the market has been trading, in days: since Arcus listed it (addedTimestamp), or since the recorder
    first saw it when that came after the recorder started (a market pre-listed OFFLINE for months keeps its old
    addedTimestamp when it finally turns ONLINE). None when neither says it is new."""
    ages = []
    try:
        added = float(meta.get("addedTimestamp") or 0)
    except (TypeError, ValueError):
        added = 0.0
    if added > 0:
        ages.append((now_us / 1e6 - added) / 86400)
    if first_seen and recorder_first and first_seen > recorder_first:
        ages.append((now_us - day_start_us(first_seen)) / US_DAY)
    return min(ages) if ages else None


def score_market(market: str, bt: dict[str, Any], now: dict[str, float], pct: Pct | None = None,
                 listed_days: float | None = None) -> list[Candidate]:
    """bt: {risk_key: {"risk", "days", "recent"}} from Scanner.backtest (or one such entry for a single sizing).
    listed_days: days since Arcus listed the market (new listings need NEW_LISTING_MIN_DAYS full days)."""
    if "days" in bt:
        bt = {"": bt}
    out = []
    lev_max = max((e.get("risk", {}).get("leverage", 0.0) for e in bt.values()), default=0.0)
    for entry in bt.values():
        r = entry.get("risk") or {}
        out += _score(market, entry, now, r, at_max=r.get("leverage", 0.0) >= lev_max, pct=pct or Pct(),
                      listed_days=listed_days)
    return out


def _score(market: str, bt: dict[str, Any], now: dict[str, float], risk: dict[str, Any], at_max: bool,
           pct: Pct, listed_days: float | None = None) -> list[Candidate]:
    out = []
    by_cfg: dict[str, list[dict[str, Any]]] = {}
    for _d, rs in sorted(bt["days"].items()):
        for r in rs:
            by_cfg.setdefault(r["config"], []).append(r)
    recent = {r["config"]: r for r in bt["recent"]}
    lev = float(risk.get("leverage", 0.0))
    used = float(risk.get("used_usd") or risk.get("capital_usd") or 100.0)
    min_day, min_tail = -used * pct.go_pnl_day / 100, -used * pct.go_tail_pnl / 100

    def of_cap(x: float) -> str:
        return f"${x:.2f} ({100 * x / used:.2f}% of ${used:,.0f})"

    for name in [c.name for c in MENU]:
        days = by_cfg.get(name, [])
        rec = recent.get(name)
        reasons = []
        if bt.get("skip"):
            reasons.append(bt["skip"])
        elif len(days) < 1:
            reasons.append("no full day of data yet")
        pnl = [d["pnl"] for d in days]
        n = max(1, len(days))
        fills_day = sum(d["maker_fills"] for d in days) / n
        vol_day = sum(d["maker_usd"] for d in days) / n
        pnl_day = sum(pnl) / n
        day_stops = sum(d["day_stops"] for d in days)
        pos_days = sum(1 for p in pnl if p >= 0)
        if days:
            if pnl_day < min_day:
                reasons.append(f"loses {of_cap(-pnl_day)}/day over {len(days)} days")
            if day_stops > MAX_DAY_STOPS:
                reasons.append(f"hit the daily stop {day_stops} times")
            if any(d.get("liquidated") for d in days):
                reasons.append("liquidated")
            elif any(d["killed"] for d in days):
                reasons.append(f"hit the {pct.kill:g}% kill")
            if pos_days < len(days) / 2:
                reasons.append(f"only {pos_days}/{len(days)} days not negative")
            if fills_day < MIN_FILLS_DAY:
                reasons.append(f"too few fills ({fills_day:.1f}/day)")
            if listed_days is not None and listed_days < NEW_LISTING_DAYS and len(days) < NEW_LISTING_MIN_DAYS:
                reasons.append(f"new market (trading {listed_days:.0f} days): {len(days)} of {NEW_LISTING_MIN_DAYS} "
                               "full days")
        if rec is None or rec["hours"] < 12:
            if not bt.get("skip"):
                reasons.append("under 12 h of recent data")
        else:
            if rec["pnl"] < min_day:
                reasons.append(f"last 24 h lost {of_cap(-rec['pnl'])}")
            if rec["tail_pnl"] < min_tail:
                reasons.append(f"last 6 h lost {of_cap(-rec['tail_pnl'])}")
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
            liq_reduces=sum(d.get("liq_reduces", 0) for d in days), capital_usd=float(risk.get("capital_usd", 0.0)),
            used_usd=used, min_capital_usd=float(risk.get("min_capital_usd", 0.0)), too_small=bool(bt.get("skip")),
            risk=risk))
    return out


def _order(c: Candidate) -> tuple[bool, bool, float, float]:
    """GO first; then candidates that were backtested; by maker volume (GO) or PnL (not GO)."""
    return (not c.go, not c.days, -c.volume_day if c.go else -c.pnl_day, -c.pnl_day)


def rank(cands: list[Candidate]) -> list[Candidate]:
    """GO first, by maker volume/day then PnL; one entry per market."""
    best: dict[str, Candidate] = {}
    for c in sorted(cands, key=_order):
        best.setdefault(c.market, c)
    return sorted(best.values(), key=_order)


def best_at_max(cands: list[Candidate]) -> list[Candidate]:
    """Per market, the best setting at its maximum leverage (GO or not): what max leverage does, as asked."""
    best: dict[str, Candidate] = {}
    for c in sorted((c for c in cands if c.at_max and (c.days or c.too_small)), key=_order):
        best.setdefault(c.market, c)
    return sorted(best.values(), key=_order)


def scan(root: Path, *, now_us: int | None = None, markets: list[str] | None = None, workers: int = 6,
         capital: float = 100.0, pct: Pct | None = None, capital_source: str = "fixed", risk: Risk | None = None,
         ladder: bool = True) -> dict[str, Any]:
    """capital: what the sizes and stops are taken on (bucketed here, as the live bot does)."""
    now_us = now_us or time.time_ns() // 1000
    mis = load_markets(root / "markets.json")
    meta = market_meta(root / "markets.json")
    pct = pct or Pct()
    sc = Scanner(root, capital=bucket(capital), pct=pct, risk=risk or Risk(), workers=workers, ladder=ladder)
    have = [m for m in sc.store.markets() if m in mis and (not markets or m in markets) and sc.store.days(m)]
    t0 = time.time()
    bt = sc.backtest(have, now_us, mis, meta)
    cands: list[Candidate] = []
    rec_first = next(iter(sc.store.days(ALIVE_MARKET)), None)
    for m in have:
        age = listed_days_ago(meta[m], now_us, next(iter(sc.store.days(m)), None), rec_first)
        cands += score_market(m, bt[m], market_now(sc.store, m, now_us), pct, age)
    ranked = rank(cands)
    return {"ts_us": now_us, "took_s": round(time.time() - t0, 1), "risk": asdict(sc.risk),
            "capital": {"usd": sc.capital, "source": capital_source, "pct": asdict(pct)},
            "leverage": {"policy": "max, then " + ", ".join(f"{x:g}x" for x in LADDER) if ladder else "max",
                         "caps": LEV_CAPS},
            "markets": len(have), "configs": len(MENU),
            "offline": sorted(m for m in sc.store.markets() if m in meta and meta[m].get("status") != "ONLINE"),
            "top": [c.as_dict() for c in ranked if c.go][:3],
            "ranked": [c.as_dict() for c in ranked],
            "at_max": [c.as_dict() for c in best_at_max(cands)],
            "all": [c.as_dict() for c in cands]}


def as_result(d: dict[str, Any]) -> Result:
    return Result(**d)


def limits(root: Path, markets: list[str] | None = None, now_us: int | None = None) -> list[dict[str, Any]]:
    """Per market, the least capital the bot can trade it with (its smallest order still 1.2x the Arcus minimum) and
    the most one order can use (the liquidity ceiling), from markets.json and the recorded taker flow."""
    now_us = now_us or time.time_ns() // 1000
    mis, meta = load_markets(root / "markets.json"), market_meta(root / "markets.json")
    sc = Scanner(root)
    out = []
    for m in sorted(mis):
        if markets and m not in markets:
            continue
        lev, off = max_leverage(meta[m])
        vmin = venue_min(mis[m], meta[m])
        days = sc.full_days(m, now_us) if sc.store.days(m) else []
        om = sc.order_max(m, days)
        vols = []
        for d in days:
            with contextlib.suppress(OSError, ValueError, KeyError):
                vols.append(json.loads((root / "cache" / "liq" / m / f"{d}.json").read_text())["volume"])
        two = min(2.0, off)
        out.append({"market": m, "venue_min_usd": vmin, "leverage": lev, "leverage_off": off,
                    "min_capital_max_lev": min_capital(vmin, off), "min_capital_2x": min_capital(vmin, two),
                    "order_max_usd": om, "days": len(days), "taker_volume_day": float(np.median(vols)) if vols else 0.0,
                    "full_use_to_max_lev": om * 2 * 1.25 / lev if om else None,
                    "full_use_to_2x": om * 2 * 1.25 / 2 if om else None})
    return out


def limits_table(root: Path, markets: list[str] | None = None) -> str:
    def usd(x: float | None) -> str:
        return f"{x:,.0f}" if x else "-"

    lines = ["least capital: the smallest order (off-hours on RWA perps) stays >= 1.2x the Arcus minimum order.",
             "order ceiling: the 99th percentile taker order (median over the recorded full days). Past it orders "
             "stop growing,",
             "so capital beyond 'fully used up to' only adds margin cushion (a lower effective leverage).", "",
             f"{'market':<13}{'min order':>10}{'max lev':>10}{'least $':>10}{'least $':>10}{'ceiling':>10}"
             f"{'fully used up to':>20}{'takers $':>12}{'days':>5}",
             f"{'':<13}{'':>10}{'in/off':>10}{'@max lev':>10}{'@2x':>10}{'1 order':>10}{'@max lev':>10}{'@2x':>10}"
             f"{'per day':>12}{'':>5}"]
    for r in sorted(limits(root, markets), key=lambda r: r["min_capital_max_lev"]):
        lev = f"{r['leverage']:g}/{r['leverage_off']:g}"
        lines.append(f"{r['market']:<13}{r['venue_min_usd']:>10,.2f}{lev:>10}{r['min_capital_max_lev']:>10,.2f}"
                     f"{r['min_capital_2x']:>10,.2f}{usd(r['order_max_usd']):>10}{usd(r['full_use_to_max_lev']):>10}"
                     f"{usd(r['full_use_to_2x']):>10}{r['taker_volume_day']:>12,.0f}{r['days']:>5}")
    return "\n".join(lines)


def capital_line(res: dict[str, Any]) -> str:
    cap = res.get("capital") or {}
    p = cap.get("pct") or asdict(Pct())
    return (f"capital ${cap.get('usd', 100):,.2f} ({cap.get('source', 'fixed')}); stops: position {p['position_stop']:g}%, "
            f"day {p['daily_stop']:g}%, kill {p['kill']:g}% of the capital each setup uses")


def table(res: dict[str, Any], limit: int = 25) -> str:
    """Plain-text ranking for the terminal: the best setting per market, then each market at its max leverage."""
    lines = [f"scan of {res['markets']} markets x {res['configs']} settings in {res['took_s']} s; "
             f"{len(res['top'])} pass all checks", capital_line(res), ""]
    head = (f"{'':3}{'market':<12} {'setting':<30} {'uses':>7} {'order':>6} {'fills/d':>7} {'volume/d':>9} "
            f"{'pnl/d':>7} {'worst':>7} {'24h':>6}  why not")

    def rows(cs: list[dict[str, Any]]) -> list[str]:
        return [f"{i:>2} {c['market']:<12} {c['config']:<30} {c.get('used_usd', 0):>7,.0f} "
                f"{c.get('order_usd', 0):>6,.0f} {c['fills_day']:>7.0f} {c['volume_day']:>9,.0f} "
                f"{c['pnl_day']:>+7.2f} {c['worst_day']:>+7.2f} {c['recent_pnl']:>+6.2f}  "
                f"{'GO' if c['go'] else '; '.join(c['reasons'])[:80]}" for i, c in enumerate(cs, 1)]
    lines += ["best per market (any leverage up to the max):", head]
    lines += rows([r for r in res["ranked"] if r["days"]][:limit])
    if res.get("at_max"):
        lines += ["", "each market at its MAXIMUM leverage:", head]
        lines += rows(res["at_max"][:limit])
    small = [f"{r['market']} (${r.get('min_capital_usd', 0):,.2f})" for r in res["ranked"]
             if r.get("too_small") and not r["days"]]
    if small:
        lines += ["", f"capital too small for (minimum at the best leverage): {', '.join(small)}"]
    waiting = [r["market"] for r in res["ranked"] if not r["days"] and not r.get("too_small")]
    if waiting:
        lines += ["", f"still recording (under a full day of data): {', '.join(waiting)}"]
    return "\n".join(lines)
