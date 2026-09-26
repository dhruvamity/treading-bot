# Live paper engines

Updated 2026-09-26 04:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NVDA | touch 0bp | 20.00 | 7.08 | 68,818 | 97.27 | 36.00 | 1.27 | 1.27 | -18.50 | 0.31 | 1.40 | 330.87 | 1,236.08 | 99.50 | 69.20 | 73.50 |  |
| SPY | touch 0bp | 50.00 | 7.07 | 52,121 | 73.69 | 14.00 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,997.59 | 11.00 | 5.20 | 7.10 | daily loss stop |
| SOL | touch 0bp | 20.00 | 7.07 | 37,866 | 53.54 | 12.60 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,295.83 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 7.08 | 37,088 | 52.41 | 11.20 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,351.91 | 8.90 | 5.90 | 5.70 | daily loss stop |
| BTC | touch 0bp | 20.00 | 7.08 | 35,316 | 49.89 | 37.40 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,252.79 | 4.30 | 2.60 | 2.10 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 7.07 | 12,818 | 18.12 | 3.70 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,610.47 | 11.10 | 2.10 | 9.20 | daily loss stop |
| GLD | touch 0bp | 25.00 | 7.08 | 10,424 | 14.73 | 4.90 | 0.12 | 0.12 | -11.60 | 0.78 | -0.43 | -388.39 | 1,013.62 | 91.30 | 89.70 | 85.50 |  |
| ZEC | touch 0bp | 10.00 | 7.07 | 10,368 | 14.66 | 4.80 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 678.47 | 2.60 | 2.40 | 1.40 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 7.08 | 8,141.86 | 11.51 | 8.30 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 594.69 | 8.90 | 5.80 | 4.50 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 7.08 | 7,576.87 | 10.71 | 5.40 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 586.47 | 4.90 | 3.70 | 4.50 | daily loss stop |
| SLV | touch 0bp | 25.00 | 7.07 | 4,197.57 | 5.93 | 0.80 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,497.46 | 19.70 | 15.00 | 11.50 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 7.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.80 | 84.00 | 97.00 |  |
