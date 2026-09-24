---
updatedAt: 2026-09-04T12:50:35.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Historical Data

Users can fetch historical data using various REST endpoints, mainly:

* [accountInactiveOrders](https://apidocs.rh.lighter.xyz/reference/accountinactiveorders)
* [trades](https://apidocs.rh.lighter.xyz/reference/trades)
* [logs](https://apidocs.rh.lighter.xyz/reference/get_accounts-param-logs)
* [tx](https://apidocs.rh.lighter.xyz/reference/tx)
* [pnl](https://apidocs.rh.lighter.xyz/reference/pnl)
* [deposit\_history](https://apidocs.rh.lighter.xyz/reference/deposit_history), [transfer\_history](https://apidocs.rh.lighter.xyz/reference/transfer_history), [withdraw\_history](https://apidocs.rh.lighter.xyz/reference/withdraw_history)
* [candles](https://apidocs.rh.lighter.xyz/reference/candles)
* [fundings](https://apidocs.rh.lighter.xyz/reference/fundings), [positionFunding](https://apidocs.rh.lighter.xyz/reference/positionfunding)
* [liquidations](https://apidocs.rh.lighter.xyz/reference/liquidations)
* [exchangeMetrics](https://apidocs.rh.lighter.xyz/reference/exchangemetrics), [executeStats](https://apidocs.rh.lighter.xyz/reference/executestats)

Alternatively, you can use [export](https://apidocs.rh.lighter.xyz/reference/export) to obtain CSVs containing up to 12 months of trades, or up to 3 months of funding payments, for a specific account index.

For interested parties, you can access parquet files containing trade events (global), up to mainnet genesis (June 26th, 2026), using the [`historicalTrade`](https://apidocs.rh.lighter.xyz/reference/export_historicaltrades) endpoint. To obtain access, perform an in-app transfer of 300 USDG to `0x4FD058F25bE85E459ec552cA8e4C696FD1D34125`. On-chain transfers are not supported; you must transfer from within the exchange directly (`L2Transfer`).