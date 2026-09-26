# Live paper engines

Updated 2026-09-26 01:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 4.07 | 52,121 | 128.02 | 24.30 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,997.75 | 28.30 | 13.30 | 18.20 | daily loss stop |
| SOL | touch 0bp | 20.00 | 4.07 | 37,866 | 93.01 | 21.90 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,311.58 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 4.08 | 37,088 | 91.01 | 19.40 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,354.60 | 22.70 | 15.00 | 14.70 | daily loss stop |
| BTC | touch 0bp | 20.00 | 4.08 | 35,316 | 86.63 | 65.00 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,253.94 | 10.90 | 6.50 | 5.50 | daily loss stop |
| NVDA | touch 0bp | 20.00 | 4.07 | 23,148 | 56.83 | 27.50 | -2.11 | -2.11 | 91.20 | -0.18 | -1.50 | -717.36 | 1,237.62 | 98.80 | 51.20 | 62.80 |  |
| QQQ | touch 0bp | 25.00 | 4.07 | 12,818 | 31.47 | 6.40 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,610.75 | 28.50 | 5.30 | 23.70 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 4.07 | 10,368 | 25.48 | 8.40 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 682.99 | 6.80 | 6.10 | 3.60 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 4.07 | 8,141.86 | 19.98 | 14.50 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 595.90 | 22.90 | 14.90 | 11.40 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 4.07 | 7,576.87 | 18.60 | 9.30 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 593.30 | 12.70 | 9.50 | 11.40 | daily loss stop |
| GLD | touch 0bp | 25.00 | 4.08 | 4,548.00 | 11.16 | 4.20 | -1.02 | -1.02 | 224.80 | -0.20 | -0.59 | -479.64 | 1,014.19 | 77.80 | 74.50 | 66.70 |  |
| SLV | touch 0bp | 25.00 | 4.07 | 4,197.57 | 10.31 | 1.50 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,496.69 | 50.50 | 38.50 | 29.60 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 4.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.60 | 95.10 | 92.30 |  |
