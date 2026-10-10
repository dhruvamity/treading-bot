"""Where things are, the account ids and switches from the credentials file, and the owner's settings from
arbitrage/settings.json (all optional).

The trading bot has ONE credentials file, treading-bot/arcus/.env. What this part reads from it: ARCUS_ADDRESS,
ARCUS_ACCOUNT_INDEX, LIGHTER_ACCOUNT_INDEX (the accounts whose balances are read), PROFUNDING_API_KEY (a data
feed's key), ARB_LIVE (the live switch) and TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (where alerts go: the one
Telegram bot). An arbitrage/.env, if there is one, is read too and wins.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path

from arbitrage.rank import Settings

ROOT = Path(os.environ.get("ARB_ROOT") or Path(__file__).resolve().parent.parent)
STATE = ROOT / "state"


KEYS = ("ARCUS_ADDRESS", "ARCUS_ACCOUNT_INDEX", "LIGHTER_ACCOUNT_INDEX", "PROFUNDING_API_KEY", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID")


# What this machine is for (BOT_ROLE, one variable for the three bots; arcus/arcus/common/role.py says why). The
# arbitrage trades, so its executor starts on a machine that trades: "all" (the default) or "trader".
ROLES = ("all", "trader", "recorder", "scout")


def _ours(k: str) -> bool:
    return k in KEYS or k.startswith("ARB_") or k == "BOT_ROLE"


def no_trading(env: dict[str, str] | None = None) -> str:
    """Why this machine must not start the executor ("" when it may)."""
    r = ((read_env() if env is None else env).get("BOT_ROLE") or "all").strip().lower()
    if r not in ROLES:
        return f"BOT_ROLE={r!r} in arcus/.env: must be one of {', '.join(ROLES)}"
    return "" if r in ("all", "trader") else (f"this machine is a {r} (BOT_ROLE={r} in arcus/.env): the executor "
                                              "starts on the trader machine")


def _env_file(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def read_env(path: Path | None = None) -> dict[str, str]:
    """The bot's one file (treading-bot/arcus/.env, our keys only), then arbitrage/.env over it, then the process
    environment over both. Empty values count as not set."""
    p = path or ROOT / ".env"
    shared = {k: v for k, v in _env_file(p.parent.parent / "arcus" / ".env").items() if _ours(k)}
    return {**shared, **_env_file(p), **{k: v for k, v in os.environ.items() if _ours(k) and v}}


# What `arbitrage set` accepts: name -> (lowest, highest, what it is). Everything else in Settings is a constant.
ADJUSTABLE: dict[str, tuple[float, float, str]] = {
    "min_hold_h": (0, 720, "hours a position is kept at least (a stop still closes it)"),
    "max_hold_h": (0, 8760, "hours after which a position is closed whatever it pays; 0 = no limit"),
    "exit_edge_apr": (-50, 500, "close (after min_hold_h) once the last 24 h and the next payment pay less than this "
                                "% a year"),
    "stop_pct": (0, 50, "stop and take profit, % from the entry on both legs; 0 = dynamic (half the way to "
                        "liquidation)"),
    "min_edge_apr": (0, 1000, "open only when the difference pays at least this % a year on the position"),
    "max_breakeven_h": (1, 720, "open only when this many hours of funding pay for getting in and out"),
    "max_leverage": (1, 50, "never use more leverage than this; 50 = the highest the venues allow at that hour"),
    "profunding_side": (0, 1, "1 = which venue is short is ProFunding's answer (its LighterRH / Arcus row) and "
                              "nothing else; 0 = the venues' own rates decide"),
    "max_margin_usd": (0, 1e7, "never more than this many dollars of each venue's money as margin; 0 = no limit "
                               "(margin_use of the smaller balance)"),
    "max_notional_usd": (0, 1e7, "never more than this many dollars a leg; 0 = no limit (use a small number for the "
                                 "first live run)"),
    "rwa_only": (0, 1, "1 = stocks, indices and commodities only (never a market Arcus calls crypto); 0 = every "
                       "market"),
    "rebalance_share": (0.05, 0.5, "flat, with one venue under this share of the money: say how much to move (0.4 = "
                                   "under 40%)"),
    "drift_close_share": (0, 0.5, "open, with one venue down to this share of the money: close after the next funding "
                                  "payment so it can be moved; 0 = off (the stop closes it instead)"),
    "uneven_wait": (0, 1, "1 = flat with the money uneven (rebalance_share), open nothing until it has been moved; "
                          "0 = go on, sized by the smaller balance"),
    "uneven_remind_min": (1, 1440, "while waiting for that transfer, say how much to move again this often (minutes)"),
    "cycle_h": (0, 720, "close every this many hours and open again on the side the funding points to, whatever it "
                        "pays: volume, at the cost of the fills (`arbitrage cyclecost`); 0 = off"),
    "hold_off_hours": (0, 1, "0 = sized by the margin of the hour it is opened in (more leverage while the stock "
                             "market is open); 1 = always by Arcus's off-hours margin"),
    "hedge_taker": (0, 1, "1 = Lighter's leg rests no order and follows Arcus's fills with (free) taker orders at "
                          "once; 0 = both legs rest maker orders"),
    "margin_use": (0.1, 0.95, "share of the smaller venue's free collateral a position may use"),
    "min_volume_24h": (0, 1e9, "skip markets that traded less than this many dollars in 24 h on either venue"),
    "fill_cost_bp": (0, 50, "what one fill is assumed to cost, in bp"),
    "stop_frac": (0.1, 0.8, "dynamic stop: share of the distance to liquidation"),
    "band_guard": (0, 1, "1 = while the stock market is closed, open nothing whose Arcus stop would sit outside "
                         "Arcus's off-hours price band (it could not be filled there); 0 = open anyway"),
    "stop_early": (0, 0.95, "share of the way to the stop at which it starts closing with limit orders; the stop "
                            "itself is always taker orders; 0 = off"),
    "stop_sigmas": (0, 10, "dynamic stop: at least this many daily moves from the entry, which lowers the leverage "
                           "on a market that moves a lot; 0 = off (the venues' highest leverage)"),
    "chase_s": (1, 600, "seconds an unfilled leg follows the price as maker after the other leg has filled"),
    "max_cross_bp": (0, 100, "then it crosses the spread if that costs no more than this many bp; dearer than that, "
                             "it keeps following until enter_timeout_s"),
    "requote_s": (1, 60, "an order off the best price is moved back at most this often, in seconds"),
    "enter_timeout_s": (10, 7200, "seconds after which an unfinished entry is left as it is and an unfinished exit is "
                                  "completed by crossing"),
}


def settings_path() -> Path:
    return ROOT / "settings.json"


def set_value(name: str, text: str, path: Path | None = None) -> float:
    """Store one setting in arbitrage/settings.json after checking its range. Raises ValueError with what to type."""
    if name not in ADJUSTABLE:
        raise ValueError(f"unknown setting {name!r}: one of {', '.join(sorted(ADJUSTABLE))}")
    lo, hi, _ = ADJUSTABLE[name]
    try:
        word = text.strip().lower()
        v = 0.0 if word in ("auto", "dynamic", "none", "off") else hi if word in ("max", "highest") else float(text)
    except ValueError:
        raise ValueError(f"{name}: {text!r} is not a number") from None
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    p = path or settings_path()
    try:
        cur = json.loads(p.read_text())
    except (OSError, ValueError):
        cur = {}
    cur[name] = v
    p.write_text(json.dumps(cur, indent=1) + "\n")
    return v


@dataclass(frozen=True)
class Config:
    arcus_address: str
    arcus_account: int
    lighter_account: int | None
    profunding_key: str
    settings: Settings


def load(env: dict[str, str] | None = None, settings_file: Path | None = None) -> Config:
    e = read_env() if env is None else env
    over: dict[str, object] = {}
    try:
        raw = json.loads((settings_file or ROOT / "settings.json").read_text())
        names = {f.name: f.type for f in fields(Settings)}
        over = {k: (bool(v) if names[k] == "bool" else float(v)) for k, v in raw.items() if k in names}
    except (OSError, ValueError):
        pass
    li = e.get("LIGHTER_ACCOUNT_INDEX", "").strip()
    return Config(arcus_address=e.get("ARCUS_ADDRESS", "").strip(),
                  arcus_account=int(e.get("ARCUS_ACCOUNT_INDEX") or 0), lighter_account=int(li) if li else None,
                  profunding_key=e.get("PROFUNDING_API_KEY", "").strip(),
                  settings=Settings(**over))   # type: ignore[arg-type]
