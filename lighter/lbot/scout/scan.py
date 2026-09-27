"""The scan: every setup on the menu, on every recorded market, at the owner's capital, each market's maximum leverage
and the owner's stops; then the safety checks and the three lists.

- Full UTC days are backtested once each (from flat at 00:00 UTC, as a fresh run would start) and cached under
  data/scout/cache/<market>/<day>/<key>.json; the key holds the capital, the leverage, the stops and SIM_VERSION, so a
  change in any of them re-runs the days.
- The last 24 hours are re-run on every scan for the setups that could make a list (the best few per market).
- Checks (every list): no kill and no liquidation on any day, at least 5 fills a day, enough recorded days, the market
  not trending or unusually wild in the last hour (when the recorder has it), its data fresh.
- Lists: Most Volume = the most volume among setups whose cost is within the owner's budget (volume_cost, $ lost per
  $1,000 traded); Cheapest = the lowest cost among setups that trade at least 50x the capital a day; Max Volume = the
  most volume whatever it costs. The best setup per market, top 3 markets each.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import multiprocessing as mp
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from lbot.config import Config
from lbot.log import Log
from lbot.scout.sim import MarketRules, Sim, SimCfg, Window
from lbot.scout.tape import DayTape, Tape, day_start
from lbot.trade.sizing import Stops, bucket, min_capital, sizes
from lbot.trade.strategy import BIASES, Setup

log = Log("scan")
US = 1_000_000
SIM_VERSION = 2
MID_SPREADS = (0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0)
GRID_SPREADS = (0.0, 1.0, 2.0)
SMART_SPREADS = (0.0, 0.5, 1.0, 2.0)
TOUCH_SPREADS = (0.0, 0.5)
LISTS = ("most", "cheapest", "max")
BIAS_DAYS = 3
LIST_NAMES = {"most": "Most Volume", "cheapest": "Cheapest", "max": "Max Volume"}


def menu() -> list[Setup]:
    return ([Setup("mid", s, b) for s in MID_SPREADS for b in BIASES]
            + [Setup("grid", s, b) for s in GRID_SPREADS for b in BIASES]
            + [Setup("smart", s) for s in SMART_SPREADS] + [Setup("touch", s) for s in TOUCH_SPREADS])


@dataclass(frozen=True)
class Job:
    root: str
    market: str
    day: str
    capital: float
    leverage: float
    stops: tuple[float, float, float]
    rules: MarketRules
    setups: tuple[str, ...]
    start_us: int = 0
    end_us: int = 0
    cfg: SimCfg = field(default_factory=SimCfg)


def cache_key(capital: float, lev: float, stops: tuple[float, float, float]) -> str:
    return f"v{SIM_VERSION}-c{capital:g}-l{lev:g}-s{stops[0]:g}_{stops[1]:g}_{stops[2]:g}"


def merge(a: DayTape, b: DayTape) -> DayTape:
    """Two consecutive days as one tape (the last 24 hours spans two UTC days)."""
    out = DayTape(b.market, b.day)
    for k in ("bbo", "trades", "depth", "stats"):
        x, y = getattr(a, k), getattr(b, k)
        setattr(out, k, {c: np.concatenate([x[c], y[c]]) if c in x and len(x["ts"]) else y[c] for c in y})
    return out


def _result(r: Any) -> dict[str, Any]:
    return {"volume": r.volume, "maker": r.maker_usd, "pnl": r.pnl, "fills": r.maker_fills, "taker_fills": r.taker_fills,
            "hours": r.hours, "quoting_h": r.quoting_s / 3600, "pos_stops": r.pos_stops, "day_stops": r.day_stops,
            "killed": r.killed, "liquidated": r.liquidated, "min_pnl": r.min_pnl, "tail_pnl": r.tail_pnl,
            "edge": r.edge_bps, "markouts": r.markout_bps, "max_pos": r.max_pos_usd}


def run_job(job: Job) -> tuple[str, str, dict[str, dict[str, Any]]]:
    """All of `job.setups` on one window of one market (the window is prepared once)."""
    with contextlib.suppress(AttributeError, OSError):
        os.nice(19)
    tape = Tape(job.root)
    t = tape.load_day(job.market, job.day)
    start, end = job.start_us or day_start(job.day), job.end_us or day_start(job.day) + 86_400 * US
    if job.start_us and job.start_us < day_start(job.day):
        prev = (dt.date.fromisoformat(job.day) - dt.timedelta(days=1)).isoformat()
        t = merge(tape.load_day(job.market, prev), t)
    w = Window(t, start, end, job.cfg)
    sz = sizes(job.capital, job.leverage, Stops(*job.stops))
    out: dict[str, dict[str, Any]] = {}
    for name in job.setups:
        from lbot.trade.strategy import parse
        s = parse(name)
        out[name] = _result(Sim(s.params(), sz, job.rules, job.cfg, s.name).run(w))
    return job.market, job.day, out


def full_days(tape: Tape, market: str, min_hours: float, today: str) -> list[str]:
    out = []
    for d in tape.days(market):
        if d >= today:
            continue
        t = tape.load_day(market, d, kinds=("bbo", "trades"))
        if t.hours() >= min_hours:
            out.append(d)
    return out


def ceiling(tape: Tape, market: str, days: list[str]) -> float | None:
    """The 99th percentile taker transaction, in $: one order never needs to be larger."""
    sizes_: list[np.ndarray] = []
    for d in days[-7:]:
        tr = tape.load(market, d, "trades")
        if not len(tr["ts"]):
            continue
        usd = tr["px"] * tr["sz"]
        _, inv = np.unique(tr["grp"], return_inverse=True)
        sizes_.append(np.bincount(inv, weights=usd))
    if not sizes_:
        return None
    return float(np.percentile(np.concatenate(sizes_), 99))


def now_checks(tape: Tape, market: str, days: list[str], now_us: int) -> dict[str, Any]:
    """The last hour against usual: trending (efficiency ratio), volatility and spread, data age."""
    today = dt.datetime.fromtimestamp(now_us / US, dt.UTC).strftime("%Y-%m-%d")
    t = tape.load(market, today, "bbo")
    if len(t["ts"]) < 10:
        return {"known": False}
    age = (now_us - int(t["ts"][-1])) / US
    mids = (t["bid"] + t["ask"]) / 2
    mins = np.arange(now_us - 3600 * US, now_us + 1, 60 * US)
    k = np.searchsorted(t["ts"], mins, side="right") - 1
    ok = k >= 0
    m = mids[k[ok]]
    if len(m) < 20:
        return {"known": False, "age_s": age}
    steps = np.abs(np.diff(m)).sum()
    er = float(abs(m[-1] - m[0]) / steps) if steps > 0 else 0.0
    vol = float(np.std(np.diff(np.log(m))))
    usual: list[float] = []
    for d in days[-7:]:
        b = tape.load(market, d, "bbo")
        if len(b["ts"]) < 100:
            continue
        g = np.arange(int(b["ts"][0]), int(b["ts"][-1]), 60 * US)
        kk = np.searchsorted(b["ts"], g, side="right") - 1
        mm = ((b["bid"] + b["ask"]) / 2)[kk[kk >= 0]]
        if len(mm) > 60:
            usual.append(float(np.std(np.diff(np.log(mm)))))
    vol_x = vol / float(np.median(usual)) if usual and np.median(usual) > 0 else 1.0
    return {"known": True, "age_s": round(age, 1), "trend": round(er, 2), "vol_x": round(vol_x, 2),
            "ok": age < 300 and er < 0.5 and vol_x < 2.0}


def aggregate(rows: list[dict[str, Any]], capital: float) -> dict[str, Any]:
    n = len(rows)
    vol = sum(r["volume"] for r in rows) / n
    pnl = sum(r["pnl"] for r in rows) / n
    return {"days": n, "volume_d": vol, "pnl_d": pnl, "worst": min(r["pnl"] for r in rows),
            "fills_d": sum(r["fills"] for r in rows) / n, "day_stops": sum(r["day_stops"] for r in rows),
            "pos_stops_d": sum(r["pos_stops"] for r in rows) / n,
            "killed": any(r["killed"] for r in rows), "liquidated": any(r["liquidated"] for r in rows),
            "cost_1k": -pnl / vol * 1000 if vol > 0 else math.inf, "quoting_h": sum(r["quoting_h"] for r in rows) / n,
            "x_capital": vol / capital if capital else 0.0}


def checks(a: dict[str, Any], now: dict[str, Any], min_days: int) -> list[str]:
    why = []
    if a["days"] < min_days:
        why.append(f"{a['days']} day(s) of data (needs {min_days})")
    if a["killed"] or a["liquidated"]:
        why.append("liquidated" if a["liquidated"] else "hit the kill")
    if a["fills_d"] < 5:
        why.append("under 5 fills a day")
    if now.get("known") and not now.get("ok"):
        if now.get("age_s", 0) >= 300:
            why.append("no fresh data")
        elif now.get("trend", 0) >= 0.5:
            why.append("trending now")
        else:
            why.append("wild now")
    return why


class Scanner:
    def __init__(self, cfg: Config, *, workers: int | None = None) -> None:
        self.cfg = cfg
        self.root = cfg.data_dir / "tape"
        self.tape = Tape(self.root)
        self.out = cfg.data_dir / "scout"
        self.workers = workers or max(1, (os.cpu_count() or 2) - 1)
        self.simcfg = replace(SimCfg(), maker_us=cfg.latency.maker_us, taker_us=cfg.latency.taker_us,
                              quotes_per_min=cfg.requests.quotes_per_min, step_us=cfg.loop_ms * 1000)

    def _cache(self, market: str, day: str, key: str) -> Path:
        return self.out / "cache" / market / day / f"{key}.json"

    def scan(self, capital: float, *, markets: list[str] | None = None, stops: tuple[float, float, float],
             volume_cost: float, lev_cap: float | None, now_us: int | None = None, full24: bool = False,
             min_days: int | None = None) -> dict[str, Any]:
        from lbot.scout.record import load_markets
        t0 = time.time()
        now_us = now_us or int(time.time() * US)
        today = dt.datetime.fromtimestamp(now_us / US, dt.UTC).strftime("%Y-%m-%d")
        ms = load_markets(self.cfg.data_dir)
        names = [s.name for s in menu()]
        min_days = min_days if min_days is not None else self.cfg.scout.min_days
        jobs: list[Job] = []
        plan: dict[str, dict[str, Any]] = {}
        for mk in markets or self.tape.markets():
            m = ms.get(mk)
            if m is None:
                continue
            days = full_days(self.tape, mk, self.cfg.scout.full_day_hours, today)
            lev = min(m.max_leverage, lev_cap or m.max_leverage)
            ceil_ = ceiling(self.tape, mk, days)
            cap_used = capital
            if ceil_ and capital * lev / 2.5 > ceil_:
                cap_used = bucket(ceil_ * 2.5 / lev)       # past the liquidity ceiling extra capital only idles
            minc = min_capital(m.min_order_usd(m.last_price or 1.0), lev)
            plan[mk] = {"days": days, "lev": lev, "capital": cap_used, "ceiling": ceil_, "min_capital": minc,
                        "rules": MarketRules(m.tick, m.step, m.min_base, m.min_quote, m.mmf_frac, m.maker_fee,
                                             m.taker_fee)}
            if capital < minc or not days:
                continue
            key = cache_key(cap_used, lev, stops)
            for d in days:
                if not self._cache(mk, d, key).exists():
                    jobs.append(Job(str(self.root), mk, d, cap_used, lev, stops, plan[mk]["rules"], tuple(names),
                                    cfg=self.simcfg))
        if jobs:
            log.info("backtesting", jobs=len(jobs), workers=self.workers)
            with mp.get_context("spawn").Pool(self.workers) as pool:
                for mk, d, res in pool.imap_unordered(run_job, jobs):
                    p = self._cache(mk, d, cache_key(plan[mk]["capital"], plan[mk]["lev"], stops))
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(res))
        # ---- aggregate full days per market and setup
        table: list[dict[str, Any]] = []
        for mk, pl in plan.items():
            if not pl["days"] or capital < pl["min_capital"]:
                continue
            key = cache_key(pl["capital"], pl["lev"], stops)
            per: dict[str, list[dict[str, Any]]] = {}
            for d in pl["days"][-7:]:
                try:
                    res = json.loads(self._cache(mk, d, key).read_text())
                except (OSError, ValueError):
                    continue
                for name, r in res.items():
                    per.setdefault(name, []).append(r)
            nowc = now_checks(self.tape, mk, pl["days"], now_us)
            for name, rows in per.items():
                a = aggregate(rows, pl["capital"])
                a.update(market=mk, setup=name, leverage=pl["lev"], capital=pl["capital"], now=nowc,
                         why=checks(a, nowc, min_days), ceiling=pl["ceiling"])
                table.append(a)
        # ---- the last 24 hours for the ones that could make a list
        self._last24(table, plan, stops, volume_cost, now_us, full24)
        lists = self.lists(table, volume_cost, capital)
        out = {"t": time.time(), "capital": capital, "stops": stops, "volume_cost": volume_cost, "lev_cap": lev_cap,
               "lists": lists, "table": table, "took_s": round(time.time() - t0, 1),
               "markets": {k: {kk: vv for kk, vv in v.items() if kk != "rules"} for k, v in plan.items()},
               "sim_version": SIM_VERSION}
        self.write(out)
        return out

    def _last24(self, table: list[dict[str, Any]], plan: dict[str, Any], stops: tuple[float, float, float],
                volume_cost: float, now_us: int, full: bool) -> None:
        today = dt.datetime.fromtimestamp(now_us / US, dt.UTC).strftime("%Y-%m-%d")
        want: dict[str, set[str]] = {}
        for mk in {r["market"] for r in table}:
            rows = [r for r in table if r["market"] == mk and not r["why"]]
            pick = set()
            for key_, rev in (("volume_d", True), ("cost_1k", False)):
                good = [r for r in rows if key_ == "cost_1k" or r["cost_1k"] <= volume_cost]
                pick |= {r["setup"] for r in sorted(good, key=lambda r: r[key_], reverse=rev)[:3]}
            pick |= {r["setup"] for r in sorted(rows, key=lambda r: r["volume_d"], reverse=True)[:2]}
            if full:
                pick = {r["setup"] for r in rows}
            if pick and len(self.tape.load(mk, today, "bbo")["ts"]) > 100:
                want[mk] = pick
        jobs = [Job(str(self.root), mk, today, plan[mk]["capital"], plan[mk]["lev"], stops, plan[mk]["rules"],
                    tuple(sorted(s)), now_us - 86_400 * US, now_us, self.simcfg) for mk, s in want.items()]
        if not jobs:
            return
        with mp.get_context("spawn").Pool(self.workers) as pool:
            res = {mk: r for mk, _, r in pool.imap_unordered(run_job, jobs)}
        for row in table:
            r = res.get(row["market"], {}).get(row["setup"])
            if r is None:
                continue
            row["last24"] = {"pnl": r["pnl"], "volume": r["volume"], "fills": r["fills"], "hours": r["hours"],
                             "killed": r["killed"] or r["liquidated"]}
            if r["killed"] or r["liquidated"]:
                row["why"].append("kill in the last 24 h")
            elif r["hours"] >= 12 and r["fills"] < 0.3 * row["fills_d"] * r["hours"] / 24:
                row["why"].append("flow dried up in the last 24 h")

    @staticmethod
    def lists(table: list[dict[str, Any]], volume_cost: float, capital: float) -> dict[str, list[dict[str, Any]]]:
        # a Long or Short bias is a view on the price, not an edge: a day or two of drift makes it look good, so
        # the lists show it only once a market has BIAS_DAYS recorded days
        ok = [r for r in table if not r["why"] and (r["days"] >= BIAS_DAYS or not r["setup"].endswith(("Long", "Short")))]

        def best_per_market(rows: list[dict[str, Any]], key: Any, reverse: bool) -> list[dict[str, Any]]:
            seen: set[str] = set()
            out = []
            for r in sorted(rows, key=key, reverse=reverse):
                if r["market"] in seen:
                    continue
                seen.add(r["market"])
                out.append(r)
                if len(out) == 3:
                    break
            return out

        within = [r for r in ok if r["cost_1k"] <= volume_cost]
        busy = [r for r in within if r["x_capital"] >= 50]
        return {"most": best_per_market(within, lambda r: (r["volume_d"], -r["cost_1k"]), True),
                "cheapest": best_per_market(busy, lambda r: (r["cost_1k"], -r["volume_d"]), False),
                "max": best_per_market(ok, lambda r: (r["volume_d"], -r["cost_1k"]), True)}

    def write(self, out: dict[str, Any]) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "latest.json").write_text(json.dumps(out, default=str))
        ceil_ = {k: v.get("ceiling") for k, v in out["markets"].items()}
        (self.out / "ceilings.json").write_text(json.dumps(ceil_))
        txt = report(out)
        (self.out / "report.txt").write_text(txt)
        day = time.strftime("%Y-%m-%d", time.gmtime(out["t"]))
        (self.out / "reports").mkdir(exist_ok=True)
        (self.out / "reports" / f"{day}.txt").write_text(txt)


def fmt_row(i: int, r: dict[str, Any]) -> str:
    l24 = r.get("last24", {}).get("pnl")
    return (f"{i:>2} {r['market']:<9} {r['setup']:<16} {r['leverage']:>3.0f}x ${r['capital']:>7,.0f} "
            f"{r['fills_d']:>6,.0f} {r['volume_d']:>12,.0f} {r['pnl_d']:>8.2f} {r['worst']:>8.2f} "
            f"{(f'{l24:.2f}' if l24 is not None else '-'):>7} ${r['cost_1k']:.3f}/1k {r['days']}d")


def report(out: dict[str, Any]) -> str:
    t = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(out["t"]))
    lines = [f"Lighter scan {t}: capital ${out['capital']:,.0f}, stops {'/'.join(f'{x:g}' for x in out['stops'])}%, "
             f"budget ${out['volume_cost']:.2f} per $1,000, took {out['took_s']}s", ""]
    head = " # market    setup             lev capital  fills/d     volume/d    pnl/d    worst     24h cost       days"
    for k in LISTS:
        lines += [f"{LIST_NAMES[k].upper()}:", head]
        lst = out["lists"].get(k) or []
        lines += [fmt_row(i + 1, r) for i, r in enumerate(lst)] or ["   nothing passes all checks"]
        lines.append("")
    lines.append("EVERY MARKET (its best setup by volume within the budget, else by cost):")
    lines.append(head.replace("cost", "why "))
    by: dict[str, list[dict[str, Any]]] = {}
    for r in out["table"]:
        by.setdefault(r["market"], []).append(r)
    for i, (_mk, rows) in enumerate(sorted(by.items(), key=lambda kv: -max(r["volume_d"] for r in kv[1]))):
        within = [r for r in rows if r["cost_1k"] <= out["volume_cost"]]
        r = max(within, key=lambda r: r["volume_d"]) if within else min(rows, key=lambda r: r["cost_1k"])
        lines.append(fmt_row(i + 1, r) + ("  GO" if not r["why"] else "  " + "; ".join(r["why"])))
    return "\n".join(lines) + "\n"
