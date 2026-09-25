# Live paper engines

Updated 2026-09-25 23:27 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 1.61 | 42,106 | 261.47 | 39.70 | -3.25 | -3.25 | 77.10 | -0.87 | -1.03 | 0.00 | 3,003.16 | 91.20 | 16.70 | 26.50 | daily loss stop |
| SOL | touch 0bp | 20.00 | 1.61 | 33,030 | 205.11 | 42.80 | -2.50 | -2.50 | 75.60 | -4.97 | 3.32 | 0.00 | 1,310.51 | 41.10 | 30.10 | 28.00 | daily loss stop |
| BTC | touch 0bp | 20.00 | 1.61 | 19,648 | 121.68 | 75.60 | -2.49 | -2.49 | 126.60 | -0.36 | -1.86 | 0.00 | 1,253.69 | 13.10 | 7.30 | 9.80 | daily loss stop |
| ETH | touch 0bp | 20.00 | 1.61 | 18,817 | 116.55 | 24.20 | -2.62 | -2.62 | 139.40 | -0.93 | -1.44 | 0.00 | 1,353.26 | 18.30 | 16.40 | 15.10 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 1.61 | 8,818.06 | 54.70 | 9.90 | 0.41 | 0.41 | -46.20 | 0.17 | 0.23 | -1,400.99 | 1,613.78 | 100.00 | 17.20 | 11.60 |  |
| NVDA | touch 0bp | 20.00 | 1.61 | 8,433.09 | 52.32 | 25.40 | 0.78 | 0.78 | -92.10 | 0.31 | 0.47 | 878.74 | 1,137.37 | 100.00 | 93.80 | 41.90 |  |
| ZEC | touch 0bp | 10.00 | 1.61 | 6,219.22 | 38.65 | 13.70 | -2.12 | -2.12 | 340.40 | -0.98 | -0.84 | 0.00 | 686.16 | 27.50 | 18.30 | 19.10 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 1.61 | 3,534.50 | 21.91 | 13.60 | -2.45 | -2.45 | 693.10 | -0.54 | -1.65 | 0.00 | 592.00 | 40.70 | 26.70 | 25.90 | daily loss stop |
| GLD | touch 0bp | 25.00 | 1.61 | 2,035.70 | 12.61 | 6.20 | -1.87 | -1.87 | 916.70 | -0.55 | -1.09 | 5.38 | 1,014.81 | 98.60 | 88.90 | 83.10 |  |
| NEAR | touch 0bp | 10.00 | 1.61 | 1,768.98 | 10.97 | 6.80 | -2.02 | -2.02 | 1,144.00 | -0.33 | -1.49 | 0.00 | 503.68 | 1.40 | 1.20 | 1.20 | daily loss stop |
| SLV | touch 0bp | 25.00 | 1.61 | 999.74 | 6.21 | 0.60 | -0.52 | -0.52 | 516.50 | 0.09 | -0.60 | 999.23 | 999.23 | 100.00 | 79.30 | 91.10 |  |
| TSLA | touch 0bp | 10.00 | 1.61 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 99.90 | 94.20 |  |
