"""One deployment at a time: offer the scan's top 3, run the one the owner approves, keep checking it.

Files (paths relative to the project root):
  data/scout/latest.json        the last scan, written by `bot scout run`
  state/pilot.json              the active deployment and the last offer
  state/pilot_events.jsonl      what happened; the Telegram bot posts every new line
  config/sessions/pilot.yaml    the session the runner starts with, rewritten on each approval

After every scan, review():
- nothing running: offer the top 3 when they differ from the last offer;
- running and still GO: nothing to do;
- running and NO-GO: pause quoting on it (the runner's reduce-only exit works any position off), say why, and offer
  the current top 3. It unpauses by itself once the same setting is GO on two scans in a row;
- another GO candidate with at least 1.5x its maker volume: suggest a switch. The bot never switches on its own.
Approving a different candidate closes the current position first (runner "close" command), then starts the new one.

Sizes follow the account: the scout scans at the subaccount's equity (bot/scout/capital.py) and the engine re-sizes
from it at start and at 00:00 UTC (bot/common/sizing.py), never above 1.25x the last capital a GO scan covered. Each
GO review records that capital (kv "sizing_ok"), so the sizes grow with the account as long as the backtest agrees.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml

from bot.common.config import SizingDefaults
from bot.common.sizing import INV_BUFFER
from bot.scout.scan import BY_NAME
from bot.scout.sim import Config, Risk
from bot.telegram.control import Control
from bot.venues.base import Venue
from bot.venues.symbols import canonical_base

SESSION = "pilot"
SWITCH_X = 1.5
RESUME_AFTER_GO_SCANS = 2
MAX_SCAN_AGE_S = 90 * 60


def base_of(market: str) -> str:
    return canonical_base(Venue.ARCUS, market)


def session_for(market: str, cfg: Config, risk: Risk, *, live: bool, account_index: int = 0,
                sizing: SizingDefaults | None = None) -> dict[str, Any]:
    """The session file for one scan candidate: the same strategy settings, leverage, sizes and dollar stops the
    backtest used. Outside an RWA perp's session the cap and the order size shrink with the off-hours margin.
    The `sizing` block holds the recipe behind those numbers, so the engine can re-size them from the account's
    equity (bot/common/sizing.py): leverage for the sizes, the stops in % of the capital, the liquidity ceiling."""
    off = risk.off_scale()
    z = sizing or SizingDefaults()
    used = risk.used
    s: dict[str, Any] = {
        "session_id": SESSION, "venue": "arcus", "account_index": account_index, "market": base_of(market),
        "mode": cfg.mode, "live_enabled": live, "capital_usd": round(used, 2),
        "leverage_max": risk.leverage or 3,
        "order_size_usd": round(risk.order_usd, 2), "inventory_cap_usd": round(risk.cap_usd, 2),
        "daily_stop_usd": risk.daily_stop_usd, "pos_stop_usd": risk.pos_stop_usd, "kill_usd": risk.kill_usd,
        "exit_taker_after_s": risk.exit_taker_after_s, "cooldown_s": risk.cooldown_s,
        "stop_loss_pct": round(100 * risk.kill_usd / used, 4),
        "participation_cap_pct": 100,   # not in the backtest: never widen for our share of volume
        "spacing_bps": cfg.spacing_bps, "levels_per_side": cfg.levels,
        "session": {"duration": "24h", "repeat": 3650, "windows_ist": []},
        "off_hours": {"spacing_mult": 1, "size_mult": round(off, 4), "allow_mid": True},
        "sizing": {
            "follow_equity": True, "backtest_capital_usd": risk.capital_usd, "capital_frac": z.capital_frac,
            "max_capital_usd": z.max_capital_usd,
            "leverage": round(risk.cap_usd * INV_BUFFER / used, 6),
            "leverage_off": round((risk.cap_off_usd or risk.cap_usd) * INV_BUFFER / used, 6) if off < 1 else None,
            "order_max_usd": risk.liq_ceiling_usd or risk.order_max_usd or None,
            "position_stop_pct": round(100 * risk.pos_stop_usd / used, 6),
            "daily_stop_pct": round(100 * risk.daily_stop_usd / used, 6),
            "kill_pct": round(100 * risk.kill_usd / used, 6), "min_capital_usd": risk.min_capital_usd,
        },
    }
    if off < 1:
        s["inventory_cap_off_usd"] = round(risk.cap_usd * off, 2)
    if cfg.mode == "mid":
        s.update(execution_style=cfg.style, passive_k_sigma=0.0, skew_kappa=cfg.kappa,
                 level_step_bps=cfg.level_step_bps)
    elif cfg.mode == "grid":
        s.update(reset_threshold_pct=cfg.reset_pct, recentre_after_s=cfg.recentre_after_s,
                 recentre_inventory="skew_exit", skew_kappa=0.0)
    elif cfg.mode == "rgrid":
        s.update(reset_threshold_pct=cfg.reset_pct, rgrid_ema_s=cfg.rgrid_ema_s, rgrid_cut_after_s=cfg.rgrid_cut_after_s,
                 skew_kappa=0.0)
    elif cfg.mode == "signal":
        s["signal"] = {"rsi_low": cfg.rsi_low, "rsi_high": cfg.rsi_high, "tp_bps": cfg.tp_bps, "sl_bps": cfg.sl_bps,
                       "max_hold_min": cfg.max_hold_min, "cooldown_s": cfg.cooldown_min * 60}
    if not cfg.safety:
        s["safety_pause"] = {"move_sigma_1s": 1e9, "spread_x_median": 1e9, "depth_frac_min": 0.0}
    return s


def describe(c: dict[str, Any]) -> str:
    """One line a person can read: market, setting, backtest numbers."""
    size = f" · ${c['order_usd']:,.0f} orders, cap ${c['cap_usd']:,.0f}" if c.get("order_usd") else ""
    if c.get("used_usd"):
        size += f" on ${c['used_usd']:,.0f} of capital"
    return (f"{c['market']} · {c['config']}{size} · {c['fills_day']:.0f} fills/day · "
            f"${c['volume_day']:,.0f} maker volume/day"
            f" · PnL {c['pnl_day']:+.2f}/day (worst day {c['worst_day']:+.2f}, {c['days']} days) · last 24 h "
            f"{c['recent_pnl']:+.2f}")


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

    def running(self) -> bool:
        a = self.active()
        return a is not None and self.control.is_running(a["mode"])

    # ---------------------------------------------------------------- review after each scan
    def review(self, scan: dict[str, Any]) -> list[dict[str, Any]]:
        st = self.state()
        out: list[dict[str, Any]] = []
        top = scan.get("top") or []
        keys = [f"{c['market']}|{c['config']}" for c in top]
        a = st.get("active")
        if not a or not self.control.is_running(a["mode"]):
            if keys != st.get("offer_keys"):
                st["offer_keys"] = keys
                if top:
                    out.append(self.event("offer", "Best setups right now (backtested with the $100 account's stops):",
                                          top=top))
                else:
                    out.append(self.event("offer", "Nothing passes all checks right now: no setup is both near "
                                          "breakeven or better on the long window and still working now.", top=[]))
            self.save(st)
            return out
        cand = next((c for c in scan.get("all", []) if c["market"] == a["market"] and c["config"] == a["config"]), None)
        base = base_of(a["market"])
        if cand is None:
            out.append(self.event("review", f"{a['market']} is not in this scan (no fresh data?). Leaving it as is."))
        elif not cand["go"]:
            st["go_streak"] = 0
            if not st.get("paused_by_scout"):
                why = "; ".join(cand["reasons"])
                self.control.set_pause(a["mode"], base, f"scout: {why}")
                st["paused_by_scout"] = why
                out.append(self.event("paused", f"Paused {a['market']} ({a['config']}): {why}. Any position is being "
                                      "closed with reduce-only orders. It resumes by itself if the checks pass again "
                                      "twice in a row.", top=top))
        else:
            st["go_streak"] = st.get("go_streak", 0) + 1
            if cand.get("capital_usd"):   # still GO at this capital: the engine may size up to 1.25x it
                self.control.set_sizing_ok(a["mode"], float(cand["capital_usd"]), base)
            if st.get("paused_by_scout") and st["go_streak"] >= RESUME_AFTER_GO_SCANS:
                self.control.clear_pause(a["mode"], base)
                st.pop("paused_by_scout", None)
                out.append(self.event("resumed", f"{a['market']} ({a['config']}) passes all checks again: quoting "
                                      "resumed."))
            best = next((c for c in top if c["market"] != a["market"]), None)
            if best and best["volume_day"] >= SWITCH_X * max(cand["volume_day"], 1.0) and \
                    st.get("suggested") != f"{best['market']}|{best['config']}":
                st["suggested"] = f"{best['market']}|{best['config']}"
                out.append(self.event("suggest", f"{best['market']} ({best['config']}) now backtests at "
                                      f"${best['volume_day']:,.0f}/day vs ${cand['volume_day']:,.0f}/day for "
                                      f"{a['market']}. Switch only if you approve.", top=top))
        st["last_review"] = {"ts": time.time(), "go": bool(cand and cand["go"]),
                             "reasons": cand["reasons"] if cand else ["not in scan"]}
        self.save(st)
        return out

    # ---------------------------------------------------------------- approve / close
    def pick(self, k: int) -> dict[str, Any]:
        scan = self.latest_scan()
        if not scan:
            raise ValueError("no scan yet: start `bot scout run` and wait for the first scan")
        if time.time() - scan["ts_us"] / 1e6 > MAX_SCAN_AGE_S:
            raise ValueError("the last scan is over 90 minutes old; is `bot scout run` running?")
        top = scan.get("top") or []
        if not 1 <= k <= len(top):
            raise ValueError(f"pick 1-{len(top)}" if top else "nothing passes the checks right now")
        c: dict[str, Any] = top[k - 1]
        return c

    def write_session(self, c: dict[str, Any], *, live: bool) -> Path:
        cfg = BY_NAME[c.get("setting") or c["config"]]
        risk = Risk(**c["risk"]) if c.get("risk") else self.risk
        s = session_for(c["market"], cfg, risk, live=live, account_index=self.account_index,
                        sizing=self.control.app.sizing)
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        head = (f"# Written by the pilot {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} for: {describe(c)}\n"
                "# Rewritten on every approval; edit the scout's menu or risk instead of this file.\n")
        self.session_path.write_text(head + yaml.safe_dump(s, sort_keys=False))
        return self.session_path

    async def approve(self, k: int, *, live: bool, by: str, wait_close_s: float = 660.0,
                      wait_start_s: float = 90.0) -> str:
        c = self.pick(k)
        mode = "live" if live else "paper"
        st = self.state()
        a = st.get("active")
        for m in {mode, a["mode"] if a else mode}:
            if self.control.is_running(m):
                self.control.request_close(m, by)
                self.event("closing", f"Closing the running {m} bot's position before switching.")
                t0 = time.time()
                while self.control.is_running(m) and time.time() - t0 < wait_close_s:
                    await asyncio.sleep(5)
                if self.control.is_running(m):
                    raise RuntimeError(f"the {m} bot did not stop within {wait_close_s / 60:.0f} min; check it (/status)")
        self.write_session(c, live=live)
        self.control.clear_sizing_ok(mode)
        rec = self.control.start_run(SESSION, live=live)
        st.update(active={"market": c["market"], "config": c["config"], "mode": mode, "since": time.time(), "by": by,
                          "backtest": c, "pid": rec["pid"], "log": rec["log"]}, go_streak=0)
        st.pop("paused_by_scout", None)
        st.pop("suggested", None)
        self.save(st)
        t0 = time.time()
        while time.time() - t0 < wait_start_s:
            await asyncio.sleep(3)
            if self.control.is_running(mode):
                self.event("deployed", f"Running {describe(c)} in {mode.upper()} (approved by {by}).")
                return f"running in {mode}"
        tail = self.control.log_tail(rec["log"])
        self.event("failed", f"{c['market']} did not start. Last log lines:\n{tail[-1500:]}")
        raise RuntimeError(f"{c['market']} did not start; see {rec['log']}")

    async def close(self, *, by: str, wait_s: float = 660.0) -> str:
        st = self.state()
        a = st.get("active")
        if not a:
            return "nothing is deployed"
        if self.control.is_running(a["mode"]):
            self.control.request_close(a["mode"], by)
            t0 = time.time()
            while self.control.is_running(a["mode"]) and time.time() - t0 < wait_s:
                await asyncio.sleep(5)
        st["active"] = None
        st.pop("paused_by_scout", None)
        self.save(st)
        self.event("closed", f"Closed {a['market']} ({a['config']}) and stopped (by {by}).")
        return "closed"
