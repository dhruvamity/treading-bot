# Live paper engines

Updated 2026-09-26 02:35 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 4.73 | 52,121 | 110.09 | 20.90 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,997.75 | 21.00 | 9.90 | 13.50 | daily loss stop |
| SOL | touch 0bp | 20.00 | 4.74 | 37,866 | 79.96 | 18.80 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,308.47 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 4.74 | 37,088 | 78.25 | 16.70 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,354.65 | 16.90 | 11.20 | 10.90 | daily loss stop |
| BTC | touch 0bp | 20.00 | 4.74 | 35,316 | 74.51 | 55.90 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,253.45 | 8.10 | 4.90 | 4.10 | daily loss stop |
| NVDA | touch 0bp | 20.00 | 4.74 | 25,434 | 53.70 | 25.30 | -1.63 | -1.63 | 64.10 | -0.03 | -1.17 | 170.29 | 1,237.24 | 99.10 | 58.80 | 68.00 |  |
| QQQ | touch 0bp | 25.00 | 4.74 | 12,818 | 27.06 | 5.50 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,611.62 | 21.20 | 3.90 | 17.60 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 4.73 | 10,368 | 21.90 | 7.20 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 680.19 | 5.00 | 4.50 | 2.60 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 4.74 | 8,141.86 | 17.18 | 12.50 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 595.36 | 17.00 | 11.00 | 8.50 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 4.74 | 7,576.87 | 15.99 | 8.00 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 587.43 | 9.40 | 7.10 | 8.50 | daily loss stop |
| GLD | touch 0bp | 25.00 | 4.74 | 4,548.00 | 9.60 | 3.60 | -1.10 | -1.10 | 240.90 | -0.20 | -0.67 | -479.72 | 1,014.34 | 83.50 | 80.40 | 75.20 |  |
| SLV | touch 0bp | 25.00 | 4.74 | 4,197.57 | 8.86 | 1.30 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,497.20 | 37.50 | 28.70 | 22.00 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 4.73 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.70 | 93.00 | 94.30 |  |
