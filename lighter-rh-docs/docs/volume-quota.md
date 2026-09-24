---
updatedAt: 2026-08-24T14:36:46.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Volume Quota

**Volume Quota** gives users higher rate limits on `SendTx` and `SendTxBatch`based on trading volume and is **only available to Premium accounts now**. The only types of transactions that consume volume quota are:

* `L2CreateOrder` (14)
* `L2CancelAllOrders` (16)
* `L2ModifyOrder` (17)
* `L2CreateGroupedOrders` (28)

Other types of transactions has separate rate limits, see [Transaction Type Limits](https://apidocs.rh.lighter.xyz/docs/rate-limits#transaction-type-limits-per-user).

For more details on transaction types, see [constants.go](https://github.com/elliottech/lighter-go/blob/main/types/txtypes/constants.go). Grouped orders only consume one quota. Cancels do not consume quota. If you include `n` transactions in a `SendTxBatch` request, `n` quota will be consumed.

For every 2 USD of trading volume, traders receive an additional transaction limit (i.e. volume quota increases by 1). `SendTx` and `SendTxBatch` requests will return a response indicating the remaining quota, e.g. "10780 volume quota remaining.". Every 15 seconds, you get a free `SendTx` which won't consume volume quota (nor show remaining quota). Volume quota is shared across all sub-accounts under the same L1 address.

New accounts start at 1K quota, and you can stack at most 15.000.000 TX allowance in your volume quota, which does not expire.

This differs from Rate Limits, which enforce a maximum of **40000** transactions per minute for premium accounts. You can check the weight of the endpoints, and standard accounts limits here: [Rate Limits](https://apidocs.rh.lighter.xyz/docs/rate-limits).

<br />