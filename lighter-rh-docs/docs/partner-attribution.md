---
updatedAt: 2026-09-11T11:57:50.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Partner Attribution

This guide explains how partners can integrate with Lighter programmatically to earn additional fees for trades executed through their applications, or just for tracking purposes. The program is permissionless, allowing any account index to be used to collect fees, including subaccounts.

***

## Definitions

**Partner**: You, the integrator building on top of Lighter.

**Client**: Your user, or customer, whose transactions you send on their behalf. Both standard and premium accounts are accepted.

***

## Requirements

Both approvals and trades containing partner attribution attributes with non-zero integrator fees will require the client submitting them to be on a Plus or Premium [account type](https://apidocs.rh.lighter.xyz/docs/account-types). Transactions coming from a Standard account will be rejected. To check the account type of a client, you can use the [accountLimits](https://apidocs.rh.lighter.xyz/reference/accountlimits) endpoint. To change account type, use [changeAccountTier](https://apidocs.rh.lighter.xyz/reference/changeaccounttier).

## Integrator Approval

Before you can append partner attributes to client transactions, they must submit a `ApproveIntegrator` transaction. Approving an account index associated with a different L1 address requires an L1 signature. However, if the account index is associated with the same L1 address, or fees are zero, only an L2 signature is needed.  This transaction authorizes you to charge additional taker/maker fees on trades originating from orders created through your platform, as defined via the [systemConfig](https://apidocs.rh.lighter.xyz/reference/systemconfig) endpoint.

At any point in time, there can only be a maximum of 4 approved Partners per Client.

### Example (Python SDK)

```python
client.approve_integrator(
        eth_private_key=STRING,
        integrator_account_index=INTEGER,
        max_perps_taker_fee=INTEGER,
        max_perps_maker_fee=INTEGER,
        max_spot_taker_fee=INTEGER,
        max_spot_maker_fee=INTEGER,
        approval_expiry=INTEGER # in ms  
    )
```

```python
client.approve_integrator(
        eth_private_key="",
        integrator_account_index=1234,
        max_perps_taker_fee=1000, # 10 bps
        max_perps_maker_fee=1000, # 10 bps
        max_spot_taker_fee=1000,  # 10 bps
        max_spot_maker_fee=1000,  # 10 bps
        approval_expiry=1775518466000 # in ms
    )
```

You can find the full example [here](https://github.com/elliottech/lighter-python/blob/main/examples/integrator_approve.py). To revoke an integrator before the allowance expires, you can send another `ApproveIntegrator` request setting all fees to zero, requiring just an L2 signature.

### Fields Explanation

**IntegratorAccountIndex**: Partner's account index. All partner fees collected from client trades will be credited to this account. For perpetual markets, the fees are paid and credited in USDC (perps). For spot markets, the fees are paid and credited in the received asset (e.g. buying spot ETH means the Client pays fees in ETH directly).

**Max Fees**: These values define the maximum fees you are allowed to charge your client.

Fees are calculated as:

`fee(bps) = trade_size × (fee_value / 1e6)`

**Example:**

```
MaxPerpsTakerFee = 500
500 / 1e6 = 0.0005 = 5 basis points
```

This means you can charge up to **5 bps** of the trade quote for each perpetual taker order.

**System-wide Maximums**

Lighter enforces global maximum fee limits. If you submit an approval transaction exceeding these limits, the transaction will fail. Current limits can be checked via the [systemConfig](https://apidocs.rh.lighter.xyz/reference/systemconfig)  endpoint.

**ApprovalExpiry**

The timestamp when the integrator approval expires in a form of Unix timestamp in **milliseconds**. After this time, the partner must obtain approval again from the client.

The maximum value you can send is equivalent to `2^48-1`.

## Creating Orders for Your Client

Once the integrator approval is successfully submitted, you can begin sending transactions for the client and collecting partner fees.

When creating orders, include the following fields:

```python
IntegratorAccountIndex: INTEGER # the partner account index, where integrator fees will accrue
IntegratorMakerFee: INTEGER     # fee the partner wants to charge for maker trades
IntegratorTakerFee: INTEGER     # fee the partner wants to charge for taker trades
```

These fees **must not exceed the maximum values** defined during the approval step.

### Example: Creating an Order via Python SDK

```python
client.create_order(
        market_index=0,
        client_order_index=0 # bound to 2^48-1
        base_amount=1000,  # decimals as described in the orderBookDetails endpoint
        price=4050_00,  # decimals as descibred in the orderBookDetails endpoin
        is_ask=True, # sell
        order_type=client.ORDER_TYPE_LIMIT,
        time_in_force=client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
        reduce_only=False,
        trigger_price=0,
        integrator_account_index=integrator_account_index,
        integrator_taker_fee=integrator_taker_fee,
        integrator_maker_fee=integrator_maker_fee,
        nonce=nonce,
        api_key_index=api_key_index,
    )
```

You can find the full example [here](https://github.com/elliottech/lighter-python/blob/main/examples/integrator_create_modify_order.py).

Additionally, a TypeScript Client Demo has been made available, open-sourcing our client-side state store, the WebSocket pipeline that feeds it, the trading math, and the types that describe it all: [https://github.com/elliottech/lighter-ts](https://github.com/elliottech/lighter-ts "https://github.com/elliottech/lighter-ts")