# Live paper engines

Updated 2026-09-26 02:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 5.07 | 52,121 | 102.76 | 19.50 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,997.83 | 18.60 | 8.70 | 11.90 | daily loss stop |
| SOL | touch 0bp | 20.00 | 5.07 | 37,866 | 74.66 | 17.50 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,305.81 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 5.08 | 37,088 | 73.07 | 15.60 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,353.06 | 14.90 | 9.90 | 9.70 | daily loss stop |
| BTC | touch 0bp | 20.00 | 5.08 | 35,316 | 69.56 | 52.20 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,253.43 | 7.20 | 4.30 | 3.60 | daily loss stop |
| NVDA | touch 0bp | 20.00 | 5.07 | 25,434 | 50.12 | 23.60 | -1.63 | -1.63 | 64.10 | -0.03 | -1.17 | 170.29 | 1,237.24 | 99.20 | 61.10 | 71.70 |  |
| QQQ | touch 0bp | 25.00 | 5.07 | 12,818 | 25.26 | 5.10 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,611.42 | 18.70 | 3.50 | 15.60 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 5.07 | 10,368 | 20.45 | 6.70 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 678.45 | 4.50 | 4.00 | 2.30 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 5.07 | 8,141.86 | 16.04 | 11.60 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 595.10 | 15.00 | 9.80 | 7.50 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 5.07 | 7,576.87 | 14.93 | 7.50 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 584.44 | 8.30 | 6.20 | 7.50 | daily loss stop |
| GLD | touch 0bp | 25.00 | 5.08 | 4,548.00 | 8.96 | 3.30 | -1.08 | -1.08 | 238.20 | -0.20 | -0.65 | -479.70 | 1,014.32 | 85.40 | 82.70 | 78.10 |  |
| SLV | touch 0bp | 25.00 | 5.07 | 4,197.57 | 8.27 | 1.20 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,496.94 | 33.20 | 25.30 | 19.40 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 5.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.70 | 82.30 | 94.90 |  |
