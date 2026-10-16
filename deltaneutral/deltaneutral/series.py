"""The downloaded files (data.py) as arrays on one five-minute clock, for the backtest and the research.

Prices used:
- Arcus: its candles. Their price is the oracle's while the stock market is open (Arcus's mark is held in a tight band
  around it then) and Arcus's own mark while it is closed (docs: concepts/perpetuals/prices). Arcus liquidates on the
  mark, and its perp trades around it.
- Lighter: its mark price (what it liquidates on); its trades are around it.
A five-minute bar with no candle keeps the last price (a market that did not trade did not move).
"""

from __future__ import annotations

import datetime as dt
import json
import zoneinfo
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from arbitrage import history as H

from . import DATA
from .plan import ARCUS, LIGHTER, Rules

FIVE = 300
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")


def read(name: str, root: Path | None = None) -> np.ndarray:
    """A downloaded file as a float array (rows, columns); empty (0, 2) when it is missing."""
    rows = H.read_rows((root or DATA) / f"{name}.csv")
    if not rows:
        return np.zeros((0, 2))
    return np.array([[float(x) for x in r] for r in rows])


def on_clock(t: np.ndarray, rows: np.ndarray, col: int) -> np.ndarray:
    """Column `col` of `rows` (first column: bar start) on clock `t`, the last value carried forward; NaN before the
    first row."""
    out = np.full(len(t), np.nan)
    if len(rows) == 0:
        return out
    ts = rows[:, 0]
    k = np.searchsorted(ts, t, side="right") - 1
    ok = k >= 0
    out[ok] = rows[k[ok], col]
    return out


def funding_on_clock(t: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """The rate paid at each bar that starts an hour (a fraction of the position's value, + = longs pay); 0 elsewhere
    and where the venue published nothing for that hour."""
    out = np.zeros(len(t))
    if len(rows) == 0:
        return out
    rate = {int(r[0]) // 3600 * 3600: float(r[1]) for r in rows}
    hours = np.flatnonzero(t % 3600 == 0)
    for i in hours:
        out[i] = rate.get(int(t[i]), 0.0)
    return out


def arcus_open(t: float) -> bool:
    """Arcus's regular hours for stocks and ETFs: Monday to Friday, 04:00 to 20:00 New York (holidays not known)."""
    d = dt.datetime.fromtimestamp(t, NEW_YORK)
    return d.weekday() < 5 and 4 <= d.hour < 20


def next_open(after: float, hour_ny: float, weekends: bool = True) -> float:
    """The first moment at or after `after` that is `hour_ny` o'clock in New York (10.5 = 10:30), on a Saturday or
    Sunday only with `weekends`. New York time, so the open stays at the same point of the stock market's day when
    the clocks change."""
    d = dt.datetime.fromtimestamp(after, NEW_YORK).date()
    h, m = int(hour_ny), int(round((hour_ny - int(hour_ny)) * 60))
    for _ in range(10):
        t = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=NEW_YORK).timestamp()
        if t >= after and (weekends or d.weekday() < 5):
            return t
        d += dt.timedelta(days=1)
    return after + 86_400


def blackout(day: str) -> tuple[float, float]:
    """A skip date (an earnings report, "YYYY-MM-DD" in New York): nothing is held from 16:00 New York the day before
    to 16:00 New York the day after, which covers a report before that day's open and one after its close."""
    d = dt.date.fromisoformat(day)
    a = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(16), NEW_YORK).timestamp()
    b = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(16), NEW_YORK).timestamp()
    return a, b


def blocked(t0: float, t1: float, days: list[str] | tuple[str, ...]) -> tuple[float, float] | None:
    """The first skip window that [t0, t1) touches, or None."""
    for day in sorted(days):
        try:
            a, b = blackout(day)
        except ValueError:
            continue
        if t0 < b and t1 > a:
            return a, b
    return None


def hhmm(hour: float) -> str:
    return f"{int(hour):02d}:{int(round((hour - int(hour)) * 60)):02d}"


def cash_session(t: float) -> str:
    """"rth" 09:30-16:00 New York on weekdays, "ext" the rest of Arcus's regular hours, "off" when it is closed."""
    d = dt.datetime.fromtimestamp(t, NEW_YORK)
    if d.weekday() >= 5:
        return "off"
    m = d.hour * 60 + d.minute
    if 570 <= m < 960:
        return "rth"
    return "ext" if 240 <= m < 1200 else "off"


@dataclass
class Market:
    """One market on both venues, on the clock."""
    symbol: str
    t: np.ndarray
    px: dict[str, np.ndarray]          # venue -> mark price
    funding: dict[str, np.ndarray]     # venue -> rate paid at that bar
    rules: dict[str, Rules]


def rules_from(markets: dict, symbol: str) -> dict[str, Rules]:
    """Each venue's margin rules for `symbol` from markets.json (data.py writes it)."""
    a = markets["arcus"][symbol]
    lt = markets["lighter"][symbol]
    imf = float(a["initialMarginFraction"])
    arcus = Rules(imf=imf, imf_off=float(a.get("offHoursInitialMarginFraction") or imf),
                  mmf=float(a["maintenanceMarginFraction"]),
                  taker_bp=float(a.get("effectiveTakerFeePpm") or a.get("baseTakerFeePpm") or 0) / 100,
                  maker_bp=float(a.get("effectiveMakerFeePpm") or 0) / 100,
                  min_notional=float(a.get("minOrderNotional") or 0), step=float(a.get("stepSize") or 0),
                  liq_fee=float(a.get("liquidationFeePpm") or 10_000) / 1e6)
    limf = float(lt["min_initial_margin_fraction"]) / 1e4
    lighter = Rules(imf=limf, imf_off=limf, mmf=float(lt["maintenance_margin_fraction"]) / 1e4,
                    taker_bp=float(lt.get("taker_fee") or 0) * 100, maker_bp=float(lt.get("maker_fee") or 0) * 100,
                    min_notional=float(lt.get("min_quote_amount") or 0),
                    step=10 ** -int(lt.get("size_decimals") or 0),
                    liq_fee=float(lt.get("liquidation_fee") or 1) / 100)
    return {ARCUS: arcus, LIGHTER: lighter}


def load_markets(root: Path | None = None) -> dict:
    return json.loads(((root or DATA) / "markets.json").read_text())


def clock(start: int, end: int) -> np.ndarray:
    return np.arange(start // FIVE * FIVE, end // FIVE * FIVE, FIVE, dtype=np.int64)


def market(symbol: str, t: np.ndarray, root: Path | None = None, markets: dict | None = None) -> Market:
    m = markets or load_markets(root)
    a = read(f"arcus_{symbol}_oracle5m", root)
    lm = read(f"lighter_{symbol}_mark5m", root)
    px = {ARCUS: on_clock(t, a, 4), LIGHTER: on_clock(t, lm, 4)}
    fund = {ARCUS: funding_on_clock(t, read(f"arcus_{symbol}_funding", root)),
            LIGHTER: funding_on_clock(t, read(f"lighter_{symbol}_funding", root))}
    return Market(symbol, t, px, fund, rules_from(m, symbol))


def covered(mk: Market) -> tuple[int, int]:
    """The first and last bar index where both venues have a price."""
    ok = np.isfinite(mk.px[ARCUS]) & np.isfinite(mk.px[LIGHTER])
    idx = np.flatnonzero(ok)
    return (int(idx[0]), int(idx[-1])) if len(idx) else (0, -1)
