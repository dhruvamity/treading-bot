# Live paper engines

Updated 2026-09-25 22:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 1.07 | 33,111 | 309.52 | 46.70 | -0.42 | -0.42 | 12.60 | -0.55 | 0.13 | 2,994.02 | 3,003.00 | 100.00 | 23.60 | 34.00 |  |
| SOL | touch 0bp | 20.00 | 1.07 | 33,030 | 308.72 | 64.50 | -2.50 | -2.50 | 75.60 | -4.97 | 3.32 | 0.00 | 1,313.81 | 61.80 | 45.30 | 42.20 | daily loss stop |
| BTC | touch 0bp | 20.00 | 1.07 | 19,648 | 182.91 | 113.60 | -2.49 | -2.49 | 126.60 | -0.36 | -1.86 | 0.00 | 1,254.22 | 19.70 | 10.90 | 14.70 | daily loss stop |
| ETH | touch 0bp | 20.00 | 1.07 | 18,817 | 175.21 | 36.30 | -2.62 | -2.62 | 139.40 | -0.93 | -1.44 | 0.00 | 1,353.92 | 27.60 | 24.70 | 22.80 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 1.07 | 8,722.69 | 81.41 | 14.00 | 0.49 | 0.49 | -56.60 | 0.17 | 0.32 | -1,496.27 | 1,613.67 | 100.00 | 22.90 | 17.50 |  |
| NVDA | touch 0bp | 20.00 | 1.07 | 6,715.13 | 62.68 | 22.40 | 0.84 | 0.84 | -124.80 | 0.13 | 0.71 | 160.89 | 1,137.63 | 100.00 | 90.60 | 42.50 |  |
| ZEC | touch 0bp | 10.00 | 1.07 | 6,219.22 | 58.21 | 20.60 | -2.12 | -2.12 | 340.40 | -0.98 | -0.84 | 0.00 | 685.29 | 41.40 | 27.60 | 28.70 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 1.07 | 3,534.50 | 32.95 | 20.50 | -2.45 | -2.45 | 693.10 | -0.54 | -1.65 | 0.00 | 592.27 | 61.20 | 40.20 | 38.90 | daily loss stop |
| GLD | touch 0bp | 25.00 | 1.07 | 2,035.70 | 18.95 | 9.30 | -1.87 | -1.87 | 916.40 | -0.55 | -1.09 | 5.38 | 1,014.91 | 97.90 | 89.90 | 74.60 |  |
| NEAR | touch 0bp | 10.00 | 1.07 | 1,768.98 | 16.49 | 10.30 | -2.02 | -2.02 | 1,144.00 | -0.33 | -1.49 | 0.00 | 503.07 | 2.10 | 1.80 | 1.80 | daily loss stop |
| SLV | touch 0bp | 25.00 | 1.07 | 999.74 | 9.33 | 0.90 | 0.00 | 0.00 | -0.00 | 0.09 | -0.09 | 999.74 | 999.74 | 100.00 | 75.70 | 86.80 |  |
| TSLA | touch 0bp | 10.00 | 1.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 99.90 | 91.30 |  |
