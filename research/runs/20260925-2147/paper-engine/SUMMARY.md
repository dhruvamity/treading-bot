# Live paper engines

Updated 2026-09-26 00:30 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 2.66 | 47,289 | 177.81 | 30.50 | -3.35 | -3.35 | 70.80 | -0.90 | -1.10 | -844.71 | 3,004.40 | 99.90 | 49.50 | 67.80 |  |
| SOL | touch 0bp | 20.00 | 2.66 | 37,866 | 142.38 | 33.50 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,309.21 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 2.66 | 37,088 | 139.24 | 29.70 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,350.50 | 85.70 | 56.60 | 55.40 | daily loss stop |
| BTC | touch 0bp | 20.00 | 2.66 | 35,316 | 132.58 | 99.50 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,251.98 | 41.20 | 24.70 | 20.70 | daily loss stop |
| NVDA | touch 0bp | 20.00 | 2.66 | 15,208 | 57.15 | 24.80 | -0.40 | -0.40 | 26.40 | 0.14 | -0.26 | 383.22 | 1,238.94 | 100.00 | 88.40 | 89.90 |  |
| QQQ | touch 0bp | 25.00 | 2.66 | 11,714 | 44.02 | 9.40 | 0.55 | 0.55 | -47.00 | 0.25 | 0.30 | 1,107.36 | 1,613.30 | 100.00 | 19.90 | 89.10 |  |
| ZEC | touch 0bp | 10.00 | 2.66 | 10,368 | 39.01 | 12.80 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 683.64 | 25.50 | 23.10 | 13.40 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 2.66 | 7,576.87 | 28.46 | 14.30 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 590.06 | 47.70 | 35.80 | 43.00 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 2.66 | 7,440.93 | 27.95 | 20.70 | -4.07 | -4.07 | 546.40 | -1.18 | -2.50 | 0.00 | 594.50 | 83.90 | 53.60 | 40.90 |  |
| GLD | touch 0bp | 25.00 | 2.66 | 4,531.88 | 17.01 | 5.30 | -1.32 | -1.32 | 291.80 | -0.20 | -0.90 | -496.06 | 1,014.81 | 100.00 | 100.00 | 91.40 |  |
| SLV | touch 0bp | 25.00 | 2.66 | 2,998.37 | 11.27 | 1.50 | -1.88 | -1.88 | 628.20 | -0.43 | -1.12 | 0.00 | 1,497.98 | 95.50 | 82.90 | 59.10 |  |
| TSLA | touch 0bp | 10.00 | 2.66 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 100.00 | 100.00 |  |
