# Live paper engines

Updated 2026-09-26 03:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | touch 0bp | 50.00 | 6.07 | 52,121 | 85.83 | 16.30 | -5.38 | -5.38 | 103.30 | -1.56 | -2.03 | 0.00 | 2,997.05 | 13.90 | 6.50 | 8.90 | daily loss stop |
| NVDA | touch 0bp | 20.00 | 6.07 | 46,629 | 76.77 | 30.60 | -0.49 | -0.49 | 10.50 | -0.19 | 0.14 | -633.22 | 1,236.52 | 99.40 | 65.30 | 74.50 |  |
| SOL | touch 0bp | 20.00 | 6.07 | 37,866 | 62.36 | 14.70 | -4.31 | -4.31 | 113.80 | -5.35 | 1.97 | 0.00 | 1,295.16 | 92.50 | 79.50 | 79.30 | stopped: drawdown $9.03 > $9.00 from the peak |
| ETH | touch 0bp | 20.00 | 6.08 | 37,088 | 61.04 | 13.00 | -5.00 | -5.00 | 134.70 | -1.91 | -2.59 | 0.00 | 1,351.00 | 11.10 | 7.40 | 7.20 | daily loss stop |
| BTC | touch 0bp | 20.00 | 6.08 | 35,316 | 58.11 | 43.60 | -4.50 | -4.50 | 127.40 | -0.70 | -3.54 | 0.00 | 1,252.25 | 5.40 | 3.20 | 2.70 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 6.07 | 12,818 | 21.11 | 4.30 | -2.54 | -2.54 | 198.10 | 0.17 | -2.71 | 0.00 | 1,611.29 | 13.90 | 2.60 | 11.60 | daily loss stop |
| GLD | touch 0bp | 25.00 | 6.08 | 10,418 | 17.15 | 5.60 | -0.12 | -0.12 | 11.30 | 0.78 | -0.67 | -394.00 | 1,014.24 | 89.10 | 87.10 | 82.10 |  |
| ZEC | touch 0bp | 10.00 | 6.07 | 10,368 | 17.08 | 5.60 | -4.25 | -4.25 | 410.00 | -3.05 | -0.79 | 0.00 | 676.88 | 3.30 | 3.00 | 1.70 | daily loss stop |
| HYPE | touch 0bp | 10.00 | 6.08 | 8,141.86 | 13.40 | 9.70 | -4.80 | -4.80 | 589.10 | -1.24 | -3.09 | 0.00 | 592.98 | 11.20 | 7.30 | 5.60 | daily loss stop |
| NEAR | touch 0bp | 10.00 | 6.07 | 7,576.87 | 12.47 | 6.30 | -3.67 | -3.67 | 483.80 | -2.61 | -0.67 | 0.00 | 584.56 | 6.20 | 4.70 | 5.60 | daily loss stop |
| SLV | touch 0bp | 25.00 | 6.07 | 4,197.57 | 6.91 | 1.00 | -2.74 | -2.74 | 653.50 | -0.69 | -1.58 | 0.00 | 1,497.46 | 24.70 | 18.90 | 14.50 | daily loss stop |
| TSLA | touch 0bp | 10.00 | 6.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 99.80 | 79.90 | 96.20 |  |
