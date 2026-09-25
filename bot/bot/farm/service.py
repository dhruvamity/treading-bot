"""The paper farm: record Arcus live, paper-trade the whole Tread.fi menu on it every hour, keep everything.

    bot farm run --hours 15            record every Arcus perp; every hour replay the menu on the busiest markets
                                       since the start; commit the results (and the finished hours of tape) to git
    bot farm analyze RUN_DIR           the same analysis once, on a run's tape (all markets with data)
    bot farm synth                     the synthetic mechanics study (bot/farm/synth.py): no network needed

A run lives in one folder (default ../research/runs/<UTC start>):

    run.json                 what was run: start, hours, capital, the menu, the simulator parameters
    LEADERBOARD.md           the latest ranking (research/01_strategy_shortlist.md defines the risk labels)
    results/latest.json      every paper run's summary row; results/hour-NN.json the same after hour NN
    candles/<MARKET>.csv.gz  1-minute candles of the market (mid OHLC, spread, trades, volume, taker buys)
    paper/<MARKET>.npz       per-minute paper series of every setting and leverage, and every paper fill
    scout/tape/...           the recorded best bid/offer and trades (bot/scout/tape.py); finished hours are committed
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import fcntl
import functools
import json
import os
import subprocess
import time
import zlib
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np

from bot.common.logging import Log
from bot.farm.analyze import FARM_LEVERAGES, FARM_PCT, leaderboard, run_market, save_rows
from bot.farm.menu import FARM_MENU
from bot.scout.scan import market_meta
from bot.scout.sim import S, SimParams
from bot.scout.tape import TapeStore

log = Log("farm")
ALWAYS = ("BTC-USD", "ETH-USD", "SOL-USD", "HYPE-USD", "QQQ-USD", "SPY-USD", "GLD-USD", "SLV-USD", "NVDA-USD",
          "USO-USD", "TSLA-USD", "XRP-USD")
WARMUP_S = 1800
SERIES_EVERY_H = 4


def utc(ts_us: int) -> str:
    return dt.datetime.fromtimestamp(ts_us / 1e6, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------------------------------------ git
@contextlib.contextmanager
def git_lock(repo: Path) -> Iterator[None]:
    """One writer at a time in the repository (the farm and a person committing by hand)."""
    path = repo / ".git" / "farm.lock"
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def repo_root(p: Path) -> Path | None:
    """The git repository `p` is (or will be) in: found from its nearest existing parent."""
    p = p.resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        out = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=30, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return Path(out.stdout.strip())


def git_commit_push(repo: Path, paths: list[Path], message: str, *, push: bool = True) -> str:
    """Stage `paths`, commit, push the current branch (retrying on network errors). Returns what happened."""
    rel = [str(p.relative_to(repo)) for p in paths if p.exists()]
    if not rel:
        return "nothing to add"
    trailer = ("\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
               "Claude-Session: https://claude.ai/code/session_01Wqt9P7V7FHY78ZJPzwSAXk")
    with git_lock(repo):
        for i in range(0, len(rel), 200):
            subprocess.run(["git", "-C", str(repo), "add", "--", *rel[i:i + 200]], check=True, timeout=300)
        st = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"], timeout=120)
        if st.returncode == 0:
            return "no changes"
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message + trailer], check=True, timeout=300)
        if not push:
            return "committed"
        branch = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                text=True, check=True, timeout=30).stdout.strip()
        for wait in (0, 2, 4, 8, 16):
            time.sleep(wait)
            r = subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", branch], capture_output=True,
                               text=True, timeout=600)
            if r.returncode == 0:
                return "pushed"
            log.warning("farm_push_failed", reason=(r.stderr or r.stdout)[-300:])
        return "commit kept locally (push failed)"


def finished_tape(tape_root: Path, now_s: float) -> list[Path]:
    """Recorder part files of hours that are over (the recorder rewrites the current hour's file every flush)."""
    hour = int(now_s // 3600)
    out = []
    for p in tape_root.rglob("*.npz"):
        stem = p.stem
        if stem.endswith(".tmp"):
            continue
        tail = stem.rsplit("-", 1)[-1]
        if tail.isdigit() and int(tail) < hour:
            out.append(p)
    return sorted(out)


# ------------------------------------------------------------------------------------------------ analysis
def market_activity(store: TapeStore, markets: list[str], start: int, end: int) -> dict[str, float]:
    act = {}
    for m in markets:
        tr = store.load_range(m, start, end).trades
        act[m] = float((tr["px"] * tr["sz"]).sum()) if len(tr["ts"]) else 0.0
    return act


def choose_markets(store: TapeStore, meta: dict[str, dict[str, Any]], start: int, end: int, max_markets: int,
                   only: list[str] | None = None) -> tuple[list[str], dict[str, float]]:
    """The markets to paper-trade: `only`, or the busiest `max_markets` by traded notional plus the ALWAYS list."""
    have = [m for m in store.markets() if m in meta and meta[m].get("status") == "ONLINE"]
    act = market_activity(store, have, start, end)
    if only:
        return [m for m in only if m in act and act[m] > 0], act
    busy = [m for m, v in sorted(act.items(), key=lambda kv: -kv[1]) if v > 0][:max_markets]
    return sorted(set(busy) | {m for m in ALWAYS if act.get(m, 0) > 0}), act


def analyze_window(run_dir: Path, tape_root: Path, markets_json: Path, start: int, end: int, *, capital: float,
                   workers: int, max_markets: int = 20, only: list[str] | None = None,
                   hour_tag: str | None = None, sim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Paper-trade the menu on [start, end) for the chosen markets; write results, candles, series, leaderboard."""
    import bot.farm.policies  # noqa: F401

    meta = market_meta(markets_json)
    store = TapeStore(tape_root)
    markets, act = choose_markets(store, meta, start, end, max_markets, only)
    alive = max(act, key=lambda m: act[m]) if act else "BTC-USD"
    jobs = [{"tape_root": str(tape_root), "market": m, "start": start, "end": end, "meta": meta[m],
             "out": str(run_dir), "capital": capital, "alive_market": "BTC-USD" if "BTC-USD" in act else alive,
             "warmup_s": WARMUP_S, "sim": sim or {}} for m in markets]
    rows: list[dict[str, Any]] = []
    notes = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=get_context("spawn")) as ex:
        for res in ex.map(run_market, jobs):
            rows += res["rows"]
            notes[res["market"]] = {k: res.get(k) for k in ("liq", "order_max", "hours", "note")}
    save_rows(run_dir / "results" / "latest.json", rows)
    if hour_tag:
        save_rows(run_dir / "results" / f"hour-{hour_tag}.json", rows)
    (run_dir / "results" / "markets.json").write_text(json.dumps(
        {"window": [utc(start), utc(end)], "activity_usd": act, "analysed": markets, "notes": notes}, indent=1,
        default=str))
    head = {"window": f"{utc(start)} to {utc(end)} ({(end - start) / 3.6e9:.2f} h)",
            "markets": f"{len(markets)} of {len(act)} recorded: {', '.join(markets)}",
            "capital": f"${capital:g} per paper account; leverage {', '.join(f'{x:g}x' for x in FARM_LEVERAGES)} "
                       "(capped at each market's maximum)",
            "stops": f"position {FARM_PCT.position_stop:g}%, day {FARM_PCT.daily_stop:g}%, kill {FARM_PCT.kill:g}% "
                     "of capital",
            "paper runs": f"{len(rows)} ({len(FARM_MENU)} settings), computed in {time.time() - t0:.0f} s"}
    (run_dir / "LEADERBOARD.md").write_text(leaderboard(rows, "Paper farm leaderboard", head))
    return rows


# ------------------------------------------------------------------------------------------------ the live run
class Farm:
    def __init__(self, run_dir: Path, *, hours: float, every_min: float, capital: float, workers: int,
                 max_markets: int, rest_url: str, ws_url: str, git: bool = True, push: bool = True) -> None:
        self.run_dir = run_dir
        self.hours, self.every_min, self.capital = hours, every_min, capital
        self.workers, self.max_markets = workers, max_markets
        self.rest_url, self.ws_url = rest_url, ws_url
        self.scout = run_dir / "scout"
        self.repo = repo_root(run_dir) if git else None
        self.push = push

    def _state(self) -> dict[str, Any]:
        p = self.run_dir / "run.json"
        if p.exists():
            return dict(json.loads(p.read_text()))
        now = int(time.time() * 1e6)
        st = {"started_us": now, "started": utc(now), "hours": self.hours, "every_min": self.every_min,
              "capital": self.capital, "leverages": list(FARM_LEVERAGES), "stops_pct": FARM_PCT.__dict__,
              "menu": [{"name": e.cfg.name, "mode": e.cfg.mode, "family": e.family, "sources": e.sources,
                        "config": e.cfg.__dict__} for e in FARM_MENU],
              "sim": SimParams().__dict__, "warmup_s": WARMUP_S, "restarts": []}
        self.run_dir.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, indent=1, default=str))
        return st

    def _commit(self, what: str, *, series: bool = True) -> None:
        """Commit the run folder. The per-minute paper series are large (every setting, every fill) and rewritten
        each analysis, so they go in only when `series` (every SERIES_EVERY_H hours and at the end)."""
        if self.repo is None:
            return
        keep = ("run.json", "LEADERBOARD.md", "results", "candles") + (("paper",) if series else ())
        paths = [self.run_dir / x for x in keep]
        paths += [self.scout / "markets.json", self.scout / "recorder.json"]
        paths += finished_tape(self.scout / "tape", time.time())
        try:
            out = git_commit_push(self.repo, paths, f"Paper farm {self.run_dir.name}: {what}", push=self.push)
        except (subprocess.SubprocessError, OSError) as e:
            out = f"git failed: {e}"
        log.info("farm_git", data={"what": what, "result": out})

    async def run(self) -> None:
        from bot.scout.record import ScoutRecorder

        st = self._state()
        t0 = int(st["started_us"])
        end_us = t0 + int(self.hours * 3600 * S)
        if time.time() * 1e6 > t0 + 60 * S:
            st.setdefault("restarts", []).append(utc(int(time.time() * 1e6)))
            (self.run_dir / "run.json").write_text(json.dumps(st, indent=1, default=str))
        rec = ScoutRecorder(self.scout, rest_url=self.rest_url, ws_url=self.ws_url, flush_s=60.0, min_free_gb=3.0)
        left_s = max(60.0, (end_us - time.time() * 1e6) / S + 120)
        rec_task = asyncio.create_task(rec.run(duration_s=left_s))
        loop = asyncio.get_running_loop()
        start = t0 + WARMUP_S * S
        k = max(1, int((time.time() * 1e6 - start) // (self.every_min * 60 * S)) + 1)
        log.info("farm_start", data={"run": str(self.run_dir), "until": utc(end_us)})
        try:
            while True:
                due = start + int(k * self.every_min * 60 * S)
                final = due >= end_us
                due = min(due, end_us)
                while time.time() * 1e6 < due:
                    if rec_task.done():   # the recorder gave up (network): start it again
                        with contextlib.suppress(Exception):
                            rec_task.result()
                        rec = ScoutRecorder(self.scout, rest_url=self.rest_url, ws_url=self.ws_url, flush_s=60.0,
                                            min_free_gb=3.0)
                        rec_task = asyncio.create_task(rec.run(duration_s=max(60.0, (end_us - time.time() * 1e6)
                                                                              / S + 120)))
                    await asyncio.sleep(min(30.0, max(0.5, (due - time.time() * 1e6) / S)))
                rec.flush()
                if not (self.scout / "markets.json").exists():
                    log.warning("farm_no_markets", reason="no market list yet (is Arcus reachable?)")
                    k += 1
                    if final:
                        break
                    continue
                tag = f"{k * self.every_min / 60:05.2f}"
                rows = await loop.run_in_executor(None, functools.partial(
                    analyze_window, self.run_dir, self.scout / "tape", self.scout / "markets.json", start, due,
                    capital=self.capital, workers=self.workers, hour_tag=tag,
                    max_markets=10_000 if final else self.max_markets))   # the last analysis: every market
                log.info("farm_analysis", data={"hour": tag, "rows": len(rows)})
                series = final or k % max(1, round(SERIES_EVERY_H * 60 / self.every_min)) == 0
                await loop.run_in_executor(None, functools.partial(
                    self._commit, f"hour {tag}, {len(rows)} paper runs", series=series))
                k += 1
                if final:
                    break
        finally:
            rec.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(rec_task, 30)
            self._commit("final (recorder stopped)")


# ------------------------------------------------------------------------------------------------ synthetic study
def synth_study(out: Path, *, hours: float = 12.0, workers: int = 3, seeds: tuple[int, ...] = (1, 2),
                profiles: tuple[str, ...] = ("index", "major", "alt"),
                regimes: tuple[str, ...] = ("chop", "trend", "mixed"),
                informed: tuple[float, ...] = (2.5, 0.5)) -> list[dict[str, Any]]:
    """Every menu setting on synthetic tapes: profile x regime x toxicity x seed. Toxicity is the informed taker's
    cost threshold in bps (`informed`): 2.5 = only traders who pay Arcus's 2.25 bp taker fee (low), 0.5 = a fast
    trader who trades almost any stale quote (high); informed_p is 0.5 throughout. The tapes start on a
    Saturday so the skip-US-session variants are comparable (they quote all weekend)."""
    from bot.farm.synth import PROFILES, REGIMES, generate, variant

    start = int(dt.datetime(2026, 9, 26, 0, 0, tzinfo=dt.UTC).timestamp()) * S   # a Saturday
    tape_root = out / "tape"
    store = TapeStore(tape_root)
    jobs = []
    for pn in profiles:
        for rn in regimes:
            for edge in informed:
                for seed in seeds:
                    market = f"{pn.upper()}-{rn.upper()}-E{edge:g}-S{seed}"
                    tape, meta = generate(variant(PROFILES[pn], informed_p=0.5, informed_edge_bps=edge), REGIMES[rn],
                                          hours=hours,
                                          start_us=start, seed=seed * 1000 + zlib.crc32(f"{pn}/{rn}/{edge}".encode()) % 997,
                                          market=market)
                    store.write_part(market, "bbo", "syn", tape.bbo)
                    store.write_part(market, "trades", "syn", tape.trades)
                    jobs.append({"tape_root": str(tape_root), "market": market, "start": start + WARMUP_S * S,
                                 "end": start + int(hours * 3600 * S), "meta": meta, "out": str(out),
                                 "capital": 100.0, "alive_market": market, "warmup_s": WARMUP_S,
                                 "scenario": {"profile": pn, "regime": rn, "informed_edge_bps": edge,
                                              "seed": seed}})
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=get_context("spawn")) as ex:
        for job, res in zip(jobs, ex.map(run_market, jobs), strict=True):
            for r in res["rows"]:
                r.update(job["scenario"])
            rows += res["rows"]
    save_rows(out / "results.json", rows)
    return rows


def median(xs: list[float]) -> float:
    return float(np.median(xs)) if xs else float("nan")


def run_dir_for(base: Path, now: float | None = None) -> Path:
    return base / time.strftime("%Y%m%d-%H%M", time.gmtime(now or time.time()))


__all__ = ["Farm", "analyze_window", "git_commit_push", "median", "os", "run_dir_for", "synth_study"]
