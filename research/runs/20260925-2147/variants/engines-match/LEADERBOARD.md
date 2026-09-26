# Paper farm leaderboard

- window: 2026-09-25 21:52 UTC to 2026-09-26 00:30 UTC (2.64 h)
- markets: 11 of 59 recorded: BTC-USD, ETH-USD, ZEC-USD, NEAR-USD, QQQ-USD, SPY-USD, NVDA-USD, GLD-USD, SLV-USD, SOL-USD, HYPE-USD
- capital: $100 per paper account; leverage 5x, 10x, 20x (capped at each market's maximum)
- stops: position 5%, day 10%, kill 20% of capital
- paper runs: 22 (31 settings), computed in 1 s

Near breakeven = projected loss at most 5% of the capital per day and never killed. Turnover = volume (maker + taker) / capital per hour. CPM = dollars lost per $1M traded (negative = profit). Risk labels: research/01_strategy_shortlist.md 1.2.

## Best single runs (near breakeven, by turnover)

| market | setting | lev | turnover/h | volume $ | fills/h | PnL $ | CPM | max DD % | worst h % | risk |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC-USD | touch 0bp | 20.00 | 130.47 | 34,463 | 40.10 | -5.07 | 147.00 | 5.07 | -2.53 | R2 |
| SOL-USD | touch 0bp | 20.00 | 127.69 | 33,727 | 32.90 | -2.90 | 86.10 | 9.76 | -4.15 | R3 |
| BTC-USD | touch 0bp | 5.00 | 125.96 | 33,270 | 89.70 | -3.65 | 109.70 | 3.58 | -1.68 | R2 |
| SOL-USD | touch 0bp | 5.00 | 46.23 | 12,210 | 35.60 | -0.35 | 29.00 | 1.80 | -0.71 | R2 |
| ETH-USD | touch 0bp | 20.00 | 42.69 | 11,277 | 9.50 | -5.39 | 477.80 | 5.39 | -2.71 | R3 |
| ZEC-USD | touch 0bp | 10.00 | 32.74 | 8,647.89 | 10.60 | -5.48 | 633.70 | 5.48 | -3.13 | R3 |
| HYPE-USD | touch 0bp | 10.00 | 31.81 | 8,401.54 | 20.10 | -3.42 | 406.70 | 2.83 | -1.11 | R3 |
| SPY-USD | touch 0bp | 5.00 | 27.69 | 7,314.46 | 23.10 | -0.25 | 33.90 | 0.24 | -0.16 | R1 |
| NEAR-USD | touch 0bp | 10.00 | 21.75 | 5,743.85 | 9.50 | -4.76 | 829.30 | 3.96 | -3.21 | R3 |
| QQQ-USD | touch 0bp | 25.00 | 19.85 | 5,243.17 | 12.50 | 0.49 | -92.60 | 0.20 | 0.01 | R1 |
| ZEC-USD | touch 0bp | 5.00 | 17.31 | 4,571.53 | 10.20 | -2.79 | 610.40 | 2.79 | -1.95 | R3 |
| NEAR-USD | touch 0bp | 5.00 | 12.45 | 3,288.89 | 8.00 | -3.02 | 918.50 | 3.02 | -2.41 | R3 |
| QQQ-USD | touch 0bp | 5.00 | 9.63 | 2,543.74 | 8.70 | 0.08 | -30.40 | 0.10 | 0.03 | R1 |
| GLD-USD | touch 0bp | 25.00 | 9.48 | 2,504.14 | 1.10 | 0.40 | -158.50 | 0.00 | 0.00 | R1 |
| NVDA-USD | touch 0bp | 5.00 | 2.59 | 684.58 | 3.40 | -0.28 | 410.20 | 0.35 | -0.26 | R3 |
| GLD-USD | touch 0bp | 5.00 | 1.91 | 504.05 | 1.10 | 0.08 | -158.60 | 0.00 | 0.00 | R1 |

## Each setting across markets

| setting | lev | family | markets | near BE | profitable | avg turnover/h | volume $ | PnL $ | CPM | median CPM | worst DD % | kills | risk mix |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| touch 0bp | 10.00 | aggressive | 3 | 3 | 0 | 28.77 | 22,793 | -13.66 | 599.30 | 633.70 | 5.48 | 0 | R3:3 |
| touch 0bp | 20.00 | aggressive | 4 | 3 | 0 | 78.19 | 82,254 | -14.78 | 179.70 | 312.40 | 9.76 | 0 | R2:1 R3:3 |
| touch 0bp | 5.00 | aggressive | 11 | 8 | 2 | 32.05 | 93,113 | -14.16 | 152.00 | 109.70 | 3.58 | 0 | R1:3 R2:3 R3:4 R4:1 |
| touch 0bp | 25.00 | aggressive | 3 | 2 | 2 | 14.82 | 11,743 | -2.32 | 197.60 | -92.60 | 2.72 | 0 | R1:2 R3:1 |
| touch 0bp | 50.00 | aggressive | 1 | 0 | 0 | 99.54 | 26,292 | -1.65 | 62.70 | 62.70 | 1.88 | 0 | R3:1 |
