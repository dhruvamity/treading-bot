"""One deployment at a time: offer the scan's top 3, run the one the owner approves, keep checking it.

Files (paths relative to the project root):
  data/scout/latest.json        the last scan, written by `bot scout run`
  state/pilot.json              the active deployment and the last offer
  state/pilot_events.jsonl      what happened; the Telegram bot posts every new line
  config/sessions/pilot.yaml    the session the runner starts with, rewritten on each approval

Three lists (bot/scout/profiles.py): Most Volume (the most volume within the owner's cost per $1,000, /set
volume_cost; the default), Cheapest (the lowest cost per $1,000) and Max Volume (the most volume at any cost). The scan
backtests each market's maximum leverage; a pick runs at any leverage: judged by its list at the backtested one, else
as the owner's own pick.

After every scan, review():
- nothing running: post the Most Volume top 3 when its #1 changes, at most every OFFER_EVERY_S (/top3 shows the
  lists any time);
- running and still in its list (judged by the list it was picked from): nothing to do;
- running and out of its list: pause quoting on it (the runner's reduce-only exit works any position off), say why,
  and offer the current top 3. It unpauses by itself once it is back in its list on two scans in a row;
- another GO candidate with at least 1.5x its maker volume: suggest a switch. The bot never switches on its own.
Approving a different candidate closes the current position first (runner "close" command), then starts the new one.
Besides the lists' top 3, the owner can deploy any market x setup (Mid or Grid, any spread, any bias) x leverage up
to the Arcus maximum (find(), profile "manual"), without waiting for a scan: it is sized for the capital the scout uses now, with the backtest shown
when there is one. The scout reports on it but never pauses it for failing a list, only when its market goes offline.

Sizes follow the account: the scout scans at the subaccount's equity (bot/scout/capital.py) and the engine re-sizes
from it at start and at 00:00 UTC (bot/common/sizing.py), never above 1.25x the capital the run was sized for (a list
pick: the last capital a GO scan covered; the owner's pick: the capital when it started). Each GO review records that
capital (kv "sizing_ok"), so the sizes grow with the account as long as the backtest agrees.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from bot.common import settings
from bot.common.config import SizingDefaults
from bot.common.sizing import INV_BUFFER, loop_ms, min_capital
from bot.scout import profiles
from bot.scout.scan import BY_NAME, config_for, load_markets, market_meta, max_leverage, venue_min
from bot.scout.sim import Config, Risk
from bot.strategies import setup as su
from bot.telegram.control import Control, pause_where
from bot.venues.base import Venue
from bot.venues.symbols import canonical_base

SESSION = "pilot"
SWITCH_X = 1.5
RESUME_AFTER_GO_SCANS = 2
MAX_SCAN_AGE_S = 90 * 60
OFFER_EVERY_S = 3 * 3600


def base_of(market: str) -> str:
    return canonical_base(Venue.ARCUS, market)


def session_for(market: str, cfg: Config, risk: Risk, *, live: bool, account_index: int = 0,
                sizing: SizingDefaults | None = None) -> dict[str, Any]:
    """The session file for one scan candidate: the same strategy settings, leverage, sizes and dollar stops the
    backtest used. Outside an RWA perp's session the cap and the order size shrink with the off-hours margin.
    The `sizing` block holds the recipe behind those numbers, so the engine can re-size them from the account's
    equity (bot/common/sizing.py): leverage for the sizes, the stops in % of the capital, the liquidity ceiling."""
    risk = risk.with_stops(cfg.stops)   # a setting's own stops (the session's sizing keeps them as % of the capital)
    off = risk.off_scale()
    z = sizing or SizingDefaults()
    used = risk.used
    s: dict[str, Any] = {
        "session_id": SESSION, "venue": "arcus", "account_index": account_index, "market": base_of(market),
        "mode": cfg.mode, "live_enabled": live, "capital_usd": round(used, 2),
        "leverage_max": risk.leverage or 3,
        "order_size_usd": round(risk.order_usd, 2), "inventory_cap_usd": round(risk.cap_usd, 2),
        "daily_stop_usd": risk.daily_stop_usd, "pos_stop_usd": risk.pos_stop_usd, "kill_usd": risk.kill_usd,
        "pos_stop_k": risk.pos_stop_k, "pos_stop_max_usd": risk.pos_stop_max_usd if risk.pos_stop_k > 0 else None,
        "exit_taker_after_s": risk.exit_taker_after_s, "cooldown_s": risk.cooldown_s,
        "loop_ms": loop_ms(market),
        "stop_loss_pct": round(100 * risk.kill_usd / used, 4),
        "participation_cap_pct": 100,   # not in the backtest: never widen for our share of volume
        "spacing_bps": cfg.spacing_bps, "levels_per_side": cfg.levels,
        "session": {"duration": "24h", "repeat": 3650, "windows_ist": [], "skip_et": list(cfg.skip_et)},
        "off_hours": {"spacing_mult": 1, "size_mult": round(off, 4), "allow_mid": True},
        "sizing": {
            "follow_equity": True, "backtest_capital_usd": risk.capital_usd, "capital_frac": z.capital_frac,
            "max_capital_usd": z.max_capital_usd,
            "leverage": round(risk.cap_usd * INV_BUFFER / used, 6),
            "leverage_off": round((risk.cap_off_usd or risk.cap_usd) * INV_BUFFER / used, 6) if off < 1 else None,
            "order_max_usd": risk.liq_ceiling_usd or risk.order_max_usd or None,
            "position_stop_pct": round(100 * risk.pos_stop_usd / used, 6),
            "position_stop_k": risk.pos_stop_k,
            "position_stop_max_pct": round(100 * risk.pos_stop_max_usd / used, 6) if risk.pos_stop_k > 0 else 5.0,
            "daily_stop_pct": round(100 * risk.daily_stop_usd / used, 6),
            "kill_pct": round(100 * risk.kill_usd / used, 6), "min_capital_usd": risk.min_capital_usd,
        },
    }
    if off < 1:
        s["inventory_cap_off_usd"] = round(risk.cap_usd * off, 2)
    # Tread-style setup: Mid quotes exactly spacing_bps from the mid (the passive anchor, no volatility term); Grid
    # quotes around the last fill. The bias holds bias_frac of the position cap (bot/strategies/setup.py).
    s.update(bias={1: "long", -1: "short"}.get(cfg.bias, "neutral"), bias_frac=cfg.bias_frac,
             skew_kappa=cfg.kappa)
    if cfg.mode in ("mid", "smart"):
        s.update(execution_style=cfg.style, passive_k_sigma=0.0, level_step_bps=cfg.level_step_bps)
    else:
        s.update(reset_threshold_pct=cfg.reset_pct)
    # the backtest's safety pause has the move and spread rules only (recorded books carry no depth), so the live
    # thin-depth rule stays off too: it paused live runs several times as often as the backtest did
    s["safety_pause"] = {"depth_frac_min": 0.0}
    if not cfg.safety:
        s["safety_pause"].update(move_sigma_1s=1e9, spread_x_median=1e9)
    return s


def describe(c: dict[str, Any]) -> str:
    """One line: market, setup, leverage, sizes, backtest numbers."""
    return f"{what_line(c)} · {numbers(c)}"


def stale_note(scan: dict[str, Any] | None, now: float | None = None) -> str:
    """A warning when the last scan is older than MAX_SCAN_AGE_S (the scout stopped, or a long scan is running)."""
    if not scan:
        return ""
    age = (now or time.time()) - scan["ts_us"] / 1e6
    if age <= MAX_SCAN_AGE_S:
        return ""
    return (f"The lists are from a scan {age / 3600:.1f} h old: is the scout running (bot status)? A long scan at a "
            "new capital keeps the last one up until it ends. You can still run.")


def numbers(c: dict[str, Any]) -> str:
    """Sizes and backtest numbers of a scan row (or of the owner's pick: find() says whether they are at this size)."""
    size = f"${c['order_usd']:,.0f} orders · max position ${1.25 * c['cap_usd']:,.0f} · " if c.get("order_usd") else ""
    if c.get("volume_day") is None:
        return f"{size}not backtested at this leverage yet"
    at = "" if c.get("backtested", True) or not c.get("backtest_capital_usd") else \
        f" at ${float(c['backtest_capital_usd']):,.0f} capital"
    return (f"{size}backtest{at} ${c['volume_day']:,.0f}/day, {_signed(c['pnl_day'])}/day, worst "
            f"{_signed(c['worst_day'])} ({c['days']}d)")


def _signed(x: float) -> str:
    return f"{'+' if x > 0 else '-' if x < 0 else ''}${abs(x):,.2f}"


def _k(x: float) -> str:
    return f"${x / 1000:,.1f}k" if abs(x) >= 1000 else f"${x:,.0f}"


def size_line(c: dict[str, Any]) -> str:
    """"Order $1,760 · Max position $4,400" (empty without sizes)."""
    return f"Order ${c['order_usd']:,.0f} · Max position ${1.25 * c['cap_usd']:,.0f}" if c.get("order_usd") else ""


def backtest_line(c: dict[str, Any]) -> str:
    """"Backtest $13.2k/day · -$3.74/day", or that it has none at this leverage."""
    if c.get("volume_day") is None:
        return "Not backtested at this leverage"
    at = "" if c.get("backtested", True) or not c.get("backtest_capital_usd") else \
        f" at ${float(c['backtest_capital_usd']):,.0f}"
    return f"Backtest{at} {_k(float(c['volume_day']))}/day · {_signed(float(c['pnl_day']))}/day"


class Pilot:
    def __init__(self, root: Path, control: Control, *, account_index: int = 0, risk: Risk | None = None) -> None:
        self.root = root
        self.control = control
        self.account_index = account_index
        self.risk = risk or Risk()
        self.state_path = root / "state" / "pilot.json"
        self.events_path = root / "state" / "pilot_events.jsonl"
        self.session_path = root / "config" / "sessions" / f"{SESSION}.yaml"
        self.scan_path = root / "data" / "scout" / "latest.json"

    # ---------------------------------------------------------------- state
    def state(self) -> dict[str, Any]:
        try:
            d: dict[str, Any] = json.loads(self.state_path.read_text())
            return d
        except (OSError, ValueError):
            return {}

    def save(self, st: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, default=str))
        os.replace(tmp, self.state_path)

    def event(self, kind: str, text: str, **data: Any) -> dict[str, Any]:
        e = {"ts": time.time(), "kind": kind, "text": text, **data}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a") as f:
            f.write(json.dumps(e, default=str) + "\n")
        return e

    def events_since(self, offset: int) -> tuple[list[dict[str, Any]], int]:
        """New event lines after byte `offset` (-1 = start at the end)."""
        try:
            size = self.events_path.stat().st_size
        except OSError:
            return [], 0
        if offset < 0 or offset > size:
            return [], size
        with self.events_path.open() as f:
            f.seek(offset)
            data = f.read()
        out = []
        for line in data.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out, size

    def latest_scan(self) -> dict[str, Any] | None:
        try:
            d: dict[str, Any] = json.loads(self.scan_path.read_text())
            return d
        except (OSError, ValueError):
            return None

    def active(self) -> dict[str, Any] | None:
        a: dict[str, Any] | None = self.state().get("active")
        return a

    def budget(self) -> float:
        """The owner's cost budget for the volume lists (/set volume_cost), in dollars per $1,000 of volume."""
        return settings.volume_cost(settings.load(self.root / "state"))

    def running(self) -> bool:
        a = self.active()
        return a is not None and self.control.is_running(a["mode"])

    # ---------------------------------------------------------------- review after each scan
    def review(self, scan: dict[str, Any]) -> list[dict[str, Any]]:
        from bot.scout import autopilot

        st = self.state()
        out: list[dict[str, Any]] = []
        if autopilot.is_on(self.root / self.control.app.state_dir):
            # the autopilot judges its runs every minute (session, regime, events) and starts its own: no offers,
            # no pauses from here
            st["last_review"] = {"ts": time.time(), "go": True, "reasons": [], "profile": "auto"}
            self.save(st)
            return out
        budget = self.budget()
        top = profiles.top(scan, profiles.DEFAULT, budget)
        keys = [f"{c['market']}|{c['config']}" for c in top]
        a = st.get("active")
        if not a or not self.control.is_running(a["mode"]):
            if keys and keys[0] != st.get("offer_first") and \
                    time.time() - float(st.get("offer_ts") or 0) >= OFFER_EVERY_S:
                st["offer_first"], st["offer_ts"] = keys[0], time.time()
                out.append(self.event("offer", "🏆 NEW #1 SETUP", top=top, profile=profiles.DEFAULT))
            self.save(st)
            return out
        want = canonical(a["config"])   # a run deployed before 2026-09-26 carries the old name ("touch 1bp @ 40x")
        cand = next((c for c in scan.get("all", []) if c["market"] == a["market"] and canonical(c["config"]) == want),
                    None)
        prof = profiles.profile_of(a.get("profile"))
        why_not = profiles.verdict(cand, prof, budget) if cand is not None else ["not in scan"]
        top = profiles.top(scan, prof, budget) if prof.listed else []   # the owner's own pick: no switch suggestions
        base = base_of(a["market"])
        if a["market"] in (scan.get("offline") or []):
            st["go_streak"] = 0
            if not st.get("paused_by_scout"):
                why = "Arcus has taken the market offline"
                self.control.set_pause(a["mode"], base, f"scout: {why}")
                st["paused_by_scout"] = why
                out.append(self.event("paused", f"⏸ PAUSED BY THE SCOUT\n{a['market']} · {a['config']}\n{why}\n"
                                      "Closing any position once it trades", top=top, profile=prof.key))
        elif cand is None:
            if not st.get("missing_noted"):
                st["missing_noted"] = True
                out.append(self.event("review", f"ℹ️ NOT IN THE LAST SCAN\n{a['market']} · {a['config']}\n"
                                      "No fresh data? Left running"))
        elif why_not:
            st["go_streak"] = 0
            if not st.get("paused_by_scout"):
                why = "; ".join(why_not)
                self.control.set_pause(a["mode"], base, f"scout: {why}")
                st["paused_by_scout"] = why
                out.append(self.event("paused", f"⏸ PAUSED BY THE SCOUT\n{a['market']} · {a['config']}\n{why}\n"
                                      "Closing any position · resumes after 2 good scans", top=top,
                                      profile=prof.key))
        else:
            st.pop("missing_noted", None)
            st["go_streak"] = st.get("go_streak", 0) + 1
            if cand.get("capital_usd"):   # still GO at this capital: the engine may size up to 1.25x it
                self.control.set_sizing_ok(a["mode"], float(cand["capital_usd"]), base)
            if st.get("paused_by_scout") and st["go_streak"] >= RESUME_AFTER_GO_SCANS:
                self.control.clear_pause(a["mode"], base)
                st.pop("paused_by_scout", None)
                out.append(self.event("resumed", f"▶️ RESUMED\n{a['market']} · {a['config']}\nBack in {prof.title}"))
            best = next((c for c in top if c["market"] != a["market"]), None)
            if best and best["volume_day"] >= SWITCH_X * max(cand["volume_day"], 1.0) and \
                    st.get("suggested") != f"{best['market']}|{best['config']}":
                st["suggested"] = f"{best['market']}|{best['config']}"
                out.append(self.event("suggest", f"💡 BETTER SETUP\n{best['market']} · {best['config']}\n"
                                      f"{_k(best['volume_day'])}/day vs {_k(cand['volume_day'])}/day now\n"
                                      "Switch only if you want", top=top, profile=prof.key))
        st["last_review"] = {"ts": time.time(), "go": bool(cand is not None and not why_not),
                             "reasons": why_not, "profile": prof.key}
        self.save(st)
        return out

    # ---------------------------------------------------------------- approve / close
    def top(self, profile: str = profiles.DEFAULT) -> list[dict[str, Any]]:
        """The list's top 3 from the last scan."""
        return profiles.top(self.latest_scan(), profiles.profile_of(profile), self.budget())

    def last_scan(self) -> dict[str, Any]:
        """The last scan, however old: an old one is a warning on the run screen (stale_note), never a reason not to
        run (it refused everything once the scan was 90 minutes old, e.g. during a long scan)."""
        scan = self.latest_scan()
        if not scan:
            raise ValueError("no scan yet: start `bot scout run` and wait for the first scan (or /run any setup)")
        return scan

    def pick(self, k: int, profile: str = profiles.DEFAULT, lev: str = "rec") -> dict[str, Any]:
        """Candidate k of a list; lev "max": the same market and setting at the market's maximum leverage."""
        scan = self.last_scan()
        prof = profiles.profile_of(profile)
        top = self.top(prof.key)
        if not 1 <= k <= len(top):
            raise ValueError(f"pick 1-{len(top)}" if top else f"nothing is in the {prof.title} list right now")
        c: dict[str, Any] = top[k - 1]
        if lev == "max":
            m = profiles.at_max(scan, c)
            if m is None:
                raise ValueError(f"{c['market']} has no maximum-leverage backtest for {c.get('setting') or c['config']}")
            c = m
        _in_menu(c)
        # sized for the account now, like the owner's own pick; the list's backtest rides along
        return {**self.find(c["market"], setting_of(c), float(c["leverage"])), "profile": prof.key, "lev": lev}

    def leverages(self, market: str) -> list[float]:
        """The leverages the run screen offers: the market's Arcus maximum, then 20x, 10x, 5x and 2x below it (the
        scan backtests the maximum only; any other leverage runs sized from the account)."""
        from bot.scout.scan import LADDER

        meta = self._meta().get(market)
        if meta is None:
            return []
        top = max_leverage(meta)[0]
        return [top] + [x for x in LADDER if x < top - 1e-9]

    def rows(self, market: str, setting: str | None = None) -> list[dict[str, Any]]:
        """The last scan's backtests of one market (one setting), every leverage, highest leverage first."""
        return sorted((c for c in (self.latest_scan() or {}).get("all") or [] if c["market"] == market
                       and (setting is None or setting_of(c) == setting)), key=lambda c: -float(c["leverage"]))

    def find(self, market: str, setting: str, lev: float | str) -> dict[str, Any]:
        """The owner's own pick: any setup (Mid or Grid, any spread, any bias) on any market at any leverage up to the
        Arcus maximum ("max": that maximum), sized for the capital the scout uses now. It never waits for a scan: the
        backtest at that leverage and capital rides along when the last scan has one (c["backtested"]), else the run
        is marked not backtested at this size, and the live engine sizes it from the account like any other run."""
        setting = config_for(setting).name   # "touch 0bp" -> "Mid 0"; raises ValueError for an unknown setup
        rows = [c for c in (self.latest_scan() or {}).get("all") or [] if c["market"] == market
                and setting_of(c) == setting]
        meta = self._meta().get(market)
        if meta is None:   # no market list (yet): only what the last scan backtested can run
            return self._scan_row(market, setting, lev, rows)
        if meta.get("status") not in (None, "ONLINE"):
            raise ValueError(f"{market} is not an online Arcus market")
        top, off = max_leverage(meta)
        want = top if lev == "max" else float(lev)
        if want > top + 1e-9:
            raise ValueError(f"{market}: Arcus allows at most {top:g}x")
        if want < 1:
            raise ValueError("leverage must be at least 1x")
        cap_now = self.capital_now()
        row = next((r for r in rows if abs(float(r["leverage"]) - want) < 0.01), None)
        liq = next((float(r["risk"].get("liq_ceiling_usd") or 0) for r in rows if r.get("risk")), 0.0) or None
        mi = load_markets(self.scan_path.parent / "markets.json").get(market)
        vmin = venue_min(mi, meta) if mi is not None else 5.0
        z = settings.effective_sizing(self.control.app.sizing, settings.load(self.root / self.control.app.state_dir))
        risk = Risk.for_capital(cap_now, want, min(off, want), pct=z.pct(), order_max=liq,
                                min_capital=round(min_capital(vmin, min(off, want)), 2),
                                exit_taker_after_s=self.risk.exit_taker_after_s, cooldown_s=self.risk.cooldown_s)
        if risk.used < risk.min_capital_usd:
            raise ValueError(f"{market} at {want:g}x needs ${risk.min_capital_usd:,.2f} of capital (Arcus minimum "
                             f"order); sizing for ${cap_now:,.2f}")
        same = row is not None and abs(float(row.get("capital_usd") or 0) - risk.capital_usd) < 0.01
        c = {**(row or {}), "market": market, "setting": setting, "config": f"{setting} @ {want:g}x",
             "leverage": want, "leverage_off": min(off, want), "at_max": abs(want - top) < 0.01,
             "risk": asdict(risk), "capital_usd": risk.capital_usd, "used_usd": risk.used,
             "order_usd": risk.order_usd, "cap_usd": risk.cap_usd, "cap_off_usd": risk.cap_off_usd,
             "backtested": same, "backtest_capital_usd": float(row["capital_usd"]) if row else None}
        return {**c, "profile": "manual", "lev": f"{want:g}x"}

    def _scan_row(self, market: str, setting: str, lev: float | str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """A backtested row of the last scan at that leverage ("max": the market's maximum), as it was sized."""
        c = next((r for r in rows if r.get("at_max")), None) if lev == "max" else \
            next((r for r in rows if abs(float(r["leverage"]) - float(lev)) < 0.01), None)
        if c is None:
            levs = ", ".join(f"{r['leverage']:g}x" for r in rows) or "none"
            raise ValueError(f"{market} {setting}: no backtest at {lev if lev == 'max' else f'{float(lev):g}x'} "
                             f"(has {levs})")
        if c.get("too_small"):
            raise ValueError(f"{market} at {c['leverage']:g}x needs ${c.get('min_capital_usd', 0):,.2f} of capital "
                             "(Arcus minimum order)")
        _in_menu(c)
        return {**c, "profile": "manual", "lev": f"{float(c['leverage']):g}x"}

    def capital_now(self) -> float:
        """The capital to size a run for now: the latest balance of this subaccount (state/balances.jsonl: the scout
        before each scan, the live bot every 5 minutes, Telegram before a /run) under the owner's sizing settings
        (/set capital, trade_share, max_capital). Else the scan's capital (state/scout_capital.json, then the last
        scan's), else a fixed sizing.capital_usd."""
        from bot.core.balances import BalanceLog
        from bot.scout.capital import STATE, choose

        state = self.root / self.control.app.state_dir
        over = settings.load(state)
        z = settings.effective_sizing(self.control.app.sizing, over)
        spec = over.get("capital") or z.capital_usd
        if str(spec).lower() != "auto":
            return choose(spec, None, z)[0]
        rows = [r for r in BalanceLog(state / "balances.jsonl").rows(since=time.time() - 6 * 3600)
                if int(r.get("account") or 0) == self.account_index and float(r.get("equity") or 0) > 0]
        if rows:
            return choose(spec, float(rows[-1]["equity"]), z)[0]
        try:
            usd = float(json.loads((self.root / self.control.app.state_dir / STATE).read_text())["usd"])
            if usd > 0:
                return usd
        except (OSError, ValueError, KeyError, TypeError):
            pass
        scan = self.latest_scan() or {}
        if (scan.get("capital") or {}).get("usd"):
            return float(scan["capital"]["usd"])
        spec = self.control.app.sizing.capital_usd
        if str(spec).lower() != "auto":
            return float(spec)
        raise ValueError("no capital known yet: start `bot scout run` so it can read the account")

    def _meta(self) -> dict[str, dict[str, Any]]:
        try:
            return market_meta(self.scan_path.parent / "markets.json")
        except (OSError, ValueError):
            return {}

    def write_session(self, c: dict[str, Any], *, live: bool, path: Path | None = None) -> Path:
        """The session file for candidate c (path: another file, e.g. to check a setup before it replaces the running
        one). c["max_loss_usd"]: the owner's loss limit for the run (/run ... sl=X); each write is a new run_id."""
        cfg = config_for(setting_of(c))
        risk = Risk(**c["risk"]) if c.get("risk") else self.risk
        s = session_for(c["market"], cfg, risk, live=live, account_index=self.account_index,
                        sizing=self.control.app.sizing)
        s["run_id"] = c.get("run_id") or f"{c['market']}-{time.time_ns() // 1000}"
        s["sizing"]["cap_to_backtest"] = c.get("profile") != "manual"   # the owner's own pick follows the balance
        for k in ("max_loss_usd", "take_profit_usd", "volume_target_usd"):   # the run's limits: sl=, tp=, vol=
            if c.get(k):
                s[k] = float(c[k])
        p = path or self.session_path
        p.parent.mkdir(parents=True, exist_ok=True)
        head = (f"# Written by the pilot {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} for: {describe(c)}\n"
                "# Rewritten on every approval; edit the scout's menu or risk instead of this file.\n")
        p.write_text(head + yaml.safe_dump(s, sort_keys=False))
        return p

    async def approve(self, k: int, *, live: bool, by: str, profile: str = profiles.DEFAULT, lev: str = "rec",
                      wait_close_s: float = 660.0, wait_start_s: float = 90.0) -> str:
        return await self.deploy(self.pick(k, profile, lev), live=live, by=by, wait_close_s=wait_close_s,
                                 wait_start_s=wait_start_s)

    async def deploy(self, c: dict[str, Any], *, live: bool, by: str, wait_close_s: float = 660.0,
                     wait_start_s: float = 90.0) -> str:
        """Run candidate c (from pick() or find()): close whatever runs, write the session, start it."""
        mode = "live" if live else "paper"
        st = self.state()
        a = st.get("active")
        for m in {mode, a["mode"] if a else mode}:
            if self.control.is_running(m):
                self.control.request_close(m, by)
                self.event("closing", f"⏳ CLOSING\nThe running {m.upper()} bot closes first")
            # until its process exits, also when it was already stopping: two bots never share the account
            t0 = time.time()
            while self.control.alive(m) and time.time() - t0 < wait_close_s:
                await asyncio.sleep(3)
            if self.control.alive(m):
                raise RuntimeError(f"the {m} bot did not stop within {wait_close_s / 60:.0f} min; check it (/status)")
        # A new run is the owner's go: a pause from before it (/pauseneworders, or the scout's on the last setup) must
        # not hold it. 2026-09-26: a BTC run sat at 0% quoting behind an old "all markets" pause. A restart of the same
        # run (`bot up`) keeps its pauses.
        cleared = self.control.paused(mode)
        if cleared:
            self.control.clear_pause(mode, None)
        c = {**c, "run_id": f"{c['market']}-{time.time_ns() // 1000}"}   # the engine keeps the run's PnL under it
        self.write_session(c, live=live)
        self.control.clear_sizing_ok(mode)
        rec = self.control.start_run(SESSION, live=live)
        st.update(active={"market": c["market"], "config": c["config"], "mode": mode, "since": time.time(), "by": by,
                          "run_id": c["run_id"],
                          "backtest": c, "pid": rec["pid"], "log": rec["log"], "profile": c["profile"],
                          "lev": c["lev"], "max_loss_usd": c.get("max_loss_usd"),
                          "take_profit_usd": c.get("take_profit_usd"),
                          "volume_target_usd": c.get("volume_target_usd")}, go_streak=0)
        st.pop("paused_by_scout", None)
        st.pop("suggested", None)
        self.save(st)
        t0 = time.time()
        while time.time() - t0 < wait_start_s:
            await asyncio.sleep(3)
            if self.control.is_running(mode):
                prof = profiles.profile_of(c["profile"])
                cfg = config_for(setting_of(c))
                setup = {"market": c["market"], "setting": setting_of(c), "leverage": float(c["leverage"]),
                         "mode": mode, "max_loss_usd": c.get("max_loss_usd"),
                         "take_profit_usd": c.get("take_profit_usd"), "volume_target_usd": c.get("volume_target_usd"),
                         "risk": asdict(Risk(**c["risk"]).with_stops(cfg.stops)) if c.get("risk") else None}
                lines = [f"🟢 {mode.upper()} STARTED", what_line(c), size_line(c), *limit_lines(c),
                         guardian_line(c, self.control.app.risk.drawdown_pct) if live else "",
                         f"Cleared the pause on new orders ({pause_where(cleared)})" if cleared else "", "",
                         backtest_line(c),
                         f"{prof.title}{' · max leverage' if c['lev'] == 'max' else ''} · by {by}"]
                blank = len(lines) - 3
                self.event("deployed", "\n".join(x for i, x in enumerate(lines) if x or i == blank),
                           setup=setup)   # what `bot diagnose --replay` backtests against the live run
                if live:   # the live bot's independent watchdog (bot/ops.py); best effort, never blocks the deploy
                    with contextlib.suppress(Exception):
                        from bot import ops

                        ops.start(self.control.app, "guardian")
                return f"running in {mode}"
        self.event("failed", f"❌ DID NOT START\n{c['market']}\n\n" + start_failure(self.control.log_tail(rec["log"], 200)))
        raise RuntimeError(f"{c['market']} did not start; see {rec['log']}")

    async def close(self, *, by: str, wait_s: float = 660.0) -> str:
        st = self.state()
        a = st.get("active")
        if not a:
            return "nothing is deployed"
        if self.control.is_running(a["mode"]):
            self.control.request_close(a["mode"], by)
            t0 = time.time()
            while self.control.alive(a["mode"]) and time.time() - t0 < wait_s:
                await asyncio.sleep(3)
        st["active"] = None
        st.pop("paused_by_scout", None)
        self.save(st)
        self.event("closed", f"⏹ CLOSED\n{a['market']} · {a['config']}\nBy {by}")
        return "closed"


def setting_of(c: dict[str, Any]) -> str:
    """The setup name of a scan row ("Mid 0" of "Mid 0 @ 40x")."""
    return str(c.get("setting") or c["config"].split(" @ ")[0])


def setup_of(c: dict[str, Any]) -> su.Setup:
    return su.parse(setting_of(c))


def canonical(config: str) -> str:
    """"touch 1bp @ 40x" -> "Mid +1 @ 40x": a scan row's or a deployment's name in today's terms (unchanged when it
    names no setup)."""
    setting, sep, lev = config.partition(" @ ")
    try:
        return f"{su.parse(setting).name}{sep}{lev}"
    except ValueError:
        return config


def _in_menu(c: dict[str, Any]) -> None:
    if setting_of(c) not in BY_NAME:   # a scan made before the menu changed
        raise ValueError(f"{setting_of(c)!r} is no longer in the scout's menu; wait for the next scan and pick again")


def setting_id(name: str) -> str:
    """A short id of a setup for Telegram buttons (64 bytes of data at most): m0n, m+1l, g+3s."""
    return su.parse(name).sid


def setting_by_id(sid: str) -> str | None:
    s = su.from_sid(sid)
    return s.name if s is not None else None


def what_line(c: dict[str, Any]) -> str:
    """"BTC · Mid 0 · Neutral · 40x": market, mode, spread, bias and leverage, as the owner reads a setup."""
    try:
        label = setup_of(c).label
    except ValueError:
        label = setting_of(c)
    lev = c.get("leverage") or str(c.get("config") or "").partition(" @ ")[2].rstrip("x") or 0
    return f"{c['market'].removesuffix('-USD')} · {label} · {float(lev):g}x"


def start_failure(log_text: str) -> str:
    """Why a run did not start, from its log: the doctor's FAIL lines, else the error that ended it, else the last
    lines. The last lines alone were aiohttp's "Unclosed client session" notices, which hid the real error
    ("unknown strategy code for 'smart'") in five failed starts."""
    lines = log_text.splitlines()
    fails = [x for x in lines if x.startswith("[FAIL]")]
    if fails:
        return "\n".join(fails[:6])
    errors = [x for x in lines if re.match(r"[A-Za-z_.]*(Error|Exception)\b", x)]
    if errors:
        return errors[-1][:400]
    return "\n".join(lines[-12:])


def guardian_line(c: dict[str, Any], drawdown_pct: float) -> str:
    """A run stop wider than the guardian's drawdown limit never fires: the guardian cancels everything first
    (2026-10-03: a run stop of 14% of the equity, stopped by the guardian at 10%). Says so at the start; "" otherwise."""
    cap, sl = float(c.get("capital_usd") or 0), float(c.get("max_loss_usd") or 0)
    lim = cap * drawdown_pct / 100
    if cap <= 0 or sl <= lim:
        return ""
    return f"The guardian stops it first, near -${lim:,.2f} ({drawdown_pct:g}% of ${cap:,.0f})"


def limit_lines(c: dict[str, Any], sep: str = "") -> list[str]:
    """The run's own limits: "Run stop -$10.00", "Take profit +$5.00", "Volume target $100.0k" (sep=":" as the
    owner's LIVE RUN template writes them: "Run stop: -$10.00")."""
    return [x for x in (f"Run stop{sep} -${float(c['max_loss_usd']):,.2f}" if c.get("max_loss_usd") else "",
                        f"Take profit{sep} +${float(c['take_profit_usd']):,.2f}" if c.get("take_profit_usd") else "",
                        f"Volume target{sep} {_k(float(c['volume_target_usd']))}" if c.get("volume_target_usd") else "")
            if x]
