# Trading bot (Arcus perps)

A maker bot for Arcus perps, built for a $100 account. The goal is as much maker volume as possible while staying at
breakeven or better. It runs one strategy on one market at a time: the market and setting that test best right now.

## How it works

1. **`bot scout run`** records the best bid/offer and every trade on all online Arcus perps (public data, no keys).
   Every 30 minutes it backtests every strategy setting on every market and ranks them. Each market is tested at its
   **maximum Arcus leverage** (BTC and ETH capped at 20x) and, below that, at 20x, 10x, 5x and 2x; the leverage sets
   the order size. `data/scout/report.txt` shows the best per market and, separately, each market at its maximum.
2. It sends you the **top 3** (Telegram `/scout`, or `bot pilot status`). Each shows the market, the setting, fills
   and maker volume per day, PnL per day, the worst day, and the last 24 h.
3. You **approve one**. It writes `config/sessions/pilot.yaml` and starts the bot on it: paper by default, live only
   if you allowed it (see below). Approving another one first closes the current position, then switches.
4. After every scan it **re-checks** what is running. If the conditions changed, it pauses quoting, closes the
   position with reduce-only orders, and tells you why. It resumes by itself when the checks pass twice in a row. It
   never switches markets without you.

A setting passes (GO) only when all three hold:

| Window | Check |
|---|---|
| Long: up to the last 7 full days | PnL/day ≥ −$0.25, at most one daily stop, never the $10 kill, at least half the days not negative |
| Short: last 24 h and last 6 h | 24 h PnL ≥ −$0.25, last 6 h ≥ −$0.50, still at least 30% of the usual fills |
| Now: last hour | Not trending, volatility and spread under 2x their usual level, data under 5 minutes old |

GO settings rank by maker volume per day, then PnL. You get one pick per market.

## The $100 rules (backtest and live bot use the same ones)

| Rule | Value | What happens |
|---|---|---|
| Leverage | per market | Up to the market's Arcus maximum (1 / initial margin); BTC and ETH at most 20x |
| Max position | $100 x leverage | Arcus's own limit. Inventory cap = that / 1.25 (the risk engine's hard cap is 1.25x it) |
| Order size | half the cap | One order per level per side; e.g. QQQ at 10x: $400 orders, $800 cap, $1,000 hard cap |
| Off-hours (RWA) | 1.5x margin | Outside 04:00-20:00 ET, weekends and holidays the cap and order size shrink with the higher initial margin |
| Position stop | $1 | The open position is down $1: close it with a maker order, cross the spread after 20 s, pause 60 s |
| Daily stop | $2 | Day PnL below −$2: close the position the same way, no new orders until 00:00 UTC, then resume by itself |
| Kill | $10 | Equity $10 below its peak: close everything with a taker order and stop until you resume it |
| Safety pause | on | Spread over 3x normal or a sudden jump: no quotes for 30 s (some settings test it off) |
| Liquidation distance | 4 sigma | Distance to liquidation under 4 sigma of 1-h moves: sell/buy half at market |

The dollar stops do not grow with leverage, so at high leverage a small move reaches them: that is what keeps most
maximum-leverage settings from passing.

The backtest is conservative:
- An order fills only when a trade goes through its price. A trade at its price does not count.
- One taker order fills it for at most what that taker printed beyond its price (the taker uses up the better levels
  and the queue at our price first). Being first in the queue instead adds only about 10-15% volume at 3 bps.
- New orders and cancels take 150 ms.
- Arcus's order budget is enforced.
- Crossing the spread pays the 2.25 bps taker fee plus slippage.
- Leftover positions are charged the cost of closing them.

## Strategies tested

Each one is the live strategy code's settings (`bot/strategies/`):

- **deep** (mid, passive): one quote each side at a fixed distance from mid. Tested at 1, 1.5, 2, 3 and 5 bps, with
  and without the safety pause, with inventory skew, and with 2 levels.
- **touch** (mid, normal): quote at mid ± max(distance, half the spread).
- **improve touch** (mid, aggressive): one tick inside the best bid and ask.
- **grid**: static 3-level grid at 10 and 25 bps.
- **rgrid**: trailing grid at 5 and 15 bps, which cuts inventory in a trend.
- **rsi signal**: mean reversion with a maker entry, a take-profit and a taker stop.

Not tested, because they break the Arcus-only rule: blend (needs another venue's price) and the delta-neutral
strategies (hedged on Lighter).

## Commands

```bash
make install                        # uv venv + editable install (Python 3.12)
bot scout run                       # record + scan every 30 min (leave it running; systemd: bot-scout)
bot scout run --depth               # also record the top 10 book levels (server; ~2x the disk)
bot scout scan                      # one scan now, printed as a table (--max-only: max leverage only)
docker compose up -d --build        # the same scout, 24/7 on a server (see docker-compose.yml)
bot pilot status                    # what is deployed + the top 3
bot pilot approve 1                 # run #1 in paper (live market data, simulated orders)
bot pilot approve 1 --live          # real money: needs BOT_PILOT_LIVE=1 in .env, then type LIVE
bot pilot close                     # close the position, stop
bot telegram                        # phone control: /scout, /pilot, /status, /pause, /stop ...
```

Live needs all of these: `BOT_PILOT_LIVE=1` in `.env`, a funded account and a passing `bot doctor pilot`, and your
typed confirmation for each deployment.

Other commands: `bot --help`. Operations: [docs/RUNBOOK.md](docs/RUNBOOK.md). The owner's go-live checklist (`OWNER_ACTIONS.md`) is kept locally, not in git.

## Layout

```
bot/scout/     tape (recorded data), record (recorder), sim (backtest + risk rules), scan (menu + ranking), pilot
bot/core/      runner, engine (the stops), risk, order manager, state, ledger
bot/strategies mid, grid, rgrid, signal (+ ones not used on Arcus-only)
bot/telegram/  the control bot
config/        venues, app, sessions (pilot.yaml is written by the pilot)
data/scout/    tape/, cache/, scans/, reports/, latest.json, report.txt
Dockerfile, docker-compose.yml       the scout for a server
state/         pilot.json, pilot_events.jsonl, per-mode state DBs and heartbeats
```
