# daytrade

The owner's directional strategies for SPY, QQQ and gold (three lists of what X/Twitter traders say works, 15 Oct
2026), written out as exact rules, each with its own backtest, and a **paper** auto-trader. Separate from the
market-making bots: it imports none of their strategies or simulators. **It sends no real orders.**

## What the backtests say

**Only the last two years count** (the owner's rule, 15 Oct 2026): trades from 15 Oct 2024 to 15 Oct 2026, the
first year to rank versions on, the last 12 months to judge them (`daytrade.PERIODS`; indicators may read up to 300
days earlier).

| Study | Report | Answer |
|---|---|---|
| Day trades: SPY opening range, QQQ EMA pullback, gold London sweep | `reports/research/REPORT.md` | **None makes money after costs**, on 1-, 5- or 15-minute bars, on either venue |
| Intraday #2 QQQ VWAP rebound, #7 volatility breakout | `reports/intraday2/REPORT.md` | #2 and #7 as posted: no edge. #7's published form (the noise breakout, Zarattini 2024): +3.9 bp a trade on QQQ at Lighter's costs, positive in both years but thin (t 1.2); loses on Arcus and on SPY |
| Overnight and swing holds; #3 %b, #4 RSI, #5 overnight, #6 gold sessions, #8 EMA + RSI | `reports/swing/REPORT.md` | **Buying short dips in an uptrend works** (Connors' RSI(2), cumulative RSI, %b; QQQ's z-score): +50 to +90 bp a trade after costs and funding, both years, both venues, at 1x-3x. Gold's Asian-range breakout also made money in both years (gold trended hard). The rest does not |
| #1 monthly rotation | `reports/rotation/REPORT.md` | As posted (SPY/TLT/EEM): below holding SPY, and TLT/EEM are not on either venue. SPY/QQQ/gold as perpetuals beat holding QQQ, mostly from gold's 2025 rise: 24 decisions, not proof |

The day-trading study, $1,000 risking 1% a trade, 5-minute bars, Lighter's costs, the two years:

| Strategy | Trades | Won | Average trade | $1,000 became |
|---|---|---|---|---|
| SPY opening range | 102 | 37% | −0.10R | $897 |
| QQQ EMA pullback | 374 | 50% | −0.12R | $629 |
| Gold London sweep (PAXG) | 78 | 26% | −0.19R | $854 |

The rules that pass, on Lighter after costs and funding (SPY and QQQ decide at 15:59 New York):

| Rule | Market | Trades in two years | Won | Average trade | First year | Last 12 months | $1,000 at 1x | at 3x | Worst fall at 3x |
|---|---|---|---|---|---|---|---|---|---|
| RSI(2) under 10, above the 200-day; out over the 5-day average | SPY | 22 | 86% | +86.8 bp | +70 bp | +103 bp | $1,209 | $1,753 | 9% |
| z-score under −1, buy in thirds; out over the 5-day EMA | QQQ | 46 | 91% | +59.2 bp | +57 bp | +61 bp | $1,302 | $2,096 | 41% |
| Cumulative RSI(2) under 35; out over 65 | QQQ | 21 | 86% | +87.6 bp | +42 bp | +116 bp | $1,195 | $1,648 | 31% |
| Noise breakout (intraday) | QQQ | 432 | 39% | +3.9 bp | +5.4 bp | +2.2 bp | $1,171 | — | 39% at 5x |
| holding SPY's perpetual (yardstick) | SPY | — | — | — | — | — | $1,213 | $1,624 | 57% |

Nothing here survives the venues' highest leverage (50x liquidated every dip-buying rule 9-22 times in two years).

## Commands

From `treading-bot/daytrade` (until it is part of `make install`, prefix `PYTHONPATH=.` and use `../arcus/.venv/bin/python -m daytrade.cli`):

```bash
daytrade fetch                        # public history into data/ (Alpaca key needed for SPY/QQQ minutes)
daytrade fetch daily funding gold_hours   # daily ETFs, the venues' funding, hourly spot gold (2024 on)
daytrade backtest spy --tf 5          # one day-trading strategy: trades, win rate, R, $1,000 result
daytrade research                     # the day-trading study -> reports/research/
python -m daytrade.intraday2          # #2 and #7 -> reports/intraday2/ (`tables`, `check`)
python -m daytrade.swing_research     # overnight, swing and the third list -> reports/swing/ (`spy qqq xau tables`)
python -m daytrade.rotation           # #1 -> reports/rotation/
daytrade check                        # the day-trading strategies on Lighter's and Arcus's own prices
daytrade run                          # the paper auto-trader: a server job, it runs until stopped
daytrade run --seconds 300            # a bounded check (--no-swing leaves out the daily rules)
daytrade status                       # what it holds now, and its paper results
```

With the rest of the bot (from `treading-bot/arcus`): `tbot up daytrade` runs `daytrade run` in the background (plain `tbot up` does too where `BOT_ROLE` is `all` or `trader`; pid and output in `state/paper.pid`, `state/paper.out`), `tbot down daytrade` stops it, `tbot status` shows it; in the one Telegram bot `/dt_status` and `/dt_trades` (the last 10 trades) only read, `/dt` lists them.

## How it works

- **The rules**: day trading in `daytrade/strategies.py` and `intraday2.py`, each read only from closed bars, on the
  market's own clock (New York for SPY/QQQ, London for gold, summer time included); overnight and swing rules in
  `swing_rules.py`, the rotation in `rotation.py`.
- **The day-trading backtest** (`sim.py`) fills on one-minute bars the cautious way: a market order at the next
  bar's open; a limit order only when a bar trades through it; when one bar reaches both stop and target, the stop
  counts; a gap through a stop fills at the open. Costs per side: Lighter SPY 0.65 bp, QQQ 0.75 bp, gold 0.55 bp for
  a market order (+0.5 bp more for a stop); Arcus adds its 2.25 bp taker fee; funding 0.05 bp an hour held.
- **The swing backtest** (`swing.py`) decides at each bar's close (stocks: 15:59 New York on minute prices, the
  official close on daily prices), pays the venue's real hourly funding (its average before late June 2026), checks
  stops through gaps and closes a position at its liquidation price.
- **The paper bot** (`bot.py`) brings each market's minute prices up to date from Lighter every minute, rebuilds
  today exactly as the backtest does and replays it through the same fill rules: the three day-trading strategies and
  the noise breakout. The daily rules (`swing_bot.py`: SPY and QQQ RSI(2), cumulative RSI, RSI(2) out over the high
  and %b; QQQ's Triple RSI and z-score) decide once a day at 15:59 New York on Lighter's price, and gold's Asian-range
  breakout (weekday entries) every hour on Lighter's XAU. Trades go to `state/paper_trades.jsonl`;
  `state/paper_status.json` says what it holds now. Paper is the backtest on prices the rules have never seen.
  Weekends and New York holidays are not sessions for SPY and QQQ, though Lighter's SPY and QQQ trade on them.

## Data (`data/`, not committed)

| File | What | From |
|---|---|---|
| `bars/alpaca_SPY`, `bars/alpaca_QQQ` | The ETFs as traded in New York, every minute 04:00–20:00 ET | Jan 2020 |
| `bars/binance_PAXGUSDT` | Tokenized gold (one token = one ounce), around the clock; thin before 2025 | Sep 2020 |
| `bars/binance_XAUUSDT_perp` | Binance's gold perpetual | Dec 2025 |
| `bars/duka_XAUUSD_1h` | Dukascopy's spot gold, hourly | Jan 2024 (older years may be in the file too) |
| `bars/lighter_SPY/QQQ/XAU`, `bars/arcus_SPY/QQQ/GLD` | The venues' own prices (Lighter's mark, Arcus's oracle) | late Jun 2026 (Lighter's gold: 24 Aug 2026) |
| `daily/yahoo_*` | SPY, QQQ, GLD, TLT, EEM, IEF, SHY, gold futures: daily, splits out, dividends listed | 1993-2004 |
| `funding/*` | Lighter's and Arcus's hourly funding, Binance's gold perpetual's | Jun 2026 (Binance Dec 2025) |

The files hold more history than the backtests read: they read only the two years and the warm-up before them.

Dukascopy throttles hard and unevenly (Oct 2026: from 1 to 30 s a request); `daytrade fetch gold_hours` keeps what
it has and asks only for the months it lacks.

## Limits

- The day-trading notes describe their SPY and QQQ setups for **0DTE options**. The venues have perpetuals, which
  pay one for one with the price: an option's convex payoff cannot be had here, and nothing here tests options.
- One-minute bars cannot show the order book: fills inside a minute are estimated (cautiously, see above).
- Some rules are a judgement call ("the retest holds", "the last swing before the sweep", a members-only threshold):
  each is fixed in the code and its alternatives were tested, not tuned.
- Funding before late June 2026 is the venues' average since; a different rate in another market regime changes the
  long holds most (the swing report shows funding at 0x and 2x).
