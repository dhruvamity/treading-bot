# Live paper engines

Updated 2026-09-26 07:13 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NVDA | touch 0bp | 20.00 | 9.37 | 80,712 | 86.11 | 32.60 | 1.68 | 1.68 | -20.80 | 0.34 | 1.77 | 404.44 | 1,237.24 | 99.50 | 66.80 | 61.80 |  |
| SPY | touch 0bp | 50.00 | 9.37 | 52,121 | 55.62 | 10.60 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,996.62 | 7.50 | 3.50 | 4.80 | daily loss stop |
| SOL | touch 0bp | 20.00 | 9.37 | 37,866 | 40.40 | 9.50 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,293.92 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 9.38 | 37,088 | 39.56 | 8.40 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,351.83 | 6.00 | 4.00 | 3.90 | daily loss stop |
| BTC | touch 0bp | 20.00 | 9.38 | 35,316 | 37.67 | 28.30 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,252.79 | 2.90 | 1.70 | 1.50 | daily loss stop |
| GLD | touch 0bp | 25.00 | 9.37 | 18,568 | 19.81 | 5.50 | -0.60 | -0.60 | 32.20 | 0.46 | -0.83 | -234.59 | 1,013.83 | 90.20 | 88.10 | 86.30 |  |
| QQQ | touch 0bp | 25.00 | 9.37 | 12,818 | 13.68 | 2.80 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,610.84 | 7.60 | 1.40 | 6.30 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 9.37 | 10,368 | 11.06 | 3.60 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 678.62 | 1.80 | 1.60 | 0.90 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 9.38 | 8,141.86 | 8.68 | 6.30 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 593.23 | 6.10 | 4.00 | 3.00 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 9.37 | 7,576.87 | 8.08 | 4.10 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 591.50 | 3.40 | 2.50 | 3.00 | daily loss stop |
| SLV | touch 0bp | 25.00 | 9.37 | 4,197.57 | 4.48 | 0.60 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,497.20 | 13.40 | 10.30 | 7.90 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 9.37 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.90 | 82.00 | 94.40 |  |
