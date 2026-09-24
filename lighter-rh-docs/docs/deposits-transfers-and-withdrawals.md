---
updatedAt: 2026-07-03T17:56:18.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Deposits, Transfers, and Withdrawals

## Deposit via Robinhood Chain

To deposit via Robinhood Chain mainnet, you can use the `deposit` method directly. You'll need to specify  the following parameters:

* `_to`: the L1 address you want to credit the deposit to
* `_assetIndex`: the asset you want to deposit. You can grab the correct asset id from the [assetDetails](https://apidocs.rh.lighter.xyz/reference/assetdetails) endpoint
* `_routeType`: whether you want to deposit the asset to your perps (0), or spot account (1). Only USDG can be deposited to your perps account
* `_amount`: the amount you're depositing. *it should be in line with the ERC20's decimals. E.g. 6 decimals for USDG, 18 decimals for Ether etc. 1 USDC, or equivalent, minimum.*

If you're depositing assets different from ETH (e.g. USDG), make sure to approve spending for Lighter's smart contract (0x94bAB9693Ba2f6358507eFfcbd372b0660AFfF9d) for that ERC20. There is a minimum of 1 USDG, and equivalent for other ERC20s, per deposit.

## Deposit via Robinhood Chain (intents)

An intent address is persistent, and tied to the address that generated it. It will credit USDG upon transfers, and doesn't require contract calls as the above method does. Minimum deposit is 1 USDG. You can either generate an intent address via the front-end, or via [API](https://apidocs.rh.lighter.xyz/reference/createintentaddress):

```shell
`curl -X POST https://https://api.rh.lighter.xyz/api/v1/createIntentAddress \`

`-H "Content-Type: application/x-www-form-urlencoded" \`

`-d "chain_id=4663&from_addr=0xyourL1address&amount=0&is_external_deposit=true"`
```

## Withdrawals: Secure and Fast

You can process both [secure](https://github.com/elliottech/lighter-python/blob/main/examples/withdraw_normal.py), [fast withdrawals](https://github.com/elliottech/lighter-python/blob/main/examples/withdraw_fast.py#L59), and [transfers](https://github.com/elliottech/lighter-python/blob/main/examples/transfer.py) via both SDKs, you can find linked examples using the Python SDK. If you're processing a Fast Withdrawal (USDG only, 1 USDG minimum), or a Transfer to another address, you'll need to sign with your private key as well. If you prefer, you can process secure withdrawals from the contract directly, using the `withdraw` method. Transfers, and Secure & Fast Withdrawals all have a 1 USDG, or equivalent, minimum.

<br />