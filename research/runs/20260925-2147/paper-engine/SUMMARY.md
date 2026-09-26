# Live paper engines

Updated 2026-09-26 00:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 3.07 | 52,121 | 169.73 | 32.20 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,993.66 | 59.00 | 27.70 | 37.90 | daily loss stop |
| SOL | touch 0bp | 20.00 | 3.07 | 37,866 | 123.31 | 29.00 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,307.26 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 3.07 | 37,088 | 120.62 | 25.70 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,350.82 | 47.40 | 31.30 | 30.70 | daily loss stop |
| BTC | touch 0bp | 20.00 | 3.08 | 35,316 | 114.85 | 86.20 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,251.44 | 22.80 | 13.70 | 11.40 | daily loss stop |
| NVDA | touch 0bp | 20.00 | 3.07 | 19,637 | 63.91 | 30.60 | -2.01 | -2.01 | 102.50 | -0.24 | -1.33 | -165.94 | 1,236.96 | 97.50 | 80.20 | 88.30 |  |
| QQQ | touch 0bp | 25.00 | 3.07 | 12,818 | 41.72 | 8.50 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,608.17 | 59.40 | 11.00 | 49.40 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 3.07 | 10,368 | 33.78 | 11.10 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 680.19 | 14.10 | 12.80 | 7.40 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 3.07 | 8,141.86 | 26.49 | 19.20 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 592.85 | 47.70 | 31.00 | 23.90 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 3.07 | 7,576.87 | 24.65 | 12.40 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 588.75 | 26.40 | 19.80 | 23.80 | daily loss stop |
| GLD | touch 0bp | 25.00 | 3.07 | 4,548.00 | 14.79 | 5.50 | -0.58 | -0.58 | 128.20 | -0.20 | -0.15 | -479.20 | 1,013.26 | 99.10 | 99.20 | 87.60 |  |
| SLV | touch 0bp | 25.00 | 3.07 | 3,598.33 | 11.71 | 1.60 | -2.40 | -2.40 | 667.30 | -0.48 | -1.58 | 599.45 | 1,496.17 | 97.50 | 72.70 | 53.90 |  |
| TSLA | touch 0bp | 10.00 | 3.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.10 | 99.00 | 95.70 |  |
