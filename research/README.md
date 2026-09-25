# Research: farming volume with little capital

The question: which Tread.fi-style market-making settings turn a small account over the most times in a few
hours, at or near breakeven, using limit (maker) orders only?

| File | What |
|---|---|
| [01_strategy_shortlist.md](01_strategy_shortlist.md) | The Tread.fi posts filtered for the goal, with derived cost per $1M, turnover and risk labels (R1–R4, U) |
| [REPORT.md](REPORT.md) | The findings: shortlist, synthetic mechanics study, live paper runs, recommendation |
| `synthetic/` | The synthetic mechanics study (`bot farm synth`): results and a summary. Not evidence of real edge |
| `runs/<UTC start>/` | Live paper runs on Arcus (`bot farm run`): leaderboard, results per hour, 1-minute candles, per-minute paper series, every paper fill, and the recorded tape |

## Run the paper farm yourself

It needs a machine that can reach `api.arcus.xyz` (no keys, no money: it reads public market data and simulates
the orders). From `treading-bot/bot` after `make install`:

```bash
.venv/bin/bot farm run --hours 15
```

- It records every Arcus perp and, every hour, paper-trades the whole menu (29 settings × 5x/10x/20x) on the 20
  busiest markets plus BTC, ETH, SOL, HYPE, QQQ, SPY, GLD, SLV, NVDA, USO, TSLA and XRP, since the start. Each
  paper account starts with $100 (`--capital`), with stops of 5% (position), 10% (day) and 20% (kill) of it.
- Results land in `research/runs/<UTC start>/`; `LEADERBOARD.md` is rewritten every hour. With git set up it also
  commits and pushes the folder every hour (`--no-push` to only commit, `--no-git` for neither).
- Stop it with Ctrl-C (it runs a final analysis and commits). Start it again with the run folder to resume:
  `bot farm run ../research/runs/20260925-2000`.
- Re-analyse a finished run (all markets, or `--markets QQQ-USD SPY-USD`): `bot farm analyze ../research/runs/<run>`.

## How "paper trading" is done here

The farm replays each strategy on the recorded tape with the scout's simulator (`bot/bot/scout/sim.py`), which is
causal. At each second it sees only the book up to that second. Orders go live 150 ms after they are sent, a
resting order fills only when a taker trades through its price, and the stops, order budget and liquidation rules
are the live bot's. Replaying the tape hour by hour as it is recorded is therefore the same as paper trading live
with that fill model, for every setting at once and on identical data.

What it cannot see: how our own quotes would change other traders' behaviour, and the queue at our exact price. A
trade at our price never fills us, which makes the numbers a lower bound on fills.

## Output files of a run

| File | Columns |
|---|---|
| `results/latest.json`, `results/hour-HH.HH.json` | One row per market × setting × leverage: volume (maker, taker), fills, fills per hour, turnover per hour, PnL, PnL % of capital, cost per $1M, max drawdown %, worst hour %, hours positive, projected loss per day %, stops, largest position, minutes to trade 100× the capital, risk label |
| `candles/<MARKET>.csv.gz` | `t` (UTC seconds), mid `open/high/low/close`, mean `spread_bps`, `trades`, `volume_usd`, `buy_usd` (taker buys), `vwap`, `last` |
| `paper/<MARKET>.npz` | `keys` (setting @ leverage); `minutes` [key, minute, column] with `t, eq, pos_usd, maker_usd, maker_fills, taker_usd, fees, state`; `fills` (`t, side, px, qty, maker`) with `fill_key` and `fill_tag` |
| `scout/tape/<MARKET>/<day>/{bbo,trades}-*.npz` | The raw recording (`bot/bot/scout/tape.py`) |

Read a paper series in Python:

```python
import numpy as np
z = np.load("research/runs/<run>/paper/QQQ-USD.npz")
keys = list(z["keys"]); i = keys.index("mid+1 @ 10x")
t, eq = z["minutes"][i, :, 0], z["minutes"][i, :, 1]          # equity change in USD, minute by minute
fills = z["fills"][z["fill_key"] == i]                          # t, side (+1 buy / -1 sell), px, qty, maker
```
