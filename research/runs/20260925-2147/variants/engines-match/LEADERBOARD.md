# Paper farm leaderboard

- window: 2026-09-25 21:52 UTC to 2026-09-26 07:13 UTC (9.35 h)
- markets: 12 of 59 recorded: BTC-USD, ETH-USD, ZEC-USD, NEAR-USD, QQQ-USD, SPY-USD, NVDA-USD, TSLA-USD, GLD-USD, SLV-USD, SOL-USD, HYPE-USD
- capital: $100 per paper account; leverage 5x, 10x, 20x (capped at each market's maximum)
- stops: position 5%, day 10%, kill 20% of capital
- paper runs: 24 (31 settings), computed in 1 s

Near breakeven = projected loss at most 5% of the capital per day and never killed. Turnover = volume (maker + taker) / capital per hour. CPM = dollars lost per $1M traded (negative = profit). Risk labels: research/01_strategy_shortlist.md 1.2.

## Best single runs (near breakeven, by turnover)

| market | setting | lev | turnover/h | volume $ | fills/h | PnL $ | CPM | max DD % | worst h % | risk |
|---|---|---|---|---|---|---|---|---|---|---|
| NVDA-USD | touch 0bp | 20.00 | 37.84 | 35,386 | 15.40 | -1.02 | 28.80 | 3.38 | -1.61 | R2 |
| BTC-USD | touch 0bp | 5.00 | 37.26 | 34,838 | 27.60 | -4.16 | 119.50 | 4.16 | -2.07 | R2 |
| BTC-USD | touch 0bp | 20.00 | 36.86 | 34,463 | 11.30 | -5.07 | 147.00 | 5.07 | -2.53 | R2 |
| SOL-USD | touch 0bp | 20.00 | 36.07 | 33,727 | 9.30 | -2.90 | 86.10 | 9.76 | -4.15 | R3 |
| SPY-USD | touch 0bp | 50.00 | 33.74 | 31,549 | 9.80 | -4.80 | 152.10 | 5.17 | -3.60 | R2 |
| NVDA-USD | touch 0bp | 5.00 | 24.47 | 22,885 | 13.30 | 0.08 | -3.50 | 1.18 | -0.44 | R1 |
| ETH-USD | touch 0bp | 5.00 | 20.70 | 19,352 | 13.50 | -3.31 | 171.00 | 3.31 | -1.64 | R2 |
| SOL-USD | touch 0bp | 5.00 | 14.05 | 13,135 | 10.60 | -1.16 | 88.60 | 2.71 | -1.61 | R1 |
| HYPE-USD | touch 0bp | 5.00 | 12.92 | 12,079 | 9.30 | -2.52 | 208.50 | 2.52 | -1.47 | R2 |
| HYPE-USD | touch 0bp | 10.00 | 12.39 | 11,589 | 6.70 | -4.29 | 370.40 | 4.29 | -2.16 | R3 |
| ETH-USD | touch 0bp | 20.00 | 12.06 | 11,277 | 2.70 | -5.39 | 477.80 | 5.39 | -2.71 | R3 |
| ZEC-USD | touch 0bp | 10.00 | 9.25 | 8,647.89 | 3.00 | -5.48 | 633.70 | 5.48 | -3.13 | R3 |
| GLD-USD | touch 0bp | 25.00 | 9.07 | 8,478.86 | 2.20 | 0.71 | -83.80 | 0.69 | -0.32 | R1 |
| QQQ-USD | touch 0bp | 5.00 | 8.16 | 7,630.43 | 6.40 | -1.18 | 154.90 | 1.65 | -0.98 | R2 |
| NEAR-USD | touch 0bp | 10.00 | 8.06 | 7,540.99 | 3.10 | -6.07 | 804.30 | 6.07 | -3.21 | R3 |
| QQQ-USD | touch 0bp | 25.00 | 6.96 | 6,511.22 | 4.30 | -1.84 | 282.20 | 2.50 | -2.46 | R2 |
| ZEC-USD | touch 0bp | 5.00 | 5.53 | 5,171.34 | 3.70 | -3.85 | 744.30 | 3.85 | -1.95 | R3 |
| NEAR-USD | touch 0bp | 5.00 | 5.53 | 5,170.50 | 3.40 | -4.59 | 887.70 | 4.59 | -2.41 | R3 |
| GLD-USD | touch 0bp | 5.00 | 3.35 | 3,135.53 | 2.20 | 0.12 | -39.10 | 0.28 | -0.18 | R1 |
| SLV-USD | touch 0bp | 5.00 | 1.18 | 1,098.92 | 0.50 | -1.09 | 989.80 | 1.24 | -0.91 | R3 |

## Each setting across markets

| setting | lev | family | markets | near BE | profitable | avg turnover/h | volume $ | PnL $ | CPM | median CPM | worst DD % | kills | risk mix |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| touch 0bp | 20.00 | aggressive | 4 | 4 | 0 | 30.71 | 114,854 | -14.38 | 125.20 | 116.50 | 9.76 | 0 | R2:2 R3:2 |
| touch 0bp | 50.00 | aggressive | 1 | 1 | 0 | 33.74 | 31,549 | -4.80 | 152.10 | 152.10 | 5.17 | 0 | R2:1 |
| touch 0bp | 5.00 | aggressive | 12 | 10 | 2 | 13.67 | 153,333 | -23.64 | 154.20 | 154.90 | 4.59 | 0 | R1:3 R2:5 R3:3 |
| touch 0bp | 10.00 | aggressive | 4 | 3 | 0 | 7.43 | 27,778 | -15.84 | 570.20 | 633.70 | 6.07 | 0 | R3:3 |
| touch 0bp | 25.00 | aggressive | 3 | 2 | 1 | 7.36 | 20,653 | -3.94 | 191.00 | 282.20 | 3.32 | 0 | R1:1 R2:1 R3:1 |
