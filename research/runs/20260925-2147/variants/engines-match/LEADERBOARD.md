# Paper farm leaderboard

- window: 2026-09-25 21:52 UTC to 2026-09-26 02:34 UTC (4.72 h)
- markets: 11 of 59 recorded: BTC-USD, ETH-USD, ZEC-USD, NEAR-USD, QQQ-USD, SPY-USD, NVDA-USD, GLD-USD, SLV-USD, SOL-USD, HYPE-USD
- capital: $100 per paper account; leverage 5x, 10x, 20x (capped at each market's maximum)
- stops: position 5%, day 10%, kill 20% of capital
- paper runs: 22 (31 settings), computed in 1 s

Near breakeven = projected loss at most 5% of the capital per day and never killed. Turnover = volume (maker + taker) / capital per hour. CPM = dollars lost per $1M traded (negative = profit). Risk labels: research/01_strategy_shortlist.md 1.2.

## Best single runs (near breakeven, by turnover)

| market | setting | lev | turnover/h | volume $ | fills/h | PnL $ | CPM | max DD % | worst h % | risk |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC-USD | touch 0bp | 5.00 | 73.87 | 34,838 | 54.70 | -4.16 | 119.50 | 4.16 | -2.07 | R2 |
| BTC-USD | touch 0bp | 20.00 | 73.07 | 34,463 | 22.50 | -5.07 | 147.00 | 5.07 | -2.53 | R2 |
| SOL-USD | touch 0bp | 20.00 | 71.51 | 33,727 | 18.40 | -2.90 | 86.10 | 9.76 | -4.15 | R3 |
| SPY-USD | touch 0bp | 50.00 | 66.89 | 31,549 | 19.50 | -4.80 | 152.10 | 5.17 | -3.60 | R2 |
| ETH-USD | touch 0bp | 5.00 | 41.03 | 19,352 | 26.70 | -3.31 | 171.00 | 3.31 | -1.64 | R2 |
| SOL-USD | touch 0bp | 5.00 | 27.85 | 13,135 | 21.00 | -1.16 | 88.60 | 2.71 | -1.61 | R1 |
| HYPE-USD | touch 0bp | 5.00 | 25.61 | 12,079 | 18.40 | -2.52 | 208.50 | 2.52 | -1.47 | R2 |
| HYPE-USD | touch 0bp | 10.00 | 24.57 | 11,589 | 13.40 | -4.29 | 370.40 | 4.29 | -2.16 | R3 |
| ETH-USD | touch 0bp | 20.00 | 23.91 | 11,277 | 5.30 | -5.39 | 477.80 | 5.39 | -2.71 | R3 |
| ZEC-USD | touch 0bp | 10.00 | 18.34 | 8,647.89 | 5.90 | -5.48 | 633.70 | 5.48 | -3.13 | R3 |
| NEAR-USD | touch 0bp | 10.00 | 15.99 | 7,540.99 | 6.10 | -6.07 | 804.30 | 6.07 | -3.21 | R3 |
| QQQ-USD | touch 0bp | 25.00 | 13.81 | 6,511.22 | 8.50 | -1.84 | 282.20 | 2.50 | -2.46 | R2 |
| ZEC-USD | touch 0bp | 5.00 | 10.96 | 5,171.34 | 7.40 | -3.85 | 744.30 | 3.85 | -1.95 | R3 |
| NEAR-USD | touch 0bp | 5.00 | 10.96 | 5,170.50 | 6.80 | -4.59 | 887.70 | 4.59 | -2.41 | R3 |
| GLD-USD | touch 0bp | 25.00 | 5.31 | 2,504.14 | 0.60 | 0.64 | -254.70 | 0.24 | -0.18 | R1 |
| GLD-USD | touch 0bp | 5.00 | 1.07 | 504.05 | 0.60 | 0.13 | -253.20 | 0.05 | -0.04 | R1 |

## Each setting across markets

| setting | lev | family | markets | near BE | profitable | avg turnover/h | volume $ | PnL $ | CPM | median CPM | worst DD % | kills | risk mix |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| touch 0bp | 50.00 | aggressive | 1 | 1 | 0 | 66.89 | 31,549 | -4.80 | 152.10 | 152.10 | 5.17 | 0 | R2:1 |
| touch 0bp | 10.00 | aggressive | 3 | 3 | 0 | 19.63 | 27,778 | -15.84 | 570.20 | 633.70 | 6.07 | 0 | R3:3 |
| touch 0bp | 20.00 | aggressive | 4 | 3 | 0 | 45.19 | 85,251 | -16.40 | 192.40 | 312.40 | 9.76 | 0 | R2:1 R3:3 |
| touch 0bp | 25.00 | aggressive | 3 | 2 | 1 | 9.67 | 13,678 | -4.55 | 332.80 | 282.20 | 3.32 | 0 | R1:1 R2:1 R3:1 |
| touch 0bp | 5.00 | aggressive | 11 | 7 | 1 | 21.59 | 112,033 | -25.09 | 223.90 | 208.50 | 4.59 | 0 | R1:2 R2:4 R3:4 R4:1 |
