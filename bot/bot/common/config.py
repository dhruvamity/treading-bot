"""Pydantic config models and YAML loaders.

Static venue facts live in config/venues/*.yaml; [LIVE] values (fees, ticks, margins, minimums, OI caps,
session hours) are fetched at start-up and hourly by `core.liveparams.LiveParams` and are never typed here.
Session files are written by the pilot (bot/scout/pilot.py) or by hand (config/sessions/).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bot.common.errors import ConfigError
from bot.common.sizing import Pct

Auto = Literal["auto"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


def load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    return data


# ------------------------------------------------------------------------------------------------------
# Venue static config (corrected against the live docs; see docs/notes/venue_corrections.md)
# ------------------------------------------------------------------------------------------------------
class EnvUrls(_Model):
    mainnet: str
    testnet: str


class ArcusDms(_Model):
    refresh_s: float = 20
    deadline_s: float = 60
    min_lead_s: float = 5
    max_lead_s: float = 300
    max_fires_per_day: int = 10


class ArcusVenueConfig(_Model):
    venue: Literal["arcus"] = "arcus"
    env: Literal["testnet", "mainnet"] = "testnet"
    rest: EnvUrls
    ws: EnvUrls
    chain_id: dict[str, int]
    eip712_api_key_domain: dict[str, str]
    signing: dict[str, dict[str, int]]
    good_til_days: int = 35
    ip_bucket: dict[str, float]
    pools: dict[str, float]
    batch: dict[str, int]
    ws_limits: dict[str, int]
    taker_speed_bump_ms: int = 50
    dms: ArcusDms = ArcusDms()
    # Requote with modifyOrder (by Arcus order id) instead of cancel + place. Off until `bot selftest --allow-funded`
    # shows a modify moving a resting order (docs/incidents/2026-09-25-live-requotes-refused.md).
    use_modify: bool = False
    live_fields: list[str] = []

    def rest_url(self) -> str:
        return self.rest.mainnet if self.env == "mainnet" else self.rest.testnet

    def ws_url(self) -> str:
        return self.ws.mainnet if self.env == "mainnet" else self.ws.testnet

    def chain(self) -> int:
        return self.chain_id[self.env]


def load_arcus_config(path: Path | str = "config/venues/arcus.yaml") -> ArcusVenueConfig:
    return ArcusVenueConfig.model_validate(load_yaml(path))


# ------------------------------------------------------------------------------------------------------
# Session configs
# ------------------------------------------------------------------------------------------------------
class RequoteCfg(_Model):
    min_ticks: int = 2
    min_frac_of_half_spread: float = 0.25
    min_size_frac: float = 0.20


class SafetyPauseCfg(_Model):
    move_sigma_1s: float = 6.0
    spread_x_median: float = 3.0
    depth_frac_min: float = 0.3
    resume_s: float = 30.0
    # Tick-constrained books (BTC median spread = 1 tick ~ 0.01 bp) would pause on every 4-tick spread;
    # also require an absolute excess and a warm-up before the medians are trusted.
    spread_min_excess_bps: float = 1.0
    warmup_samples: int = 600
    # Floor on the 1-s sigma used by the move rule: a quiet minute gives an EWMA sigma of ~0.3 bp on BTC, which made a
    # routine 2 bp tick a "6 sigma" move 66 s into a paper run (2026-09-23). 6 x 1 bp = 6 bp in one second still trips.
    min_sigma_1s: float = 0.0001


class SessionWindowCfg(_Model):
    duration: str = "8h"
    repeat: int = 1
    windows_ist: list[str] = []
    # New York time windows on NYSE trading days with no new quotes (the position is worked off with a reduce-only
    # maker order at the touch), e.g. ["09:00-16:30"]: the scout's "skip US session" settings
    skip_et: list[str] = []
    skip_events: list[str] = ["cpi", "fomc", "nfp", "earnings"]

    @field_validator("windows_ist")
    @classmethod
    def _windows(cls, v: list[str]) -> list[str]:
        for w in v:
            a, _, b = w.partition("-")
            for t in (a, b):
                hh, _, mm = t.partition(":")
                if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError(f"bad IST window {w!r}; use HH:MM-HH:MM")
        return v

    @field_validator("skip_et")
    @classmethod
    def _skip(cls, v: list[str]) -> list[str]:
        for w in v:
            a, _, b = w.partition("-")
            hm = []
            for t in (a, b):
                hh, _, mm = t.partition(":")
                if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError(f"bad New York window {w!r}; use HH:MM-HH:MM")
                hm.append((int(hh), int(mm)))
            if hm[0] >= hm[1]:
                raise ValueError(f"New York window {w!r} must end after it starts on the same day")
        return v

    def duration_s(self) -> int:
        return parse_duration_s(self.duration)


class OffHoursCfg(_Model):
    spacing_mult: float = 2.0
    size_mult: float = 0.25
    allow_mid: bool = False


class SignalCfg(_Model):
    rsi_len: int = 14
    rsi_low: float = 25
    rsi_high: float = 75
    tp_bps: float = 15
    sl_bps: float = 25
    cooldown_s: float = 300
    max_hold_min: float = 120
    trend_z: float = 1.0


class AutoSpacingCfg(_Model):
    """Grid / RGrid with `spacing_bps: auto`: delta = clamp(k x sigma_1h / sqrt(fills per hour), min, max)."""

    target_fills_per_hour: float = 20
    k_delta: float = 1.0
    delta_min_bps: float = 5
    delta_max_bps: float = 100


class SizingCfg(_Model):
    """How the dollar figures follow the account (bot/common/sizing.py). With follow_equity the engine re-reads the
    account's equity at start and at 00:00 UTC and rewrites order_size_usd, the inventory caps and the dollar stops;
    the numbers in the file are the ones the backtest used."""

    follow_equity: bool = True
    backtest_capital_usd: float          # the capital the scout backtested this setup at
    capital_frac: float = 1.0            # size from this share of the account's equity
    max_capital_usd: float | None = None
    leverage: float                      # sizes: position up to capital x leverage (not the venue leverage setting)
    leverage_off: float | None = None    # the same outside an RWA perp's session (higher initial margin)
    order_max_usd: float | None = None   # the market's liquidity ceiling for one order
    position_stop_pct: float = 1.0
    daily_stop_pct: float = 2.0
    kill_pct: float = 10.0
    min_capital_usd: float = 0.0         # below this the venue minimum order, not the capital, would set the size
    cap_to_backtest: bool = True         # at most 1.25x the capital a backtest covered (the lists' picks); False: the
                                         # owner's own pick follows the balance (trade share, max capital, sl= still apply)

    @model_validator(mode="after")
    def _checks(self) -> SizingCfg:
        if not 0 < self.capital_frac <= 1:
            raise ValueError("sizing.capital_frac must be in (0, 1]")
        if self.leverage <= 0:
            raise ValueError("sizing.leverage must be positive")
        return self


class MMSession(_Model):
    session_id: str
    venue: Literal["arcus"] = "arcus"
    account_index: int = 1
    market: str
    mode: Literal["mid", "grid", "rgrid", "signal", "anchor"] = "mid"
    live_enabled: bool = False
    capital_usd: float = 35
    leverage_max: float = 5
    bias: Literal["neutral", "long_skew", "short_skew"] = "neutral"
    bias_size_usd: float = 0
    execution_style: Literal["aggressive", "normal", "passive"] = "normal"
    offset_bps: float = 0
    spacing_bps: float | Auto = "auto"
    levels_per_side: int | Auto = "auto"
    level_step_bps: float = 2.0
    order_size_usd: float | Auto = "auto"
    inventory_cap_usd: float = 30
    inventory_cap_off_usd: float | None = None   # Arcus RWA off-hours (higher initial margin); None = same cap
    skew_kappa: float = 1.0
    passive_k_sigma: float = 1.0
    reset_threshold_pct: float = 0.25
    recentre_after_s: float = 120
    recentre_inventory: Literal["skew_exit", "maker_unwind"] = "skew_exit"
    rgrid_ema_s: float = 300
    rgrid_cut_after_s: float = 20
    stop_loss_pct: float = 10
    take_profit_pct: float | None = None
    participation_cap_pct: float = 25
    requote: RequoteCfg = RequoteCfg()
    safety_pause: SafetyPauseCfg = SafetyPauseCfg()
    session: SessionWindowCfg = SessionWindowCfg()
    off_hours: OffHoursCfg = OffHoursCfg()
    signal: SignalCfg = SignalCfg()
    auto_spacing: AutoSpacingCfg = AutoSpacingCfg()
    exit_taker_after_s: float = 60
    # Dollar stops for a small account. The scout's backtests apply exactly these rules (bot/scout/sim.py).
    # None falls back to the app-wide % limits (risk.daily_loss_pct, risk.drawdown_pct of capital_usd).
    daily_stop_usd: float | None = None  # day PnL at or below -X: no new orders until 00:00 UTC, close the position
    pos_stop_usd: float | None = None    # the open position is down X: close it (maker, then taker), then cool down
    kill_usd: float | None = None        # equity X below its peak: flatten and stop until a manual resume
    max_loss_usd: float | None = None    # this run may lose X in all (/run ... sl=X): then flatten and stop. The
                                         # daily stop, kill and session stop never stop it sooner
    run_id: str = ""                     # one deployment (the pilot writes it): the run's loss survives restarts
    cooldown_s: float = 60               # pause after a position stop
    sizing: SizingCfg | None = None      # follow the account's equity (pilot sessions); None = the fixed numbers above

    @model_validator(mode="after")
    def _checks(self) -> MMSession:
        if not 0 <= self.account_index <= 9:
            raise ValueError("account_index must be 0-9 (Arcus subaccount)")
        if not 0 <= self.skew_kappa <= 2:
            raise ValueError("skew_kappa must be in [0, 2]")
        return self


def load_session(path: Path | str) -> MMSession:
    data = load_yaml(path)
    if "strategy" in data:   # dn_carry / dn_hedged_mm / points_overlay, retired with Lighter on 2026-09-25
        raise ConfigError(f"{path}: two-venue (Arcus + Lighter) sessions are no longer supported")
    return MMSession.model_validate(data)


def parse_duration_s(s: str) -> int:
    s = s.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1] in units and s[:-1].replace(".", "", 1).isdigit():
        return int(float(s[:-1]) * units[s[-1]])
    if s.isdigit():
        return int(s)
    raise ValueError(f"bad duration {s!r}; use e.g. 30s, 15m, 8h, 7d")


class AlertsCfg(_Model):
    telegram_enabled: bool = True
    min_interval_s: float = 30
    levels: list[str] = ["WARN", "CRIT"]


class RiskLimitsCfg(_Model):
    daily_loss_pct: float = 3.0
    drawdown_pct: float = 10.0
    liq_distance_sigma: float = 4.0
    liq_resume_sigma: float = 6.0
    heartbeat_timeout_s: float = 60.0
    max_leverage_mm: float = 5.0


class SizingDefaults(_Model):
    """The capital the scout backtests at and the pilot sizes from, and the stops as % of it (bot/common/sizing.py)."""

    capital_usd: float | Auto = "auto"   # auto: the Arcus subaccount's equity; else a fixed amount
    paper_capital_usd: float = 100       # auto with no keys or an unfunded account (the Docker scout, paper runs)
    capital_frac: float = 1.0            # trade this share of the equity
    max_capital_usd: float | None = None
    position_stop_pct: float = 1.0
    daily_stop_pct: float = 2.0
    kill_pct: float = 10.0
    go_pnl_day_pct: float = 0.25
    go_tail_pnl_pct: float = 0.50

    @model_validator(mode="after")
    def _checks(self) -> SizingDefaults:
        if not 0 < self.capital_frac <= 1:
            raise ValueError("sizing.capital_frac must be in (0, 1]")
        if not 0 < self.position_stop_pct <= self.daily_stop_pct <= self.kill_pct:
            raise ValueError("sizing stops must satisfy 0 < position_stop_pct <= daily_stop_pct <= kill_pct")
        return self

    def pct(self) -> Pct:
        return Pct(self.position_stop_pct, self.daily_stop_pct, self.kill_pct, self.go_pnl_day_pct,
                   self.go_tail_pnl_pct)


class AppConfig(_Model):
    data_dir: str = "data"
    logs_dir: str = "logs"
    reports_dir: str = "reports"
    state_dir: str = "state"
    capital_usd_total: float = 100
    alerts: AlertsCfg = AlertsCfg()
    risk: RiskLimitsCfg = RiskLimitsCfg()
    sizing: SizingDefaults = SizingDefaults()
    extra: dict[str, Any] = Field(default_factory=dict)

    # Paper, testnet and live never share state: a paper run must not leave orders the live reconciler would
    # chase, and the live guardian must only ever see the LIVE bot's heartbeat.
    def state_db_for(self, mode: str) -> str:
        return str(Path(self.state_dir) / f"{mode}.sqlite")

    def heartbeat_for(self, mode: str) -> str:
        return str(Path(self.state_dir) / f"heartbeat.{mode}")

    def reports_for(self, mode: str) -> Path:
        """Root for this mode's reports; daily reports land in <root>/daily/<date>.md."""
        return Path(self.reports_dir) / mode


def load_app(path: Path | str = "config/app.yaml") -> AppConfig:
    p = Path(path)
    return AppConfig.model_validate(load_yaml(p)) if p.exists() else AppConfig()
