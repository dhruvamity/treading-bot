# Live paper engines

Updated 2026-09-25 21:55 UTC. Each row is the bot's real engine in paper mode (live Arcus data, simulated orders, the paper venue's queue-aware fills) running one session, the one Telegram's `/run MARKET SETTING max` deploys: $100 capital at the market's maximum leverage, stops 1% / 2% / 10% of the capital. Net = spread capture + inventory mark-to-market − fees + funding. CPM = dollars lost per $1M traded (negative = profit). At touch = share of quoting time the bid (ask) sat at the best price.

| market | setting | lev | hours | volume $ | turnover/h | fills/h | net $ | net % | CPM | spread $ | inventory $ | position $ | max pos $ | quoting % | bid at touch % | ask at touch % | not quoting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ETH | touch 0bp | 20.00 | 0.07 | 5,420.41 | 736.55 | 190.20 | -0.51 | -0.52 | 95.00 | -0.22 | -0.29 | 712.81 | 1,087.30 | 100.00 | 100.00 | 97.30 |  |
| BTC | touch 0bp | 20.00 | 0.07 | 3,650.03 | 494.58 | 216.80 | -0.39 | -0.39 | 105.70 | -0.03 | -0.35 | 1,249.67 | 1,249.67 | 100.00 | 52.70 | 81.90 |  |
| SPY | touch 0bp | 50.00 | 0.07 | 2,061.76 | 297.07 | 144.10 | 0.08 | 0.08 | -37.10 | 0.04 | 0.04 | 1,949.10 | 1,954.51 | 100.00 | 89.00 | 78.80 |  |
| NEAR | touch 0bp | 10.00 | 0.07 | 1,768.98 | 245.00 | 152.30 | -2.02 | -2.02 | 1,144.00 | -0.33 | -1.49 | 0.00 | 497.99 | 31.80 | 27.40 | 26.70 | daily loss stop |
| QQQ | touch 0bp | 25.00 | 0.07 | 1,000.06 | 140.85 | 14.10 | 0.05 | 0.05 | -53.70 | 0.05 | 0.00 | -1,000.01 | 1,000.01 | 100.00 | 100.00 | 100.00 |  |
| NVDA | touch 0bp | 20.00 | 0.07 | 850.09 | 120.04 | 28.20 | 0.08 | 0.08 | -91.40 | 0.08 | 0.00 | -750.01 | 750.01 | 100.00 | 24.40 | 99.20 |  |
| ZEC | touch 0bp | 10.00 | 0.07 | 594.70 | 87.42 | 58.80 | -0.55 | -0.55 | 922.60 | -0.04 | -0.51 | 594.15 | 594.15 | 100.00 | 55.00 | 61.20 |  |
| SOL | touch 0bp | 20.00 | 0.07 | 47.10 | 6.78 | 28.80 | 0.04 | 0.04 | -900.60 | 0.00 | 0.04 | -27.70 | 37.37 | 100.00 | 73.90 | 75.90 |  |
| GLD | touch 0bp | 25.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 100.00 | 100.00 |  |
| HYPE | touch 0bp | 10.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 71.00 | 99.60 |  |
| SLV | touch 0bp | 25.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 71.40 | 80.40 |  |
| TSLA | touch 0bp | 10.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |  | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 100.00 | 100.00 |  |
