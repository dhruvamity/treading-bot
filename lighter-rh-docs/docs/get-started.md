---
updatedAt: 2026-09-11T11:49:07.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Get Started

Welcome to the Lighter SDK and API Introduction. Here, we will go through everything from the system setup, to creating and cancelling all types of orders, to fetching exchange data.&#x20;

To keep up to date with Lighter's API, join this [Telegram Channel ](https://t.me/+4OylVDvI0z9lZDFk)or #api-updates on our [Discord](https://discord.gg/lighterxyz).&#x20;

For best colocation, use AWS Tokyo ap-northeast-1a (apne1-az4).&#x20;

The relevant Chain IDs for the Lighter app-chain are: `466324` (mainnet), `300` (testnet).&#x20;

Direct access to skip AWS CloudFront is available for premium accounts on the best tier; interested parties can contact us via the [#support](https://discord.com/channels/987399783985590322/1383469922943766608) Discord channel to gain access.

To access testnet, you should switch `BASE_URL` to `https://api.rh-testnet.lighter.xyz/api/v1/`

<br />

## Install the SDK

**Python:**

```shell
pip install lighter-sdk
```

**Go:**

```shell
go get https://github.com/elliottech/lighter-go
```

## Find Your Account Index

Your account index is Lighter's integer identifier for your account. If you use sub-accounts, there will be multiple account indexes tied to the same L1 wallet. Query it using your L1 (Ethereum) address:

```python
import asyncio
import lighter

BASE_URL = "https://api.rh.lighter.xyz/"
L1_ADDRESS = "0x123"

async def main():
    client = lighter.ApiClient(lighter.Configuration(host=BASE_URL))
    resp = await lighter.AccountApi(client).accounts_by_l1_address(l1_address=L1_ADDRESS)
    print(resp.sub_accounts[0].index)
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Create an API Key

API keys enable SDK signing and authenticated API requests; each is tied to a single account index and has its own nonce. You can create up to 253 keys (indices 2-254). Indices 0-1 are reserved for the web/mobile interfaces. Finally, the 255 index can be used as a value for the *api\_key\_index* parameter of the **apikeys** method of the **AccountApi** for getting the data about all the API keys. See this page for [more details](https://apidocs.rh.lighter.xyz/docs/api-keys).

## Initialize the Signer Client

In order to create a transaction (create/cancel/modify order), you need to use the SignerClient. Initialize with the following code:

```python
 client = lighter.SignerClient(  
        url=BASE_URL,  
        api_private_keys={API_KEY_INDEX:PRIVATE_KEY},  
        account_index=ACCOUNT_INDEX
    )
```

The code for the signer can be found in the same repo, in the [signer\_client.py](https://github.com/elliottech/lighter-python/blob/main/lighter/signer_client.py) file. You may notice that it uses a binary for the signer: the code for it can be found in the [lighter-go](https://github.com/elliottech/lighter-go) public repo, and you can compile it yourself using the [justfile](https://github.com/elliottech/lighter-go/blob/main/justfile).

## Nonce

When signing a transaction, you may need to provide a nonce (number used once). A nonce needs to be incremented each time you sign something. You can get the next nonce that you need to use using the **TransactionApi’s** *next\_nonce* method or take care of incrementing it yourself. Note that each nonce is handled per **API\_KEY**.

If you'd like to skip nonces, you can set the `SkipNonce`  (`skip_nonce` in the Python SDK) attribute (4th in `L2TxAttributes`) to `1`. If this attribute is not specified, we require `new_nonce = old_nonce + 1`. In any case, the following must hold true when skipping nonces: `2^47-1 > new_nonce > old_nonce`. Otherwise, nonces are capped at `2^48-1`.

## Signing a transaction

One can sign a transaction using the **SignerClient’s** *sign\_create\_order*, *sign\_modify\_order*, *sign\_cancel\_order* and its other similar methods. For actually pushing the transaction, you need to call *send\_tx* or *send\_tx\_batch* using the **TransactionApi**. Here’s an [example](https://github.com/elliottech/lighter-python/blob/main/examples/batch-orders/send_batch_tx_http.py) that includes such an operation. Alternatively, you can use *create\_order*, which will send the tx as well.

Note that *base\_amount, price* are to be passed as integers, and *client\_order\_index* is a unique (across all markets) identifier you provide for you to be able to reference this order later (e.g. if you want to cancel it). The SDK handles nonce management automatically. For complex systems, you can implement local nonce management.

### Price and Size precision

Query the [orderBookDetails](https://apidocs.rh.lighter.xyz/reference/orderbookdetails) endpoint to get decimal precision for each market:

```shell
GET /api/v1/orderBookDetails
```

### Place Your First Market Order

```python
    tx, tx_hash, err = await client.create_order(
        market_index=0, # ETH perps market
        client_order_index=1234, 
        base_amount=10,  # 0.001 ETH
        price=3100_00,  # $3100 -- worst acceptable price for the order
        is_ask=False, # Bid, i.e. buy order
        order_type=client.ORDER_TYPE_MARKET,
        time_in_force=client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
        reduce_only=False,
        order_expiry=client.DEFAULT_IOC_EXPIRY,
    )
```

### Place Your First Limit Order

```python
    tx, tx_hash, err = await client.create_order(
        market_index=0, # ETH perps market
        client_order_index=1234,
        base_amount=100,  # 0.01 ETH
        price=2900_00,  # $2900
        is_ask=False,  # Bid, i.e. buy order
        order_type=client.ORDER_TYPE_LIMIT,
        time_in_force=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
        reduce_only=False,
        order_expiry=client.DEFAULT_28_DAY_ORDER_EXPIRY,
    )
```

### Cancel an order

```python
    tx, tx_hash, err = await client.cancel_order(
        market_index=market_index,
        order_index=1234
    )
```

### Modify an order

```python
    tx, tx_hash, err = await client.modify_order(
        market_index=0,
        order_index=1234,
        base_amount=100,  # 0.01 ETH
        price=2800_00,  # $2800
    )
```

## Authentication Tokens

For REST API calls and Websocket channels requiring authentication:

```python
        auth_token, err = client.create_auth_token_with_expiry(
            deadline=3600, # seconds. Max is 8 hours, default is 10 minutes
            api_key_index=API_KEY_INDEX,
        )
```

Alternatively, you can use a read-only auth token. See more details [here](https://apidocs.rh.lighter.xyz/docs/api-keys).

## API via SDK

The SDK provides API classes that make calling the Lighter API easier. Here are some of them and the most important of their methods:

* **AccountApi** - provides account data
  * *account* - get account data either by l1\_address or index
  * *accounts\_by\_l1\_address* - get data about all the accounts (master account and subaccounts)
  * *apikeys* - get data about the api keys of an account (use api\_key\_index = 255 for getting data about all the api keys)
* **TransactionApi** - provides transaction-related data
  * *next\_nonce* - get next nonce to be used for signing a transaction using a certain api key
  * *send\_tx* - push a transaction
  * *send\_tx\_batch* - push several transactions at once
* **OrderApi** - provides data about orders, trades and the orderbook
  * *order\_book\_details* - get data about a specific market’s orderbook
  * *order\_books* - get data about all markets’ orderbooks

You can find the rest [here](https://github.com/elliottech/lighter-python/tree/main/lighter/api). We also provide an [example](https://github.com/elliottech/lighter-python/blob/main/examples/market-data/get_info.py) showing how to use some of these. For the methods that require an auth token, you can generate one using the *create\_auth\_token\_with\_expiry* method of the **SignerClient** (the same applies to the websockets auth).

## WebSockets

Lighter also provides access to essential info using websockets. A simple version of an **WsClient** for subscribing to account and orderbook updates is implemented [here](https://github.com/elliottech/lighter-python/blob/main/lighter/ws_client.py). You can also take it as an example implementation of such a client.

To get access to more data, you will need to connect to the websockets without the provided WsClient. You can find the streams you can connect to, how to connect, and the data they provide in the [websockets](https://apidocs.rh.lighter.xyz/docs/websocket) section.