# Live paper engines

Updated 2026-09-25 21:58 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ETH | touch 0bp | 20.00 | 0.13 | 6,661.63 | 510.24 | 145.50 | -0.29 | -0.29 | 44.30 | -0.38 | 0.08 | 721.94 | 1,087.69 | 100.00 | 99.80 | 98.50 |  |
| BTC | touch 0bp | 20.00 | 0.13 | 5,787.16 | 437.86 | 204.30 | -0.63 | -0.63 | 109.10 | -0.07 | -0.56 | -887.71 | 1,249.69 | 100.00 | 43.20 | 88.90 |  |
| QQQ | touch 0bp | 25.00 | 0.13 | 3,613.02 | 282.33 | 54.70 | 0.02 | 0.01 | -4.20 | 0.23 | -0.22 | 1,612.91 | 1,612.91 | 100.00 | 90.50 | 87.90 |  |
| SPY | touch 0bp | 50.00 | 0.13 | 3,076.38 | 243.45 | 102.90 | -0.26 | -0.26 | 82.90 | 0.01 | -0.26 | 2,963.40 | 2,963.40 | 100.00 | 70.20 | 54.70 |  |
| NEAR | touch 0bp | 10.00 | 0.13 | 1,768.98 | 136.95 | 85.20 | -2.02 | -2.02 | 1,144.00 | -0.33 | -1.49 | 0.00 | 497.07 | 17.60 | 15.20 | 14.80 | daily loss stop |
| ZEC | touch 0bp | 10.00 | 0.12 | 1,577.13 | 126.18 | 48.00 | -0.20 | -0.20 | 128.90 | -0.24 | 0.04 | -387.94 | 595.03 | 100.00 | 31.70 | 35.50 |  |
| SOL | touch 0bp | 20.00 | 0.13 | 1,207.66 | 95.50 | 31.60 | 0.33 | 0.33 | -275.20 | 0.04 | 0.29 | -1,187.97 | 1,187.97 | 100.00 | 63.80 | 58.70 |  |
| NVDA | touch 0bp | 20.00 | 0.13 | 850.09 | 65.81 | 15.50 | 0.21 | 0.21 | -248.10 | 0.08 | 0.13 | -749.88 | 749.88 | 100.00 | 40.90 | 82.20 |  |
| HYPE | touch 0bp | 10.00 | 0.13 | 31.60 | 2.45 | 15.50 | 0.00 | 0.00 | -65.40 | -0.00 | 0.00 | 0.00 | 15.80 | 100.00 | 83.90 | 99.10 |  |
| GLD | touch 0bp | 25.00 | 0.13 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 98.90 | 100.00 |  |
| SLV | touch 0bp | 25.00 | 0.13 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 38.50 | 89.50 |  |
| TSLA | touch 0bp | 10.00 | 0.13 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 100.00 | 81.80 |  |
