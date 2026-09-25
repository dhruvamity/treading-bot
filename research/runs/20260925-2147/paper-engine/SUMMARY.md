# Live paper engines

Updated 2026-09-25 23:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 2.07 | 42,106 | 203.38 | 30.90 | -3.25 | -3.25 | 77.10 | -0.87 | -1.03 | 0.00 | 3,003.86 | 71.00 | 13.00 | 20.60 | daily loss stop |
| SOL | touch 0bp | 20.00 | 2.07 | 33,030 | 159.54 | 33.30 | -2.50 | -2.50 | 75.60 | -4.97 | 3.32 | 0.00 | 1,311.10 | 31.90 | 23.40 | 21.80 | daily loss stop |
| BTC | touch 0bp | 20.00 | 2.07 | 19,648 | 94.71 | 58.80 | -2.49 | -2.49 | 126.60 | -0.36 | -1.86 | 0.00 | 1,253.32 | 10.20 | 5.60 | 7.60 | daily loss stop |
| ETH | touch 0bp | 20.00 | 2.07 | 18,817 | 90.71 | 18.80 | -2.62 | -2.62 | 139.40 | -0.93 | -1.44 | 0.00 | 1,352.82 | 14.30 | 12.80 | 11.80 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 2.07 | 8,931.03 | 43.10 | 8.20 | 0.68 | 0.68 | -76.50 | 0.18 | 0.50 | -1,287.74 | 1,613.46 | 100.00 | 15.10 | 9.60 |  |
| NVDA | touch 0bp | 20.00 | 2.07 | 8,793.80 | 42.44 | 20.80 | 0.15 | 0.15 | -17.10 | 0.33 | -0.17 | 1,238.83 | 1,238.83 | 99.60 | 81.30 | 32.60 |  |
| ZEC | touch 0bp | 10.00 | 2.07 | 6,219.22 | 30.06 | 10.60 | -2.12 | -2.12 | 340.40 | -0.98 | -0.84 | 0.00 | 686.26 | 21.40 | 14.30 | 14.80 | daily loss stop |
| GLD | touch 0bp | 25.00 | 2.07 | 4,526.51 | 21.82 | 6.30 | -1.41 | -1.41 | 311.60 | -0.20 | -0.98 | -501.53 | 1,014.99 | 98.90 | 86.00 | 86.90 |  |
| HYPE | touch 0bp | 10.00 | 2.07 | 3,534.50 | 17.05 | 10.60 | -2.45 | -2.45 | 693.10 | -0.54 | -1.65 | 0.00 | 589.96 | 31.60 | 20.80 | 20.10 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 2.07 | 1,768.98 | 8.53 | 5.30 | -2.02 | -2.02 | 1,144.00 | -0.33 | -1.49 | 0.00 | 504.39 | 1.10 | 0.90 | 0.90 | daily loss stop |
| SLV | touch 0bp | 25.00 | 2.07 | 1,499.96 | 7.24 | 1.00 | -0.95 | -0.95 | 631.20 | 0.04 | -0.99 | 1,499.01 | 1,499.01 | 100.00 | 81.40 | 93.10 |  |
| TSLA | touch 0bp | 10.00 | 2.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 89.20 | 95.50 |  |
