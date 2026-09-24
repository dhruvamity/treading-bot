---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# transfer_history

Get transfer history. To fetch an account index, you will need to `auth` the request, unless it's a public pool.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/transfer/history": {
      "get": {
        "summary": "transfer_history",
        "operationId": "transfer_history",
        "tags": [
          "transaction"
        ],
        "description": "Get transfer history. To fetch an account index, you will need to `auth` the request, unless it's a public pool.",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "account_index",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "cursor",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "type",
            "in": "query",
            "required": false,
            "schema": {
              "type": "array",
              "items": {
                "type": "string",
                "enum": [
                  "all",
                  "L2Transfer",
                  "L2MintShares",
                  "L2BurnShares",
                  "L2StakeAssets",
                  "L2UnstakeAssets",
                  "L2CreatePublicPool",
                  "L2CreateStakingPool",
                  "L1BurnShares",
                  "L1UnstakeAssets",
                  "ReferralProgramPayout"
                ]
              }
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/TransferHistory"
                }
              }
            }
          },
          "400": {
            "description": "Bad request",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ResultCode"
                }
              }
            }
          }
        }
      }
    }
  },
  "servers": [
    {
      "url": "https://api.rh.lighter.xyz/"
    }
  ],
  "components": {
    "schemas": {
      "ResultCode": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32",
            "example": "200"
          },
          "message": {
            "type": "string"
          }
        },
        "title": "ResultCode",
        "required": [
          "code"
        ]
      },
      "TransferHistory": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32",
            "example": "200"
          },
          "message": {
            "type": "string"
          },
          "transfers": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/TransferHistoryItem"
            }
          },
          "cursor": {
            "type": "string"
          }
        },
        "title": "TransferHistory",
        "required": [
          "code",
          "transfers",
          "cursor"
        ]
      },
      "TransferHistoryItem": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "amount": {
            "type": "string",
            "example": "0.1"
          },
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "type": {
            "type": "string",
            "enum": [
              "L2TransferInflow",
              "L2TransferOutflow",
              "L2BurnSharesInflow",
              "L2BurnSharesOutflow",
              "L2MintSharesInflow",
              "L2MintSharesOutflow",
              "L2SelfTransfer",
              "L2StakeAssetInflow",
              "L2StakeAssetOutflow",
              "L2UnstakeAssetInflow",
              "L2UnstakeAssetOutflow",
              "L2ForceBurnSharesInflow",
              "L2ForceBurnSharesOutflow",
              "L1BurnSharesInflow",
              "L1BurnSharesOutflow",
              "L1UnstakeAssetInflow",
              "L1UnstakeAssetOutflow",
              "L2CreatePublicPoolInflow",
              "L2CreatePublicPoolOutflow",
              "L2CreateStakingPoolInflow",
              "L2CreateStakingPoolOutflow"
            ]
          },
          "from_l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "to_l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "from_account_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "to_account_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "tx_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "asset_id": {
            "type": "integer",
            "format": "int16"
          },
          "fee": {
            "type": "string"
          },
          "from_route": {
            "type": "string",
            "enum": [
              "spot",
              "perps"
            ]
          },
          "to_route": {
            "type": "string",
            "enum": [
              "spot",
              "perps"
            ]
          }
        },
        "title": "TransferHistoryItem",
        "required": [
          "id",
          "amount",
          "timestamp",
          "type",
          "from_l1_address",
          "to_l1_address",
          "from_account_index",
          "to_account_index",
          "tx_hash",
          "asset_id",
          "fee",
          "from_route",
          "to_route"
        ]
      }
    }
  }
}
```