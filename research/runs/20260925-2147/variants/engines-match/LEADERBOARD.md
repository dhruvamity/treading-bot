# Paper farm leaderboard

- window: 2026-09-25 21:52 UTC to 2026-09-26 05:41 UTC (7.83 h)
- markets: 12 of 59 recorded: BTC-USD, ETH-USD, ZEC-USD, NEAR-USD, QQQ-USD, SPY-USD, NVDA-USD, TSLA-USD, GLD-USD, SLV-USD, SOL-USD, HYPE-USD
- capital: $100 per paper account; leverage 5x, 10x, 20x (capped at each market's maximum)
- stops: position 5%, day 10%, kill 20% of capital
- paper runs: 24 (31 settings), computed in 2 s

Near breakeven = projected loss at most 5% of the capital per day and never killed. Turnover = volume (maker + taker) / capital per hour. CPM = dollars lost per $1M traded (negative = profit). Risk labels: research/01_strategy_shortlist.md 1.2.

## Best single runs (near breakeven, by turnover)

| market | setting | lev | turnover/h | volume $ | fills/h | PnL $ | CPM | max DD % | worst h % | risk |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC-USD | touch 0bp | 5.00 | 44.51 | 34,838 | 33.00 | -4.16 | 119.50 | 4.16 | -2.07 | R2 |
| BTC-USD | touch 0bp | 20.00 | 44.03 | 34,463 | 13.50 | -5.07 | 147.00 | 5.07 | -2.53 | R2 |
| SOL-USD | touch 0bp | 20.00 | 43.09 | 33,727 | 11.10 | -2.90 | 86.10 | 9.76 | -4.15 | R3 |
| SPY-USD | touch 0bp | 50.00 | 40.31 | 31,549 | 11.80 | -4.80 | 152.10 | 5.17 | -3.60 | R2 |
| NVDA-USD | touch 0bp | 20.00 | 36.67 | 28,701 | 15.20 | -1.57 | 54.60 | 3.38 | -1.61 | R2 |
| ETH-USD | touch 0bp | 5.00 | 24.72 | 19,352 | 16.10 | -3.31 | 171.00 | 3.31 | -1.64 | R2 |
| NVDA-USD | touch 0bp | 5.00 | 24.50 | 19,175 | 12.80 | -0.01 | 0.70 | 1.18 | -0.44 | R1 |
| SOL-USD | touch 0bp | 5.00 | 16.78 | 13,135 | 12.60 | -1.16 | 88.60 | 2.71 | -1.61 | R1 |
| HYPE-USD | touch 0bp | 5.00 | 15.43 | 12,079 | 11.10 | -2.52 | 208.50 | 2.52 | -1.47 | R2 |
| HYPE-USD | touch 0bp | 10.00 | 14.81 | 11,589 | 8.00 | -4.29 | 370.40 | 4.29 | -2.16 | R3 |
| ETH-USD | touch 0bp | 20.00 | 14.41 | 11,277 | 3.20 | -5.39 | 477.80 | 5.39 | -2.71 | R3 |
| ZEC-USD | touch 0bp | 10.00 | 11.05 | 8,647.89 | 3.60 | -5.48 | 633.70 | 5.48 | -3.13 | R3 |
| NEAR-USD | touch 0bp | 10.00 | 9.63 | 7,540.99 | 3.70 | -6.07 | 804.30 | 6.07 | -3.21 | R3 |
| QQQ-USD | touch 0bp | 5.00 | 9.37 | 7,333.99 | 7.20 | -1.16 | 158.60 | 1.65 | -0.98 | R2 |
| QQQ-USD | touch 0bp | 25.00 | 8.32 | 6,511.22 | 5.10 | -1.84 | 282.20 | 2.50 | -2.46 | R2 |
| ZEC-USD | touch 0bp | 5.00 | 6.61 | 5,171.34 | 4.50 | -3.85 | 744.30 | 3.85 | -1.95 | R3 |
| NEAR-USD | touch 0bp | 5.00 | 6.61 | 5,170.50 | 4.10 | -4.59 | 887.70 | 4.59 | -2.41 | R3 |
| GLD-USD | touch 0bp | 25.00 | 5.96 | 4,668.74 | 1.50 | 1.03 | -221.00 | 0.24 | -0.18 | R1 |
| GLD-USD | touch 0bp | 5.00 | 2.04 | 1,597.89 | 1.50 | 0.28 | -177.00 | 0.05 | -0.04 | R1 |
| SLV-USD | touch 0bp | 5.00 | 1.40 | 1,098.92 | 0.60 | -1.10 | 1,005.50 | 1.24 | -0.91 | R4 |

## Each setting across markets

| setting | lev | family | markets | near BE | profitable | avg turnover/h | volume $ | PnL $ | CPM | median CPM | worst DD % | kills | risk mix |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| touch 0bp | 20.00 | aggressive | 4 | 4 | 0 | 34.55 | 108,168 | -14.93 | 138.00 | 116.50 | 9.76 | 0 | R2:2 R3:2 |
| touch 0bp | 50.00 | aggressive | 1 | 1 | 0 | 40.31 | 31,549 | -4.80 | 152.10 | 152.10 | 5.17 | 0 | R2:1 |
| touch 0bp | 5.00 | aggressive | 12 | 10 | 1 | 15.50 | 145,595 | -23.60 | 162.10 | 158.60 | 4.59 | 0 | R1:3 R2:5 R3:2 R4:1 |
| touch 0bp | 10.00 | aggressive | 4 | 3 | 0 | 8.87 | 27,778 | -15.84 | 570.20 | 633.70 | 6.07 | 0 | R3:3 |
| touch 0bp | 25.00 | aggressive | 3 | 2 | 1 | 7.17 | 16,843 | -3.68 | 218.60 | 282.20 | 3.32 | 0 | R1:1 R2:1 R3:1 |
