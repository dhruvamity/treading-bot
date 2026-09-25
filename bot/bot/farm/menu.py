"""The paper farm's menu: the Tread.fi settings from the research shortlist (research/01_strategy_shortlist.md),
as backtest configs. Every entry names the posts it comes from (S01-S46 in the Tread.fi source library).

All of them quote post-only (maker) orders on one venue. Taker orders happen only when a stop or a cut fires.
"""

from __future__ import annotations

from dataclasses import dataclass

import bot.farm.policies  # noqa: F401  (registers tmid, rsiskew, dgrid)
from bot.scout.sim import Config

US_SESSION = ("09:00-16:30",)   # New York, NYSE trading days (Tread: "avoid NYC volatility time", S02/S21/S27)


@dataclass(frozen=True)
class Entry:
    cfg: Config
    family: str          # aggressive | mid | grid | rgrid | dgrid | signal
    sources: str         # the posts behind it
    note: str = ""


def _mid(k: float, **kw: object) -> Config:
    return Config(f"mid+{k:g}", "mid", spacing_bps=k, safety=False, **kw)  # type: ignore[arg-type]


FARM_MENU: list[Entry] = [
    # ---- volume engines: at or inside the touch
    Entry(Config("mid0", "tmid", spacing_bps=0, safety=False), "aggressive", "S28 (Mid 0), S03/S04 (Mid -1..-3)",
          "the user's hypothesis; Tread's negative Mid spreads reduce to it with maker orders only"),
    Entry(Config("mid0 skew", "tmid", spacing_bps=0, safety=False, kappa=1.0), "aggressive", "S28, S03, S04",
          "sizes skewed against inventory"),
    Entry(Config("join", "mid", style="normal", spacing_bps=0, safety=False), "aggressive", "S04 Mid -1 aggressive",
          "at the best bid and ask"),
    Entry(Config("improve1", "mid", style="aggressive", safety=False), "aggressive", "S04 'aggressive'",
          "one tick inside the best bid and ask"),
    Entry(Config("touch 0bp", "mid", style="normal", spacing_bps=0), "aggressive",
          "the scout menu's `touch 0bp` (Telegram `/run BTC touch 0bp max`)",
          "as `join`, with the scout's safety pause on: the same config the live paper engines run"),
    Entry(Config("mid0 skipUS", "tmid", spacing_bps=0, safety=False, skip_et=US_SESSION), "aggressive",
          "S28 + S02/S21 'avoid NYC hours'"),
    # ---- research additions (not in the posts): Mid 0 only while the market is calm (research/REPORT.md)
    Entry(Config("mid0 vgate", "vmid", spacing_bps=3, level_step_bps=0.25, safety=False), "aggressive",
          "S28 + S02 'stable market' + S07 'Mid 4-5 bps when volatile'",
          "Mid 0 while 1-min volatility x 0.25 < 0.5 bp and no trend; else up to 3 bps"),
    Entry(Config("mid vadapt", "vmid", spacing_bps=5, level_step_bps=0.5, safety=False), "mid",
          "S07, S11 'scale spread with ATR'", "spacing = 0.5 x 1-min volatility, 0 to 5 bps; 5 bps in a trend"),
    # ---- Mid +k: a fixed distance from the mid
    *(Entry(_mid(k), "mid", src) for k, src in ((1, "S34, S29"), (2, "S37"), (3, "S07"), (4, "S07"), (5, "S07"))),
    Entry(Config("mid+1 skew", "mid", spacing_bps=1, kappa=1.0, safety=False), "mid", "S34 + inventory skew"),
    Entry(Config("mid+3 skipUS", "mid", spacing_bps=3, safety=False, skip_et=US_SESSION), "mid", "S07 + S21"),
    # ---- Grid +k: quotes around the last fill, soft reset (S09)
    *(Entry(Config(f"grid+{k:g} r{r:g}", "anchor", spacing_bps=k, reset_pct=r, safety=False), "grid", src)
      for k, r, src in ((1, 0.25, "S27, S35"), (2, 0.5, "S31"), (3, 0.5, "S07"), (5, 0.5, "S05, S08"),
                        (7, 1.0, "S11"), (10, 1.0, "S15, S05"))),
    Entry(Config("grid+1 r0.125", "anchor", spacing_bps=1, reset_pct=0.125, safety=False), "grid", "S01, S12"),
    Entry(Config("grid+3 skipUS", "anchor", spacing_bps=3, reset_pct=0.5, safety=False, skip_et=US_SESSION), "grid",
          "S07 + S21"),
    # ---- RGrid: trailing grid on an EMA of the mid, losing inventory cut (maker approximation of S10's RGrid)
    *(Entry(Config(f"rgrid+{k:g} r{r:g}", "rgrid", spacing_bps=k, reset_pct=r, safety=False), "rgrid", src)
      for k, r, src in ((1, 0.25, "S01, S14, S32"), (2, 0.5, "S06, S32"), (3, 0.5, "S07"))),
    # ---- DGrid approximation (S02, S04, S12, S33)
    Entry(Config("dgrid", "dgrid", spacing_bps=1, safety=False), "dgrid", "S02, S12, S33"),
    Entry(Config("dgrid skipUS", "dgrid", spacing_bps=1, safety=False, skip_et=US_SESSION), "dgrid", "S02 + S21"),
    # ---- Signal: RSI skew (S13)
    *(Entry(Config(f"rsi+{k:g}", "rsiskew", spacing_bps=k, safety=False), "signal", src)
      for k, src in ((3, "S13"), (5, "S05"), (8, "S13"))),
]

BY_NAME: dict[str, Entry] = {e.cfg.name: e for e in FARM_MENU}
assert len(BY_NAME) == len(FARM_MENU), "farm menu names must be unique"
