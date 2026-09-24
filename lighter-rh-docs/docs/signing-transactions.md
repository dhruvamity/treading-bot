---
updatedAt: 2026-08-25T18:44:33.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Signing Transactions

Traders can trade programmatically using the [sendTx](https://apidocs.rh.lighter.xyz/reference/sendtx) and [sendTxBatch](https://apidocs.rh.lighter.xyz/reference/sendtxbatch) REST endpoints, or [json/sendTx](https://apidocs.rh.lighter.xyz/docs/websocket#send-tx) and [json/sendTxBatch](https://apidocs.rh.lighter.xyz/docs/websocket#send-batch-tx) types via Websocket. Creating an API key is required to sign transactions.

Orders that have the correct syntax will be accepted by the API servers, returning code=200. This does not guarantee the execution of your order, as the sequencer could still reject it if parameters are not set properly. To monitor orders execution, you can use [websocket channels](https://apidocs.rh.lighter.xyz/docs/websocket#channels).

## Sign Transactions

Every transaction needs to be signed using the **SignerClient**. Using the [Python SDK](https://github.com/elliottech/lighter-python), you can initialize it as follows:

```python
 client = lighter.SignerClient(  
        url=BASE_URL,  
        api_private_keys={API_KEY_INDEX:PRIVATE_KEY},  
        account_index=ACCOUNT_INDEX
    )
```

The code for the signer can be found in the same repo, in the [signer\_client.py](https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py) file. You may notice that it uses a binary for the signer: the code for it can be found in the [lighter-go](https://github.com/elliottech/lighter-go) public repo, and you can compile it yourself using the [justfile](https://github.com/elliottech/lighter-go/blob/main/justfile).

## Handle Nonces

When signing a transaction, you may need to provide a nonce (number used once). You can get the next nonce that you need to use using the TransactionApi’s next\_nonce method or take care of incrementing it yourself. Note that each nonce is handled per **API\_KEY**. If the transaction throws an error at the API server level, nonce will not increase. But if the syntax is correct (`code=200`) and order is only later rejected by the Sequencer (say, because the price was set incorrectly), the order is cancelled and the nonce increases regardless - this holds true except for a few edge cases e.g. when the order isn't executed by the sequencer before the ExpiredAt timestamps. One peculiarity is that maker orders with correct syntax can still fail at the API level if some conditions aren't met (e.g. not enough margin), resulting in nonce not increasing, while taker orders will go through regardless as long as the syntax is correct, and only fail at the sequencer level - increasing the nonce.

If you'd like to skip nonces, you can set the `SkipNonce`  (`skip_nonce` in the Python SDK) attribute (4th in `L2TxAttributes`) to `1`. If this attribute is not specified, we require `new_nonce = old_nonce + 1`. In any case, the following must hold true when skipping nonces: `2^47-1 > new_nonce > old_nonce`. Otherwise, nonces are capped at `2^48-1`.

## Handle price and size

To correctly handle decimals for both price and size, you can refer to the [orderBooks](https://apidocs.rh.lighter.xyz/reference/orderbooks) and [orderBookDetails](https://apidocs.rh.lighter.xyz/reference/orderbookdetails) endpoints. The number of decimals indicates the number of zeros you'll need to use in order to specify a whole unit (e.g. to buy a whole Ethereum coin where `supported_size_decimals` is 4, you will need to specify `size` equal to 1\*10^4). Additionally, those same endpoints will offer guidance on the minimum base amount and the minimum quota amount. The former indicates the minimum amount you can trade in coin terms, while the latter indicates it using the quote asset (so, USDC). The highest of the two is applied. Note that those minimums only apply to maker orders.

As for prices, note that when specifying a price for a taker order, that is to be interpreted as the worst price you're willing to accept - if the sequencer cannot offer you an equal or better price, the order is cancelled. Additionally, when handling TP/SL orders, you should be mindful that, besides a trigger price, you should indicate a price as well, which indicates the allowed slippage for the execution of said order, with the same logic used for taker orders above.

## Handle Client Order Index, and Order Index

When signing new orders, you can specify a `client_order_index` (uint48) allowing you to better reference your trades. Sometimes, e.g. in cancels, you may see `order_index` instead as an argument - this is fine and you can use the same value here. If `client_order_index` is missing (e.g. if you trade from the front-end, that'll be 0), you can use `trade_id` (assigned by the exchange) to reference the order.

## Order types

* ORDER\_TYPE\_LIMIT (0)
* ORDER\_TYPE\_MARKET (1)
* ORDER\_TYPE\_STOP\_LOSS (2)
* ORDER\_TYPE\_STOP\_LOSS\_LIMIT (3)
* ORDER\_TYPE\_TAKE\_PROFIT (4)
* ORDER\_TYPE\_TAKE\_PROFIT\_LIMIT (5)
* ORDER\_TYPE\_TWAP (6)

Additionally, there are two internal order types:

* TWAPSubOrder (7)
* LiquidationOrder (8)

## Time in force

* ORDER\_TIME\_IN\_FORCE\_IMMEDIATE\_OR\_CANCEL (0)
* ORDER\_TIME\_IN\_FORCE\_GOOD\_TILL\_TIME (1)
* ORDER\_TIME\_IN\_FORCE\_POST\_ONLY (2)

## Order Version

You can specify an `order_version` when modifying orders, so that the sequencer will reject any version that is not strictly greater than the order's current stored version, returning `AppErrInvalidOrderOrderVersion` . This is optional, and examples can be found [here](https://github.com/elliottech/lighter-python/blob/main/examples/orders/modify_order_version_http.py\)) (http) and [here](https://github.com/elliottech/lighter-python/blob/main/examples/orders/modify_order_version_ws.py) (ws). Order events will include `order_version`, and Trade events will include `ask_order_version`, `bid_order_version` .

## Order Expiry

When specifying an order expiry, provide a millisecond timestamp between 5 minutes and 30 days.

Order types, time in force, and other types of transactions are specified on [signer\_client.py](https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py) in the Python SDK, and in [constants.go](https://github.com/elliottech/lighter-go/blob/main/types/txtypes/constants.go) in the GO SDK.

<br />