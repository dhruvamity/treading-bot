# daytrade

Three day-trading strategies, one per market, written out as exact rules from the owner's notes (15 Oct 2026), with
their own backtest and a **paper** auto-trader. Separate from the market-making bots: it imports none of their
strategies or simulators. **It sends no real orders.**

| Market | Strategy | Venue it would trade |
|---|---|---|
| SPY | Previous day's high/low + 15-minute opening range, enter on a retest that holds | Lighter (also tested on Arcus costs) |
| QQQ | Previous day's high/low + EMA 13/48/200 pullbacks, half off at 1R, the rest rides EMA48 | Lighter (also Arcus) |
| Gold | London morning: sweep of the Asian range, change of character, limit order in the fair value gap, 3R | Lighter XAU (Arcus has only GLD, which has no price outside US hours) |

## What the backtest says

**None of the three makes money after costs.** Before costs SPY and QQQ are about break-even and gold a little below
(the rules pick no better entries than chance); the costs of tight stops then make every one of them lose. The full
report, with every timeframe, both venues and each year, is `reports/research/REPORT.md`; `daytrade research` remakes its tables (summary and trade files).

$1,000, risking 1% of the account a trade (1R = the distance from entry to stop), 5-minute signal bars, Lighter's
costs (no fee, its measured spread, slippage on stops):

| Strategy | Trades a year | Won | Average trade | $1,000 after the last 12 months | $1,000 after 2020–2026 |
|---|---|---|---|---|---|
| SPY opening range | 51 | 34% | −0.17R | $877 | $533 |
| QQQ EMA pullback | 184 | 47% | −0.19R | $878 | $80 |
| Gold London sweep | 30 | 29% | −0.14R | $903 (PAXG) · $925 (gold perp) | $754 (PAXG, from Sep 2020) |

On 1- and 15-minute bars the picture is the same (all in `reports/research/`). On **Arcus** every one is far worse:
its 2.25 bp taker fee is a large part of a stop only 5–15 bp wide (SPY 5-minute: $1,000 → $95 over 2020–2026).

The other versions of each rule (about 730 versions, 1,450 runs: other targets, stops, windows, opening ranges, the
levels filter on or off, VWAP, the published first-5-minute-candle rule) were ranked on the years before October 2025
and judged on the twelve months after. The best ones of the earlier years did not hold up: gold's best (+0.3R a trade before)
lost 0.6R a trade in the last twelve months; SPY's best went from +0.07R to −0.22R. The nearest to working were
QQQ VWAP pullbacks on 15-minute bars and the first-candle rule on QQQ: both about 0R in the last twelve months.

## Commands

From `treading-bot/daytrade` (until it is part of `make install`, prefix `PYTHONPATH=.` and use `../arcus/.venv/bin/python -m daytrade.cli`):

```bash
daytrade fetch                        # public one-minute history into data/bars/ (Alpaca key needed for SPY/QQQ)
daytrade backtest spy --tf 5          # one strategy: trades, win rate, R, $1,000 result (--venue arcus, --since)
daytrade research                     # every version x 1/5/15 minutes x both venues -> reports/research/
daytrade check                        # the strategies on Lighter's and Arcus's own prices against the reference
daytrade run                          # the paper auto-trader: a server job, it runs until stopped
daytrade run --seconds 300            # a bounded check
daytrade status                       # what it holds now, and its paper results
```

## How it works

- **The rules** are in `daytrade/strategies.py`, each read only from closed bars. Times are the market's own clock
  (New York for SPY/QQQ, London for gold, summer time included).
- **The backtest** (`daytrade/sim.py`) fills on one-minute bars the cautious way: a market order at the next bar's
  open; a limit order only when a bar trades through it; when one bar reaches both stop and target, the stop counts;
  a gap through a stop fills at the open. Costs per side: Lighter SPY 0.65 bp, QQQ 0.75 bp, gold 0.55 bp for a market
  order (+0.5 bp more for a stop); Arcus adds its 2.25 bp taker fee; funding 0.05 bp an hour held.
- **The paper bot** (`daytrade/bot.py`) brings each market's minute prices up to date from Lighter every minute,
  rebuilds today exactly as the backtest does and replays it through the same fill rules. Its trades are written to
  `state/paper_trades.jsonl`; `state/paper_status.json` says what it holds now. Paper is the backtest on prices the
  rules have never seen.

## Data (`data/bars/`, not committed)

| File | What | From |
|---|---|---|
| `alpaca_SPY`, `alpaca_QQQ` | The ETFs as traded in New York, every minute 04:00–20:00 ET | Jan 2020 |
| `binance_PAXGUSDT` | Tokenized gold (one token = one ounce), around the clock; thin before 2025 | Sep 2020 |
| `binance_XAUUSDT_perp` | Binance's gold perpetual | Dec 2025 |
| `lighter_SPY/QQQ/XAU`, `arcus_SPY/QQQ/GLD` | The venues' own prices (Lighter's mark, Arcus's oracle) | late Jun 2026 (Lighter's gold: 24 Aug 2026) |

Dukascopy's spot gold was the first choice; in October 2026 it refused most requests (`daytrade fetch duka` still
tries).

## Limits

- The notes describe these setups for **0DTE options** on SPY and QQQ. The venues have perpetuals, which pay one for
  one with the price: an option's convex payoff cannot be had here, and nothing here tests options.
- One-minute bars cannot show the order book: fills inside a minute are estimated (cautiously, see above).
- Some rules are a judgement call ("the retest holds", "the last swing before the sweep"): each is fixed in the code
  and its alternatives were tested, not tuned.
