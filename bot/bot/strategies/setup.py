"""The setups the owner picks, in Tread.fi's terms: a reference price (Mid or Grid), a spread in bps and a directional
bias. "Mid 0", "Mid +1 Long", "Grid +3 Short".

- Mid: both sides quote `spread` bps from the book's mid and follow it (Tread "Mid"). 0 joins the best bid and ask on a
  one-tick book; a negative spread goes inside the mid, as far as a post-only order can (one tick from the other side).
- Grid: quotes around the last fill (Tread "Grid"): a sell never below the last buy + spread, a buy never above the
  last sell - spread. Flat, it quotes around the mid. A soft reset closes the position at the touch when the mid runs
  GRID_RESET_PCT against it.
- Smart: Mid that leaves a side out for the second its fill would likely lose (the book leans hard against it, or the
  price has just moved against it); the side that reduces the position always stays (bot/strategies/smart.py).
- Bias: Long holds about BIAS_FRAC of the position cap long while quoting both sides (gains when the price rises),
  Short the same short, Neutral none.

The research behind the defaults below is in docs/notes/2026-09-26-tread-style-setups.md (Mid, Grid) and
docs/notes/2026-09-27-profitability.md (Smart).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MODES = ("mid", "grid", "smart")
BIASES = ("neutral", "long", "short")
BIAS_FRAC = 0.5          # the share of the position cap a Long or Short bias holds
GRID_RESET_PCT = 0.5     # Grid soft reset: the mid this far (%) against the position from the last fill
MID_SPREADS = (-1.0, 0.0, 1.0, 2.0, 3.0, 5.0)    # the scout backtests these on every market ...
GRID_SPREADS = (0.0, 1.0, 2.0, 3.0, 5.0)
SMART_SPREADS = (0.0, 1.0, 2.0, 3.0)               # Smart: Neutral only (a bias fights the side it leaves out)
SPREAD_RANGE = (-5.0, 50.0)                        # ... and the owner may run any spread in this range

# Names from before 2026-09-26 that mean the same thing (old buttons, old habits: "/run BTC touch 0bp 40x")
_OLD = (
    (re.compile(r"^touch (\d+(?:\.\d+)?)bp(?:, skip us session)?$"), "mid", 1.0),
    (re.compile(r"^deep (\d+(?:\.\d+)?)bp(?:[, ].*)?$"), "mid", 1.0),
    (re.compile(r"^anchor (\d+(?:\.\d+)?)bp$"), "grid", 1.0),
)
_RETIRED = ("grid ", "rgrid", "rsi", "signal")


@dataclass(frozen=True)
class Setup:
    mode: str = "mid"
    spread: float = 0.0
    bias: str = "neutral"

    @property
    def name(self) -> str:
        """"Mid 0", "Mid +1 Long", "Grid +3 Short", "Smart 0": the scout's setting name (Neutral is left out)."""
        b = "" if self.bias == "neutral" else f" {self.bias.capitalize()}"
        return f"{self.mode.capitalize()} {spread_text(self.spread)}{b}"

    @property
    def label(self) -> str:
        """"Mid 0 · Neutral": what the owner reads."""
        return f"{self.mode.capitalize()} {spread_text(self.spread)} · {self.bias.capitalize()}"

    @property
    def sign(self) -> int:
        return {"long": 1, "short": -1}.get(self.bias, 0)

    @property
    def sid(self) -> str:
        """A short id for Telegram buttons: m0n, m+1l, g+3s, m-0.5n, s0n."""
        return f"{self.mode[0]}{spread_text(self.spread)}{self.bias[0]}"

    def with_(self, **kw: object) -> Setup:
        d = {"mode": self.mode, "spread": self.spread, "bias": self.bias, **kw}
        return Setup(str(d["mode"]), float(d["spread"]), str(d["bias"]))   # type: ignore[arg-type]


def spread_text(x: float) -> str:
    """0 -> "0", 1 -> "+1", -1 -> "-1", 0.5 -> "+0.5"."""
    x = float(x) + 0.0   # -0.0 -> 0.0
    s = f"{x:g}"
    return s if x <= 0 else f"+{s}"


def from_sid(sid: str) -> Setup | None:
    m = re.fullmatch(r"([mgs])([+-]?\d+(?:\.\d+)?)([nls])", sid or "")
    if not m:
        return None
    mode = {"m": "mid", "g": "grid", "s": "smart"}[m.group(1)]
    bias = {"n": "neutral", "l": "long", "s": "short"}[m.group(3)]
    try:
        return checked(Setup(mode, float(m.group(2)), bias))
    except ValueError:
        return None


def checked(s: Setup) -> Setup:
    if s.mode not in MODES:
        raise ValueError(f"unknown mode {s.mode!r}: Mid, Grid or Smart")
    if s.bias not in BIASES:
        raise ValueError(f"unknown bias {s.bias!r}: Long, Neutral or Short")
    lo, hi = SPREAD_RANGE
    if not lo <= s.spread <= hi:
        raise ValueError(f"spread {s.spread:g} bps is out of range ({lo:g} to +{hi:g})")
    if s.mode == "grid" and s.spread < 0:
        raise ValueError("a Grid spread cannot be negative (it would sell below the last buy)")
    return Setup(s.mode, round(float(s.spread), 2) + 0.0, s.bias)


def parse(text: str) -> Setup:
    """"mid 0", "Mid +1 long", "grid 3 short", "mid -1", "smart 0", and the names from before 2026-09-26 ("touch 0bp",
    "deep 3bp", "improve touch", "anchor 3bp"). Raises ValueError with what to type instead."""
    t = " ".join(text.lower().replace(",", " ").replace("·", " ").split())
    if t in ("", "auto"):
        raise ValueError("which setup? e.g. mid 0, mid +1 long, grid +3")
    if t.startswith("improve touch"):
        return Setup("mid", -1.0)
    for rx, mode, _k in _OLD:
        m = rx.match(t)
        if m:
            return checked(Setup(mode, float(m.group(1))))
    words = t.split()
    bias = "neutral"
    if words and words[-1] in BIASES:
        bias = words.pop()
    if len(words) == 1 and re.fullmatch(r"(mid|grid|smart)[+-]?\d+(\.\d+)?", words[0]):   # "mid+1", "grid0"
        m = re.fullmatch(r"(mid|grid|smart)([+-]?\d+(?:\.\d+)?)", words[0])
        assert m is not None
        words = [m.group(1), m.group(2)]
    if len(words) == 2 and words[0] in MODES:
        try:
            spread = float(words[1].removesuffix("bps").removesuffix("bp"))
        except ValueError:
            raise ValueError(f"spread {words[1]!r} is not a number of bps") from None
        return checked(Setup(words[0], spread, bias))
    if len(words) == 1 and words[0] in MODES:
        raise ValueError(f"{words[0].capitalize()} needs a spread, e.g. {words[0]} 0 or {words[0]} +1")
    if t.startswith(_RETIRED):
        raise ValueError(f"{text!r} was retired on 2026-09-26: use Mid or Grid (e.g. mid 0, grid +1)")
    raise ValueError(f"unknown setup {text!r}: e.g. mid 0, mid +1 long, grid +3 short, smart 0")


def menu() -> list[Setup]:
    """What the scout backtests on every market: every Mid and Grid spread, each Neutral, Long and Short, and the
    Smart spreads (Neutral)."""
    return [Setup(mode, s, b) for mode, spreads in (("mid", MID_SPREADS), ("grid", GRID_SPREADS))
            for s in spreads for b in BIASES] + [Setup("smart", s) for s in SMART_SPREADS]
