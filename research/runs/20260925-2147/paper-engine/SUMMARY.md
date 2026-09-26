# Live paper engines

Updated 2026-09-26 05:42 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NVDA | touch 0bp | 20.00 | 7.86 | 70,879 | 90.18 | 34.20 | 1.00 | 1.00 | -14.10 | 0.07 | 1.36 | -769.99 | 1,237.24 | 99.60 | 68.10 | 67.50 |  |
| SPY | touch 0bp | 50.00 | 7.86 | 52,121 | 66.34 | 12.60 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,997.13 | 9.50 | 4.50 | 6.10 | daily loss stop |
| SOL | touch 0bp | 20.00 | 7.86 | 37,866 | 48.20 | 11.30 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,296.24 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 7.86 | 37,088 | 47.18 | 10.00 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,350.94 | 7.70 | 5.10 | 5.00 | daily loss stop |
| BTC | touch 0bp | 20.00 | 7.86 | 35,316 | 44.92 | 33.70 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,252.27 | 3.70 | 2.20 | 1.80 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 7.86 | 12,818 | 16.31 | 3.30 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,611.16 | 9.60 | 1.80 | 8.00 | daily loss stop |
| GLD | touch 0bp | 25.00 | 7.86 | 12,520 | 15.93 | 4.80 | 0.20 | 0.20 | -16.10 | 0.75 | -0.32 | -501.17 | 1,013.57 | 92.50 | 91.10 | 87.50 |  |
| ZEC | touch 0bp | 10.00 | 7.86 | 10,368 | 13.20 | 4.30 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 679.30 | 2.30 | 2.10 | 1.20 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 7.86 | 8,141.86 | 10.36 | 7.50 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 593.17 | 7.70 | 5.00 | 3.90 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 7.86 | 7,576.87 | 9.64 | 4.80 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 588.51 | 4.30 | 3.20 | 3.80 | daily loss stop |
| SLV | touch 0bp | 25.00 | 7.86 | 4,197.57 | 5.34 | 0.80 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,496.94 | 17.00 | 13.00 | 10.00 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 7.86 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.90 | 86.20 | 97.30 |  |
