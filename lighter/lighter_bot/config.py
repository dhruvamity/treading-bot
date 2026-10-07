"""Settings: config/app.yaml, the environment and the credentials file.

The trading bot has ONE credentials file, treading-bot/arcus/.env: the Lighter keys (LIGHTER_*, LBOT_LIVE) live there
next to the Arcus ones and the one Telegram bot's token. A lighter/.env, if there is one, is read too and wins (it is
what this project used before it was part of the one bot)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# treading-bot/lighter (a container sets LBOT_ROOT: the package is installed elsewhere than its config and data)
ROOT = Path(os.environ.get("LBOT_ROOT") or Path(__file__).resolve().parent.parent)


OURS = ("LIGHTER_", "LBOT_", "TELEGRAM_")      # the keys this project reads from a credentials file


def _env_file(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in p.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if v.strip():
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_env(path: Path | None = None) -> dict[str, str]:
    """The credentials: the bot's one file (treading-bot/arcus/.env, our keys only), then lighter/.env over it, then
    the process environment over both. Empty values count as not set."""
    p = path or ROOT / ".env"
    shared = {k: v for k, v in _env_file(p.parent.parent / "arcus" / ".env").items() if k.startswith(OURS)}
    return {**shared, **_env_file(p), **{k: v for k, v in os.environ.items() if k.startswith(OURS) and v}}


@dataclass(frozen=True)
class Endpoints:
    rest: str
    ws: str
    chain_id: int


@dataclass(frozen=True)
class Requests:
    per_min: int = 60
    quotes_per_min: int = 54
    reserve_per_min: int = 6


@dataclass(frozen=True)
class Latency:
    maker_ms: float = 200.0
    taker_ms: float = 300.0
    network_ms: float = 80.0

    @property
    def maker_us(self) -> int:
        return int((self.maker_ms + self.network_ms) * 1000)

    @property
    def taker_us(self) -> int:
        return int((self.taker_ms + self.network_ms) * 1000)


@dataclass(frozen=True)
class SizingCfg:
    capital_usd: float | str = "auto"
    paper_capital_usd: float = 100.0
    capital_frac: float = 1.0
    max_capital_usd: float | None = None
    position_stop_pct: float = 2.0
    daily_stop_pct: float = 5.0
    kill_pct: float = 25.0
    go_pnl_day_pct: float = 0.25
    go_tail_pnl_pct: float = 0.50


@dataclass(frozen=True)
class ScoutCfg:
    every_min: float = 30.0
    full_day_hours: float = 18.0
    min_days: int = 1
    depth_levels: int = 20


@dataclass(frozen=True)
class Creds:
    address: str = ""
    private_key: str = ""
    api_key_index: int = 4
    account_index: int | None = None

    @property
    def can_sign(self) -> bool:
        return bool(self.private_key)


@dataclass(frozen=True)
class Config:
    env: str
    endpoints: Endpoints
    root: Path
    data_dir: Path
    state_dir: Path
    logs_dir: Path
    requests: Requests = field(default_factory=Requests)
    latency: Latency = field(default_factory=Latency)
    sizing: SizingCfg = field(default_factory=SizingCfg)
    scout: ScoutCfg = field(default_factory=ScoutCfg)
    creds: Creds = field(default_factory=Creds)
    live_allowed: bool = False
    telegram_token: str = ""
    telegram_chat: str = ""
    telegram_users: tuple[int, ...] = ()
    loop_ms: int = 500

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.state_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def _sub(cls: type, raw: dict[str, Any] | None) -> Any:
    known = set(cls.__dataclass_fields__)
    return cls(**{k: v for k, v in (raw or {}).items() if k in known})


def load(root: Path | None = None, env: dict[str, str] | None = None) -> Config:
    root = root or ROOT
    raw = yaml.safe_load((root / "config" / "app.yaml").read_text()) or {}
    e = load_env(root / ".env") if env is None else env
    name = e.get("LIGHTER_ENV") or raw.get("env", "mainnet")
    if name not in raw.get("venues", {}):
        raise ValueError(f"LIGHTER_ENV={name!r}: expected one of {', '.join(raw.get('venues', {}))}")
    v = raw["venues"][name]
    dirs = raw.get("dirs", {})
    acct = e.get("LIGHTER_ACCOUNT_INDEX", "").strip()
    users = tuple(int(x) for x in e.get("TELEGRAM_ALLOWED_USER_IDS", "").replace(" ", "").split(",")
                  if x.strip().isdigit())
    return Config(
        env=name,
        endpoints=Endpoints(v["rest"].rstrip("/"), v["ws"], int(v["chain_id"])),
        root=root,
        data_dir=root / dirs.get("data", "data"),
        state_dir=root / dirs.get("state", "state"),
        logs_dir=root / dirs.get("logs", "logs"),
        requests=_sub(Requests, raw.get("requests")),
        latency=_sub(Latency, raw.get("latency")),
        sizing=_sub(SizingCfg, raw.get("sizing")),
        scout=_sub(ScoutCfg, raw.get("scout")),
        creds=Creds(
            address=e.get("LIGHTER_ADDRESS", "").strip(),
            private_key=e.get("LIGHTER_API_PRIVATE_KEY", "").strip().removeprefix("0x"),
            api_key_index=int(e.get("LIGHTER_API_KEY_INDEX") or 4),
            account_index=int(acct) if acct.isdigit() else None,
        ),
        live_allowed=e.get("LBOT_LIVE", "").strip() == "1",
        telegram_token=e.get("TELEGRAM_BOT_TOKEN", "").strip(),      # the trading bot's one Telegram bot
        telegram_chat=e.get("TELEGRAM_CHAT_ID", "").strip(),
        telegram_users=users,
        loop_ms=int(raw.get("loop_ms", 500)),
    )
