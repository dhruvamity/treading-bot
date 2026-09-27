"""The stops, as one state machine the backtest and the running bot both step once per decision.

- position stop: the open position is down `pos_stop_usd` from its entry: cancel the quotes, exit with a reduce-only
  maker order at the touch, cross with a taker order after `exit_taker_after_s`, then rest `cooldown_s`;
- daily stop: the UTC day is down `daily_stop_usd`: the same exit, then no new orders until 00:00 UTC;
- kill: equity `kill_usd` below its peak: close with a taker order and stop until a manual resume;
- run limits (the running bot only): a run's own loss limit (sl=) closes and stops it; a take profit (tp=) or a volume
  target (vol=) closes the position (maker, then taker) and ends the run.
A taker exit costs no fee on a standard Lighter account, only the spread and the depth it walks through.
"""

from __future__ import annotations

from dataclasses import dataclass

US = 1_000_000
DAY_US = 86_400 * US

QUOTE, EXIT, TAKER, IDLE = "quote", "exit", "taker", "idle"


@dataclass
class Decision:
    action: str          # quote | exit (maker, reduce-only, at the touch) | taker (cross the whole position now) | idle
    why: str = ""


@dataclass
class Limits:
    pos_stop_usd: float
    daily_stop_usd: float
    kill_usd: float
    exit_taker_after_s: float = 20.0
    cooldown_s: float = 60.0
    run_loss_usd: float | None = None      # sl=
    run_profit_usd: float | None = None    # tp=
    run_volume_usd: float | None = None    # vol=


class Guard:
    def __init__(self, lim: Limits) -> None:
        self.lim = lim
        self.state = "normal"      # normal | exit_pos | exit_day | day_stopped | cooldown | killed | exit_run | done
        self.day = -1
        self.day_eq: float | None = None
        self.peak: float = -float("inf")
        self.exit_since = 0
        self.cool_until = 0
        self.pos_stops = 0
        self.day_stops = 0
        self.why = ""

    def restore(self, *, day: int, day_eq: float | None, peak: float, state: str) -> None:
        """Carry the day's start and the peak across a restart (the running bot keeps them in its state file)."""
        self.day, self.day_eq, self.peak, self.state = day, day_eq, peak, state

    def resume(self) -> None:
        if self.state in ("killed", "day_stopped", "cooldown"):
            self.state = "normal"

    def step(self, t: int, eq: float, pos: float, entry: float | None, mid: float, *,
             run_pnl: float | None = None, run_vol: float | None = None) -> Decision:
        lim = self.lim
        d = t // DAY_US
        if d != self.day:
            self.day, self.day_eq = d, eq
            if self.state == "day_stopped":
                self.state = "normal"
        self.peak = max(self.peak, eq)
        st = self.state
        # ---- new triggers
        if st not in ("killed", "done"):
            if self.peak - eq > lim.kill_usd:
                self.state, self.why = "killed", f"kill: equity ${self.peak - eq:,.2f} below its peak"
                return Decision(TAKER if pos else IDLE, self.why)
            if lim.run_loss_usd is not None and run_pnl is not None and run_pnl <= -lim.run_loss_usd:
                self.state, self.why = "done", f"run stop: the run lost ${-run_pnl:,.2f}"
                return Decision(TAKER if pos else IDLE, self.why)
            if st not in ("exit_run",):
                if lim.run_profit_usd is not None and run_pnl is not None and run_pnl >= lim.run_profit_usd:
                    self.state, self.exit_since = "exit_run", t
                    self.why = f"take profit: the run is up ${run_pnl:,.2f}"
                elif lim.run_volume_usd is not None and run_vol is not None and run_vol >= lim.run_volume_usd:
                    self.state, self.exit_since = "exit_run", t
                    self.why = f"volume target: ${run_vol:,.0f} traded"
            st = self.state
            if st not in ("exit_day", "day_stopped", "exit_run") and self.day_eq is not None \
                    and eq - self.day_eq < -lim.daily_stop_usd:
                self.state, self.exit_since = "exit_day", t
                self.day_stops += 1
                self.why = f"daily stop: the day is down ${self.day_eq - eq:,.2f}"
            elif st == "normal" and pos and entry is not None and pos * (mid - entry) <= -lim.pos_stop_usd:
                self.state, self.exit_since = "exit_pos", t
                self.pos_stops += 1
                self.why = f"position stop: the position is down ${-pos * (mid - entry):,.2f}"
        st = self.state
        if st == "cooldown" and t >= self.cool_until:
            self.state = st = "normal"
        # ---- what to do now
        if st == "normal":
            return Decision(QUOTE)
        if st in ("exit_pos", "exit_day", "exit_run"):
            if not pos:
                if st == "exit_pos":
                    self.state, self.cool_until = "cooldown", t + int(lim.cooldown_s * US)
                    return Decision(IDLE, "cooling down after a position stop")
                self.state = "day_stopped" if st == "exit_day" else "done"
                return Decision(IDLE, self.why)
            if (t - self.exit_since) / US >= lim.exit_taker_after_s:
                self.exit_since = t
                return Decision(TAKER, self.why + " (maker exit did not fill: taker)")
            return Decision(EXIT, self.why)
        if st in ("killed", "done") and pos:
            return Decision(TAKER, self.why)
        return Decision(IDLE, self.why or st)
