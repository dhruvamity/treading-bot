# Paper farm leaderboard

- window: 2026-09-25 21:52 UTC to 2026-09-25 23:27 UTC (1.59 h)
- markets: 11 of 59 recorded: BTC-USD, ETH-USD, ZEC-USD, NEAR-USD, QQQ-USD, SPY-USD, NVDA-USD, GLD-USD, SLV-USD, SOL-USD, HYPE-USD
- capital: $100 per paper account; leverage 5x, 10x, 20x (capped at each market's maximum)
- stops: position 5%, day 10%, kill 20% of capital
- paper runs: 22 (31 settings), computed in 1 s

Near breakeven = projected loss at most 5% of the capital per day and never killed. Turnover = volume (maker + taker) / capital per hour. CPM = dollars lost per $1M traded (negative = profit). Risk labels: research/01_strategy_shortlist.md 1.2.

## Best single runs (near breakeven, by turnover)

| market | setting | lev | turnover/h | volume $ | fills/h | PnL $ | CPM | max DD % | worst h % | risk |
|---|---|---|---|---|---|---|---|---|---|---|
| SOL-USD | touch 0bp | 20.00 | 174.59 | 27,712 | 44.70 | 0.62 | -22.30 | 6.24 | -2.65 | R2 |
| BTC-USD | touch 0bp | 20.00 | 162.33 | 25,766 | 46.00 | -2.53 | 98.30 | 2.53 | -2.53 | R1 |
| BTC-USD | touch 0bp | 5.00 | 154.97 | 24,597 | 101.40 | -2.09 | 85.00 | 2.09 | -1.68 | R1 |
| SOL-USD | touch 0bp | 5.00 | 56.28 | 8,933.24 | 41.00 | 0.67 | -74.60 | 0.90 | -0.21 | R1 |
| ETH-USD | touch 0bp | 20.00 | 45.21 | 7,175.90 | 12.60 | -2.71 | 378.10 | 2.71 | -2.71 | R3 |
| HYPE-USD | touch 0bp | 10.00 | 32.37 | 5,137.54 | 21.40 | -2.14 | 415.80 | 2.14 | -1.11 | R3 |
| SPY-USD | touch 0bp | 5.00 | 32.25 | 5,119.43 | 30.20 | -0.11 | 20.80 | 0.08 | -0.04 | R1 |
| ZEC-USD | touch 0bp | 10.00 | 30.81 | 4,889.50 | 10.10 | -2.35 | 481.10 | 2.47 | -2.35 | R3 |
| QQQ-USD | touch 0bp | 25.00 | 28.97 | 4,598.86 | 15.10 | 0.57 | -123.40 | 0.20 | 0.19 | R1 |
| ZEC-USD | touch 0bp | 5.00 | 16.79 | 2,664.58 | 10.10 | -1.95 | 730.70 | 1.95 | -1.95 | R3 |
| QQQ-USD | touch 0bp | 5.00 | 12.82 | 2,034.52 | 11.30 | 0.08 | -38.80 | 0.10 | 0.05 | R1 |
| NEAR-USD | touch 0bp | 10.00 | 12.48 | 1,981.64 | 4.40 | -3.21 | 1,617.60 | 3.21 | -3.21 | R4 |
| NEAR-USD | touch 0bp | 5.00 | 6.30 | 999.26 | 3.80 | -2.41 | 2,415.40 | 2.41 | -2.41 | R4 |
| NVDA-USD | touch 0bp | 5.00 | 2.01 | 318.38 | 3.20 | -0.17 | 530.30 | 0.17 | -0.11 | R3 |
| GLD-USD | touch 0bp | 5.00 | 0.03 | 5.38 | 0.60 | -0.00 | 428.50 | 0.00 | -0.00 | R3 |
| GLD-USD | touch 0bp | 25.00 | 0.03 | 5.38 | 0.60 | -0.00 | 428.50 | 0.00 | -0.00 | R3 |

## Each setting across markets

| setting | lev | family | markets | near BE | profitable | avg turnover/h | volume $ | PnL $ | CPM | median CPM | worst DD % | kills | risk mix |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| touch 0bp | 10.00 | aggressive | 3 | 3 | 0 | 25.22 | 12,009 | -7.69 | 640.70 | 481.10 | 3.21 | 0 | R3:2 R4:1 |
| touch 0bp | 20.00 | aggressive | 4 | 3 | 1 | 97.61 | 61,479 | -5.13 | 83.40 | 238.20 | 6.24 | 0 | R1:1 R2:1 R3:2 |
| touch 0bp | 5.00 | aggressive | 11 | 8 | 2 | 34.10 | 59,539 | -8.44 | 141.70 | 175.60 | 2.41 | 0 | R1:4 R3:5 R4:2 |
| touch 0bp | 25.00 | aggressive | 3 | 2 | 1 | 13.86 | 6,602.35 | -1.03 | 156.80 | 428.50 | 1.60 | 0 | R1:1 R3:2 |
| touch 0bp | 50.00 | aggressive | 1 | 0 | 0 | 103.98 | 16,505 | -0.60 | 36.60 | 36.60 | 0.85 | 0 | R2:1 |
