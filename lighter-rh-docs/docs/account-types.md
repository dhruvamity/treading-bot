---
updatedAt: 2026-09-14T13:43:11.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Account Types

#### Premium Account (Opt-in) -- Suitable for HFT, the lowest latency on Lighter. Part of [volume quota program](https://apidocs.rh.lighter.xyz/docs/volume-quota).

Latency for maker & cancel orders is 0ms.

| Last 14D Volume | sendTx/sendTxBatch per minute | Maker/Taker Fee Discount | Maker Fee | Taker Fee | Taker Latency | Latency Improvement | Sub-accounts |
| :-------------- | :---------------------------- | :----------------------- | :-------- | :-------- | :------------ | :------------------ | :----------- |
| `< $1M`         | 4000                          |                          | 0.0120%   | 0.0350%   | 200 ms        |                     | 8            |
| `> $1M`         | 5000                          | 2.5%                     | 0.0117%   | 0.0341%   | 195 ms        | 2.5%                | 8            |
| `> $10M`        | 6000                          | 5%                       | 0.0114%   | 0.0333%   | 190 ms        | 5%                  | 8            |
| `> $20M`        | 7000                          | 10%                      | 0.0108%   | 0.0315%   | 180 ms        | 10%                 | 8            |
| `> $50M`        | 8000                          | 15%                      | 0.0102%   | 0.0298%   | 170 ms        | 15%                 | 8            |
| `> $200M`       | 12000                         | 20%                      | 0.0096%   | 0.0280%   | 160 ms        | 20%                 | 8            |
| `> $500M`       | 24000                         | 30%                      | 0.0084%   | 0.0245%   | 150 ms        | 25%                 | 8            |

#### Plus Account (Opt-in) -- Suitable for latency-insensitive users, looking to get increased rate limits.

Access via the [changeAccountTier](https://apidocs.rh.lighter.xyz/reference/changeaccounttier) endpoint.&#x20;

| Maker/Taker Fee | Taker Latency | Maker/Cancel Latency | sendTx/sendTxBatch per minute | Read-only weighted requests per minute | Sub-accounts |
| :-------------- | :------------ | :------------------- | :---------------------------- | :------------------------------------- | ------------ |
| 0.005%          | 300 ms        | 200 ms               | 4000                          | 24000                                  | 16           |

#### Standard Account (Default) -- Suitable for retail and latency-insensitive traders.

| Maker Fee | Taker Fee | Taker Latency | Maker/Cancel Latency |
| :-------- | :-------- | :------------ | :------------------- |
| 0%        | 0%        | 300 ms        | 200 ms               |

#### Account Switch

You can change your Account Type (tied to your L1 address) using the `/changeAccountTier` endpoint.

You may call that endpoint if at least 24 hours have passed since the last call. To upgrade account type, there is no cooldown.

*Python snippet to switch tiers*:

```python Python: switch to premium
import asyncio
import logging
import lighter
import requests

logging.basicConfig(level=logging.DEBUG)

BASE_URL = "https://api.rh.lighter.xyz"

# You can get the values from the system_setup.py script
# API_KEY_PRIVATE_KEY =
# ACCOUNT_INDEX =
# API_KEY_INDEX =


async def main():
    client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )

    err = client.check_client()
    if err is not None:
        print(f"CheckClient error: {err}")
        return

    auth, err = client.create_auth_token_with_expiry(
        lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
    )

    response = requests.post(
        f"{BASE_URL}/api/v1/changeAccountTier",
        data={"account_index": ACCOUNT_INDEX, "new_tier": "premium"},
        headers={"Authorization": auth},
    )
    if response.status_code != 200:
        print(f"Error: {response.text}")
        return
    print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
```
```python Python: switch to standard
import asyncio
import logging
import lighter
import requests

logging.basicConfig(level=logging.DEBUG)

BASE_URL = "https://mainnet.zklighter.elliot.ai"

# You can get the values from the system_setup.py script
# API_KEY_PRIVATE_KEY =
# ACCOUNT_INDEX =
# API_KEY_INDEX =


async def main():
    client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )

    err = client.check_client()
    if err is not None:
        print(f"CheckClient error: {err}")
        return

    auth, err = client.create_auth_token_with_expiry(
        lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
    )

    response = requests.post(
        f"{BASE_URL}/api/v1/changeAccountTier",
        data={"account_index": ACCOUNT_INDEX, "new_tier": "standard"},
        headers={"Authorization": auth},
    )
    if response.status_code != 200:
        print(f"Error: {response.text}")
        return
    print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
```

#### How fees are collected:

In isolated margin, fees are taken from the isolated position itself, but if needed, we automatically transfer from cross margin to keep the position healthy. In cross margin, fees are always deducted directly from the available cross balance.

Sub-accounts share the same tier as the main L1 address on the account.