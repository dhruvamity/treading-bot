---
updatedAt: 2026-08-24T14:32:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# API Keys

You can use API keys to trade and manage your lighter account programmatically. Each API key will be assigned an index, ranging from 0 to 254 - note that indexes `{0,1,2,3,157}` are reserved for desktop and mobile interfaces.

Each internal account, whether that's a master account or a sub-account, will have its own separate set of api keys - and each comes with a public & private key, and its own nonce.

## Permissions

API keys enable both write and read permissions, allowing you to query auth-gated REST endpoints and Websocket channels, but also send transactions and process withdrawals.

While it allows for processing withdrawals, you should consider that only secure withdrawals can be executed without also signing the account's Ethereum private key - as they can only be sent to the same L1 address that created the account. On the other hand, Fast Withdrawals and Transfers can be sent to other L1 addresses and will require signing with the wallet's private key.

## Authentication

To interact with certain endpoints, you will need to generate an auth token using your API private key. You can do so using our [GO SDK](https://github.com/elliottech/lighter-go), or use the create\_auth\_token\_with\_expiry() function in our [Python SDK](https://github.com/elliottech/lighter-python). Each auth code can have a maximum expiry of 8 hours, and it uses the following structure: `{expiry_unix}:{account_index}:{api_key_index}:{random_hex}`.

## Read-only Authentication

Using a canonical auth code, you can generate read-only auth tokens - those won't allow placing trades nor request withdrawals (essentially, you won't be able to sign transactions hence initialize a signer client), but you will be able to access auth-gated data via API. Each read-only auth code can have a maximum expiry of 10 years, and a minimum of 1 day. They will use the following structure: `ro:{account_index}:{single|all}:{expiry_unix}:{random_hex}`.

## Maker-only API keys

Clients on a premium account may mark up to 251 keys per account index as maker-only. Those keys will be restricted to 0ms speed bump transactions only:

* Post-only (ALO) orders
* Modify orders on ALO orders
* Cancel, and Cancel All orders

The direct benefit of using this configuration is having access to an optimized execution path, with reduced scheduling latency. Keys reserved by our front-end (0-3) cannot be set as maker-only, and we apply a one-hour cooldown in between changes.

You can set a list of keys as maker-only using [setMakerOnlyApiKeys](https://apidocs.rh.lighter.xyz/reference/setmakeronlyapikeys), and get a list of maker-only keys using [getMakerOnlyApiKeys](https://apidocs.rh.lighter.xyz/reference/getmakeronlyapikeys).

## How to create API keys programmatically

You can create new keys programmatically using either the [Python SDK](https://github.com/elliottech/lighter-python/blob/main/examples/system_setup.py#L58), or the [GO SDK](https://github.com/elliottech/lighter-go/blob/0d4ddf155950e8ad62164a9d34a139750deebe37/README.md?plain=1#L26). While generating the API keys does not require your L1 private key, associating them with your Lighter account does. You can either do this via the SDKs, or interact with Lighter's smart contract directly using the ChangePubKey function (this is particularly helpful if you're running a multi-sig).

## Nonce management

Each API key will have its own nonce, and the API servers require it to be increased by 1 for each transaction you submit, unless `SkipNonce` is enabled. While the [Python SDK](https://github.com/elliottech/lighter-python) handles nonce management on its own, you might want to manage it locally to handle more complex systems. Since some types of transactions may be subject to speed bumps based on your [account type](https://apidocs.rh.lighter.xyz/docs/account-types), and they are processed sequentially, you may want to use multiple API keys for the same account e.g. one for each type of order to always guarantee the fastest execution.

If you'd like to skip nonces, you can set the `SkipNonce`  (`skip_nonce` in the Python SDK) attribute (4th in `L2TxAttributes`) to `1`. If this attribute is not specified, we require `new_nonce = old_nonce + 1`. In any case, the following must hold true when skipping nonces: `2^47-1 > new_nonce > old_nonce`. Otherwise, nonces are capped at `2^48-1`.