---
updatedAt: 2026-09-17T14:53:16.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Rate Limits

We enforce rate limits on both REST API and WebSocket usage. These limits apply to both IP address and L1 address. It's important to note that for non-standard account types, sendTx and sendTxBatch fall into a separate rate limit bucket [described below](https://apidocs.rh.lighter.xyz/docs/rate-limits#sendtx-and-sendtxbatch-limits-premium-and-plus-accounts), and are [linked to account type and staked tokens](https://apidocs.rh.lighter.xyz/docs/account-types).

***

## REST API Endpoint Limits

The following limits apply to the [`https://api.rh.lighter.xyz/api/v1/`](https://api.rh.lighter.xyz/api/v1/) base URL, excluding  `sendTx` and `sendTxBatch` where different limits, listed [below](https://apidocs.rh.lighter.xyz/docs/rate-limits#sendtx-and-sendtxbatch-limits-premium-and-plus-accounts), apply. Different limits, also listed [below](https://apidocs.rh.lighter.xyz/docs/rate-limits#explorer-rest-api-endpoint-limits), apply to [`https://explorerapi.rh.lighter.xyz/api/`](https://explorerapi.rh.lighter.xyz/api/ "https://explorerapi.rh.lighter.xyz/api/") To bypass IP-based rate limits, clients can authenticate each request so that only L1-based rate limits apply.

| Builder accounts                             | Premium accounts                            | Plus accounts                               | Standard accounts              |
| :------------------------------------------- | :------------------------------------------ | ------------------------------------------- | :----------------------------- |
| 240,000 weighted requests per rolling minute | 24,000 weighted requests per rolling minute | 24,000 weighted requests per rolling minute | 60 requests per rolling minute |

### Weights:

| Endpoint                                                                                                                       | Weight |
| :----------------------------------------------------------------------------------------------------------------------------- | :----- |
| `sendTx, sendTxBatch, nextNonce`                                                                                               | 6      |
| `publicPools, txFromL1TxHash`                                                                                                  | 50     |
| `accountInactiveOrders, deposit/latest`                                                                                        | 100    |
| `apikeys`                                                                                                                      | 150    |
| `transferFeeInfo`                                                                                                              | 500    |
| `trades, recentTrades`                                                                                                         | 600    |
| `changeAccountTier, tokens, tokens/revoke setAccountMetadata, notification/ack, createIntentAddress, fastwithdraw, referral/*` | 3000   |
| `tokens/create`                                                                                                                | 23000  |
| Other endpoints                                                                                                                | 300    |

While standard accounts rate limits are not weighted, whenever `{premium_weighted_requests}/{endpoint_weight} < {standard_requests}`, the former limit is going to be applied. For example, both standard and premium accounts will be able to make a maximum of 8 requests per rolling minute to the `changeAccountTier` endpoint.

You can apply for a Builder Account through our Discord support channel. It’s free of charge, but we reserve the right to review applications and verify the intended use. Builder Accounts include higher limits for querying our read-only REST API endpoints, excluding `sendTx` and `sendTxBatch`; otherwise, standard account limits apply. Builders on this tier should authenticate every request, even when not explicitly required in our documentation.

***

## WebSocket Limits

To prevent resource exhaustion, we enforce the following usage limits **per IP**:

* **Connections**: 255
* **Subscriptions per connection**: 500
* **Unique Accounts per connection**: 500
* **Max Connections Per Minute**: 255 (not to be confused with channel subscriptions)
* **Max Messages Sent By Client Per Minute**: 200 (sendTx and sendBatchTx are **not** counted here, and follow the same limits as REST requests)
* **Max Inflight Messages**: 50 (sendTx and sendBatchTx are **not** counted here)

Deployments with no downtime might drop your connection, so it's recommended to have proper reconnection logic, in addition to ping/pong logic.

***

## SendTx and SendTxBatch Limits (Premium and Plus accounts)

The following limits apply to `sendTx` and `sendTxBatch` requests, regardless of whether you send the requests using REST via HTTP, or via WebSocket. For these two types, we do not enforce IP limits, but only check rate limits at the L1 address level. Standard accounts are still bound to the 60 requests per minute limit. For Premium accounts,`sendTx` and `sendTxBatch` are the only two types constrained by [Volume Quota](https://apidocs.rh.lighter.xyz/docs/volume-quota), necessary to create and modify orders.

### Premium Accounts

| Last 14D Volume | sendTx/sendTxBatch per minute |
| :-------------- | :---------------------------- |
| `< $1M`         | 4000                          |
| `> $1M`         | 5000                          |
| `> $25M`        | 6000                          |
| `> $100M`       | 7000                          |
| `> $500M`       | 8000                          |
| `> $2B`         | 12000                         |
| `> $5B`         | 24000                         |

***

## Explorer REST API Endpoint Limits

The following limits apply to the `https://explorerapi.rh.lighter.xyz/api/` Base URL.

All account types have the same limit of 90 weighted requests per rolling minute window.

### Weights

| Endpoint        | Weight |
| :-------------- | :----- |
| `search`        | 3      |
| `accounts/*`    | 2      |
| Other endpoints | 1      |

***

## Orders

### Pending Orders

Defined as orders with a `pending` status, sent to the server and acknowledged, but not yet included in the order book for matching. For example, take-profit, stop-loss, and TWAP orders (counted as one unit; sub-orders skip limits) fall into this category.

| Per Account | Per Market |
| :---------- | :--------- |
| 500         | 16         |

### Active Orders

| Per Account | Per Market |
| :---------- | :--------- |
| 1500        | 1000       |

## Transaction Type Limits

The following limits apply to all account types, otherwise, `sendTx` limits apply:

| Transaction Type     | Limit                  |
| -------------------- | ---------------------- |
| Default              | 40 requests / minute   |
| `L2Withdraw`         | 2 requests / minute    |
| `L2CreateSubAccount` | 2 requests / minute    |
| `L2CreatePublicPool` | 2 requests / minute    |
| `L2UpdateLeverage`   | 40 requests / minute   |
| `L2ChangePubKey`     | 300 requests / minute  |
| `L2Transfer`         | 120 request / minute   |
| `L2MintShares`       | 1 request / 15 seconds |
| `L2UnstakeAssets`    | 1 request / 15 seconds |
| `L1Withdraw`         | 10 requests / hour     |
| Fast Withdrawals     | 10 requests / hour     |

***

## Rate Limit Exceeding Behavior

If you exceed any rate limit:

* You will receive `HTTP 429`, or `HTTP 405`
* For WebSocket connections, excessive messages may result in disconnection
* When you're rate-limited on REST, WebSocket connections also get rate-limited, and vice versa
* For premium and plus accounts, mainnet endpoints fall into a separate bucket from `sendTx` and `sendTxBatch`, meaning that getting rate-limited in one of the two buckets will not affect activity on the other. Put simply, sending too many transactions in a minute will not affect your ability to fetch data from the exchange, and vice versa.

To avoid this, please ensure your clients are implementing proper backoff and retry strategies.

## Cooldown

Depending on whether you have been rate-limited by our firewall, or by the api servers, the cooldown period varies:

* **Firewall**: 60 seconds, static
* **Api servers**: `weightOfEndpoint/(totalWeight/60)`

As an example, making a request to the `account` endpoint (which carries a `weight` of 300) after having exhausted your weighted requests in a given minute window, results in a 750ms cooldown period.