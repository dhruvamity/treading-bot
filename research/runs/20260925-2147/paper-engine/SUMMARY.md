# Live paper engines

Updated 2026-09-26 06:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NVDA | touch 0bp | 20.00 | 9.08 | 76,775 | 84.59 | 32.30 | 1.36 | 1.36 | -17.80 | 0.17 | 1.63 | 387.01 | 1,236.79 | 99.40 | 67.30 | 62.00 |  |
| SPY | touch 0bp | 50.00 | 9.07 | 52,121 | 57.44 | 10.90 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,996.58 | 7.80 | 3.70 | 5.00 | daily loss stop |
| SOL | touch 0bp | 20.00 | 9.08 | 37,866 | 41.72 | 9.80 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,292.27 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 9.08 | 37,088 | 40.85 | 8.70 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,349.51 | 6.30 | 4.20 | 4.10 | daily loss stop |
| BTC | touch 0bp | 20.00 | 9.08 | 35,316 | 38.90 | 29.20 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,251.45 | 3.00 | 1.80 | 1.50 | daily loss stop |
| GLD | touch 0bp | 25.00 | 9.08 | 18,568 | 20.46 | 5.70 | -0.58 | -0.58 | 31.20 | 0.46 | -0.81 | -234.57 | 1,013.75 | 89.80 | 87.50 | 85.70 |  |
| QQQ | touch 0bp | 25.00 | 9.08 | 12,818 | 14.12 | 2.90 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,610.90 | 7.90 | 1.50 | 6.60 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 9.07 | 10,368 | 11.43 | 3.70 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 676.13 | 1.90 | 1.70 | 1.00 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 9.08 | 8,141.86 | 8.97 | 6.50 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 592.03 | 6.30 | 4.10 | 3.20 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 9.08 | 7,576.87 | 8.35 | 4.20 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 589.82 | 3.50 | 2.60 | 3.20 | daily loss stop |
| SLV | touch 0bp | 25.00 | 9.07 | 4,197.57 | 4.63 | 0.70 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,497.20 | 14.00 | 10.70 | 8.20 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 9.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.90 | 82.70 | 97.60 |  |
