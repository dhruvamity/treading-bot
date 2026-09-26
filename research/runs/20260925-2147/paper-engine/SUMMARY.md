# Live paper engines

Updated 2026-09-26 05:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NVDA | touch 0bp | 20.00 | 8.07 | 72,011 | 89.18 | 33.90 | 1.06 | 1.06 | -14.70 | 0.13 | 1.37 | 0.00 | 1,237.29 | 99.50 | 65.70 | 65.10 |  |
| SPY | touch 0bp | 50.00 | 8.07 | 52,121 | 64.56 | 12.30 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,996.85 | 9.20 | 4.30 | 5.90 | daily loss stop |
| SOL | touch 0bp | 20.00 | 8.07 | 37,866 | 46.90 | 11.00 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,294.38 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 8.08 | 37,088 | 45.91 | 9.80 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,349.66 | 7.40 | 4.90 | 4.80 | daily loss stop |
| BTC | touch 0bp | 20.00 | 8.08 | 35,316 | 43.72 | 32.80 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,251.65 | 3.50 | 2.10 | 1.80 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 8.07 | 12,818 | 15.87 | 3.20 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,610.90 | 9.20 | 1.70 | 7.70 | daily loss stop |
| GLD | touch 0bp | 25.00 | 8.08 | 12,520 | 15.50 | 4.70 | 0.19 | 0.19 | -15.00 | 0.75 | -0.33 | -501.19 | 1,013.59 | 92.80 | 91.50 | 87.90 |  |
| ZEC | touch 0bp | 10.00 | 8.07 | 10,368 | 12.84 | 4.20 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 677.69 | 2.20 | 2.00 | 1.20 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 8.08 | 8,141.86 | 10.08 | 7.30 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 591.98 | 7.40 | 4.80 | 3.70 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 8.08 | 7,576.87 | 9.38 | 4.70 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 586.23 | 4.10 | 3.10 | 3.70 | daily loss stop |
| SLV | touch 0bp | 25.00 | 8.07 | 4,197.57 | 5.20 | 0.70 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,496.94 | 16.40 | 12.50 | 9.60 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 8.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.90 | 86.70 | 97.40 |  |
