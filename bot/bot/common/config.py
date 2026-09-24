"""Pydantic config models and YAML loaders.

Static venue facts live in config/venues/*.yaml; [LIVE] values (fees, ticks, margins, minimums, OI caps,
session hours) are fetched at start-up and hourly by `core.liveparams.LiveParams` and are never typed here.
Session files follow prompt pack Appendix E4; `auto` hands the field to the Autopilot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bot.common.errors import ConfigError

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
# Venue static configs (E5, corrected against the live docs; see docs/notes/venue_corrections.md)
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
    live_fields: list[str] = []

    def rest_url(self) -> str:
        return self.rest.mainnet if self.env == "mainnet" else self.rest.testnet

    def ws_url(self) -> str:
        return self.ws.mainnet if self.env == "mainnet" else self.ws.testnet

    def chain(self) -> int:
        return self.chain_id[self.env]


class LighterLimits(_Model):
    rest_req_per_min: int = 60
    sendtx_per_min: int = 60
    pending_per_market: int = 16
    pending_per_account: int = 500
    active_per_market: int = 1000
    active_per_account: int = 1500
    ws_msgs_per_min: int = 200
    ws_batch_max: int = 15
    rest_batch_max: int = 50
    ws_ping_s: int = 60
    # Design caps, tighter than the venue's, from the spec (grid <= 5/side, <= 10 actions in flight per market)
    design_active_per_market: int = 30
    design_pending_per_market: int = 10


class LighterVenueConfig(_Model):
    venue: Literal["lighter_rh"] = "lighter_rh"
    env: Literal["testnet", "mainnet"] = "mainnet"
    rest: EnvUrls
    ws: EnvUrls
    signing_chain_id: dict[str, int]
    api_key_index_default: int = 4
    reserved_api_key_indices: list[int] = [0, 1, 2, 3, 157]
    tx_types: dict[str, int]
    order_type: dict[str, int]
    tif: dict[str, int]
    cancel_all_tif: dict[str, int]
    tx_lifetime_ms: int = 599_000
    order_expiry_days: int = 28
    auth_token: dict[str, float]
    limits_standard: LighterLimits = LighterLimits()
    latency_ms: dict[str, Any]
    min_quote_usd: float = 10
    usdg_asset_index: int = 3
    deposit_contract: str = ""
    live_fields: list[str] = []

    def rest_url(self) -> str:
        return self.rest.mainnet if self.env == "mainnet" else self.rest.testnet

    def ws_url(self) -> str:
        return self.ws.mainnet if self.env == "mainnet" else self.ws.testnet

    def chain(self) -> int:
        return self.signing_chain_id[self.env]


def load_arcus_config(path: Path | str = "config/venues/arcus.yaml") -> ArcusVenueConfig:
    return ArcusVenueConfig.model_validate(load_yaml(path))


def load_lighter_config(path: Path | str = "config/venues/lighter_rh.yaml") -> LighterVenueConfig:
    return LighterVenueConfig.model_validate(load_yaml(path))


class UniverseConfig(_Model):
    record: list[str]
    phase1_trade: list[str] = []
    both_venues: list[str] = []
    excluded_pairs: dict[str, str] = {}
    book_levels: int = 100


def load_universe(path: Path | str = "config/universe.yaml") -> UniverseConfig:
    return UniverseConfig.model_validate(load_yaml(path))


# ------------------------------------------------------------------------------------------------------
# Session configs (Appendix E4)
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

    def duration_s(self) -> int:
        return parse_duration_s(self.duration)


class OffHoursCfg(_Model):
    spacing_mult: float = 2.0
    size_mult: float = 0.25
    allow_mid: bool = False


class BlendCfg(_Model):
    reference: list[str] = ["lighter_rh:BTC", "pyth"]
    weights: list[float] = [0.6, 0.4]
    stale_ms: int = 2000
    dev_bps: float = 25.0
    basis_ewma_halflife_s: float = 600.0

    @model_validator(mode="after")
    def _w(self) -> BlendCfg:
        if len(self.weights) != len(self.reference):
            raise ValueError("blend.weights must match blend.reference")
        return self


class SignalCfg(_Model):
    rsi_len: int = 14
    rsi_low: float = 25
    rsi_high: float = 75
    tp_bps: float = 15
    sl_bps: float = 25
    cooldown_s: float = 300
    max_hold_min: float = 120
    trend_z: float = 1.0


class AutopilotCfg(_Model):
    confirm_evals: int = 5
    min_dwell_min: float = 30
    er_trend: float = 0.5
    er_range: float = 0.3
    trend_z: float = 2.0
    oer_min: float = 1.5
    markout_stop_bps: float = -2.0
    target_fills_per_hour: float = 20
    k_delta: float = 1.0
    delta_min_bps: float = 5
    delta_max_bps: float = 100
    vol_max_1h: float = 0.03
    tight_spread_bps: float = 3.0
    thin_depth_usd: float = 2_000
    deep_depth_usd: float = 20_000


class MMSession(_Model):
    session_id: str
    venue: Literal["arcus", "lighter_rh"]
    account_index: int = 1
    market: str
    mode: Literal["auto", "mid", "grid", "rgrid", "dgrid", "blend", "signal"] = "auto"
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
    recentre_inventory: Literal["skew_exit", "maker_unwind", "hedge_other_venue"] = "skew_exit"
    rgrid_ema_s: float = 300
    rgrid_cut_after_s: float = 20
    rgrid_trend_tilt_beta: float = 0.0
    stop_loss_pct: float = 10
    take_profit_pct: float | None = None
    participation_cap_pct: float = 25
    requote: RequoteCfg = RequoteCfg()
    safety_pause: SafetyPauseCfg = SafetyPauseCfg()
    session: SessionWindowCfg = SessionWindowCfg()
    off_hours: OffHoursCfg = OffHoursCfg()
    blend: BlendCfg = BlendCfg()
    signal: SignalCfg = SignalCfg()
    autopilot: AutopilotCfg = AutopilotCfg()
    exit_taker_after_s: float = 60
    # Dollar stops for a small account. The scout's backtests apply exactly these rules (bot/scout/sim.py).
    # None falls back to the app-wide % limits (risk.daily_loss_pct, risk.drawdown_pct of capital_usd).
    daily_stop_usd: float | None = None  # day PnL at or below -X: no new orders until 00:00 UTC, close the position
    pos_stop_usd: float | None = None    # the open position is down X: close it (maker, then taker), then cool down
    kill_usd: float | None = None        # equity X below its peak: flatten and stop until a manual resume
    cooldown_s: float = 60               # pause after a position stop

    @model_validator(mode="after")
    def _checks(self) -> MMSession:
        if not 0 <= self.account_index <= 9:
            raise ValueError("account_index must be 0-9 (Arcus subaccount)")
        if not 0 <= self.skew_kappa <= 2:
            raise ValueError("skew_kappa must be in [0, 2]")
        if self.venue == "lighter_rh" and self.mode == "mid":
            raise ValueError("Mid is blocked on Lighter standard (stale-quote risk from 200-300 ms cancels)")
        return self


class DnLeg(_Model):
    account_index: int | None = None
    role: Literal["maker", "hedge"]


class DNSession(_Model):
    session_id: str
    strategy: Literal["dn_carry", "dn_hedged_mm", "points_overlay"]
    market: str
    live_enabled: bool = False
    legs: dict[Literal["arcus", "lighter_rh"], DnLeg]
    collateral_per_leg_usd: float = 25
    leverage_per_leg: float = 3
    entry_ev_bps: float = 5
    exit_ev_bps: float = 0
    horizons_h: list[int] = [4, 8, 12, 24, 48, 72]
    max_hold_h: float = 168
    spread_forecast: Literal["persistence", "ar1", "gbm"] = "persistence"
    risk_lambda: float = 0.5
    hedge_slippage_cap_bps: float = 10
    min_hedge_usd: float = 10
    arcus_reprice_max: int = 5
    arcus_inside_spread_ticks: int = 1
    margin_warn_x_mm: float = 3.0
    margin_crit_x_mm: float = 1.8
    avoid: list[str] = ["earnings", "ex_dividend"]
    # hedged-MM variant
    half_spread_bps: float = 8
    fair_weight_lighter: float = 0.7
    hedge_timeout_s: float = 3
    lighter_down_kill_s: float = 10
    hedge_missing_kill_s: float = 5
    # points overlay
    target_hold_h: float = 12
    churn_penalty: float = 1.0
    session: SessionWindowCfg = SessionWindowCfg(duration="168h", repeat=20)

    @model_validator(mode="after")
    def _legs(self) -> DNSession:
        if set(self.legs) != {"arcus", "lighter_rh"}:
            raise ValueError("DN sessions need exactly one arcus leg and one lighter_rh leg")
        if self.leverage_per_leg > 3:
            raise ValueError("phase 1 caps DN legs at 3x (spec: risk management)")
        return self


def load_session(path: Path | str) -> MMSession | DNSession:
    data = load_yaml(path)
    if "strategy" in data:
        return DNSession.model_validate(data)
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
    drawdown_pct_dn: float = 8.0
    liq_distance_sigma: float = 4.0
    liq_resume_sigma: float = 6.0
    hedge_missing_s: float = 5.0
    heartbeat_timeout_s: float = 60.0
    arcus_pool_freeze_frac: float = 0.05
    arcus_pool_widen_frac: float = 0.20
    max_leverage_mm: float = 5.0
    max_leverage_dn: float = 3.0
    max_actions_per_filled_usd: float = 8.0
    feed_stale_s: float = 2.0
    max_error_rate: float = 0.05


class AppConfig(_Model):
    data_dir: str = "data"
    logs_dir: str = "logs"
    reports_dir: str = "reports"
    state_dir: str = "state"
    capital_usd_total: float = 100
    alerts: AlertsCfg = AlertsCfg()
    risk: RiskLimitsCfg = RiskLimitsCfg()
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
