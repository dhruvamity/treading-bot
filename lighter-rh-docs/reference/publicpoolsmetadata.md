---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# publicPoolsMetadata

Get public pools metadata. `auth` is required in case you specify an account_index. You will see public pools with an index that starts an n-1 of the one you specify. To see staking pools, use `filter=stake`

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/publicPoolsMetadata": {
      "get": {
        "summary": "publicPoolsMetadata",
        "operationId": "publicPoolsMetadata",
        "tags": [
          "account"
        ],
        "description": "Get public pools metadata. `auth` is required in case you specify an account_index. You will see public pools with an index that starts an n-1 of the one you specify. To see staking pools, use `filter=stake`",
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
            "name": "filter",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "all",
                "user",
                "protocol",
                "account_index",
                "stake"
              ]
            }
          },
          {
            "name": "index",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "limit",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 1,
              "maximum": 100
            }
          },
          {
            "name": "account_index",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/RespPublicPoolsMetadata"
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
      "AccountAsset": {
        "type": "object",
        "properties": {
          "symbol": {
            "type": "string",
            "example": "USDC"
          },
          "asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 1
          },
          "balance": {
            "type": "string",
            "example": "1000"
          },
          "locked_balance": {
            "type": "string",
            "example": "1000"
          },
          "margin_balance": {
            "type": "string",
            "example": "1000"
          },
          "margin_mode": {
            "type": "string",
            "example": "enabled",
            "enum": [
              "enabled",
              "disabled"
            ]
          },
          "multiplier": {
            "type": "string",
            "example": "1.000000000000000000"
          }
        },
        "title": "AccountAsset",
        "required": [
          "symbol",
          "asset_id",
          "balance",
          "locked_balance",
          "margin_mode",
          "margin_balance",
          "multiplier"
        ]
      },
      "PublicPoolMetadata": {
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
          "account_index": {
            "type": "integer",
            "format": "int64",
            "example": "3"
          },
          "account_type": {
            "type": "integer",
            "format": "uint8",
            "example": "1"
          },
          "name": {
            "type": "string"
          },
          "l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "annual_percentage_yield": {
            "type": "number",
            "format": "double",
            "example": "20.5000"
          },
          "status": {
            "type": "integer",
            "format": "uint8",
            "example": "0"
          },
          "operator_fee": {
            "type": "string",
            "example": "100"
          },
          "total_asset_value": {
            "type": "string",
            "example": "19995"
          },
          "total_shares": {
            "type": "integer",
            "format": "int64",
            "example": "100000"
          },
          "account_share": {
            "$ref": "#/components/schemas/PublicPoolShare"
          },
          "assets": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/AccountAsset"
            }
          },
          "created_at": {
            "type": "integer",
            "format": "int64"
          },
          "master_account_index": {
            "type": "integer",
            "format": "int64"
          },
          "sharpe_ratio": {
            "type": "number",
            "format": "double"
          },
          "total_perps_value": {
            "type": "string"
          },
          "total_spot_value": {
            "type": "string"
          }
        },
        "title": "PublicPoolMetadata",
        "required": [
          "code",
          "account_index",
          "account_type",
          "name",
          "l1_address",
          "annual_percentage_yield",
          "status",
          "operator_fee",
          "total_asset_value",
          "total_shares",
          "assets",
          "created_at",
          "master_account_index",
          "sharpe_ratio",
          "total_perps_value",
          "total_spot_value"
        ]
      },
      "PublicPoolShare": {
        "type": "object",
        "properties": {
          "public_pool_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "shares_amount": {
            "type": "integer",
            "format": "int64",
            "example": "3000"
          },
          "entry_usdc": {
            "type": "string",
            "example": "3000"
          },
          "entry_timestamp": {
            "type": "integer",
            "format": "int64"
          },
          "principal_amount": {
            "type": "string"
          }
        },
        "title": "PublicPoolShare",
        "required": [
          "public_pool_index",
          "shares_amount",
          "entry_usdc",
          "entry_timestamp",
          "principal_amount"
        ]
      },
      "RespPublicPoolsMetadata": {
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
          "public_pools": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PublicPoolMetadata"
            }
          }
        },
        "title": "RespPublicPoolsMetadata",
        "required": [
          "code",
          "public_pools"
        ]
      },
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
      }
    }
  }
}
```