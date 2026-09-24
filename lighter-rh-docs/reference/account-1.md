---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# account

Get account by an account's index, or L1 address

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/account": {
      "get": {
        "summary": "account",
        "operationId": "account",
        "tags": [
          "account"
        ],
        "description": "Get account by an account's index, or L1 address",
        "parameters": [
          {
            "name": "by",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "index",
                "l1_address"
              ]
            }
          },
          {
            "name": "value",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "active_only",
            "in": "query",
            "required": false,
            "description": "Hide markets for which leverage and margin settings are present (meaning the account traded it at least once), but with no active position.",
            "schema": {
              "type": "boolean",
              "default": false
            }
          },
          {
            "name": "cursor",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/DetailedAccounts"
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
      "AccountPosition": {
        "type": "object",
        "properties": {
          "market_id": {
            "type": "integer",
            "format": "int16",
            "example": "1"
          },
          "symbol": {
            "type": "string",
            "example": "ETH"
          },
          "initial_margin_fraction": {
            "type": "string",
            "example": "20.00"
          },
          "open_order_count": {
            "type": "integer",
            "format": "int64",
            "example": "3"
          },
          "pending_order_count": {
            "type": "integer",
            "format": "int64",
            "example": "3"
          },
          "position_tied_order_count": {
            "type": "integer",
            "format": "int64",
            "example": "3"
          },
          "sign": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          },
          "position": {
            "type": "string",
            "example": "3.6956"
          },
          "avg_entry_price": {
            "type": "string",
            "example": "3024.66"
          },
          "position_value": {
            "type": "string",
            "example": "3019.92"
          },
          "unrealized_pnl": {
            "type": "string",
            "example": "17.521309"
          },
          "realized_pnl": {
            "type": "string",
            "example": "2.000000"
          },
          "liquidation_price": {
            "type": "string",
            "example": "3024.66"
          },
          "total_funding_paid_out": {
            "type": "string",
            "example": "34.2"
          },
          "margin_mode": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          },
          "allocated_margin": {
            "type": "string",
            "example": "46342"
          },
          "total_discount": {
            "type": "string"
          },
          "margin_set_flag": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          }
        },
        "title": "AccountPosition",
        "required": [
          "market_id",
          "symbol",
          "initial_margin_fraction",
          "open_order_count",
          "pending_order_count",
          "position_tied_order_count",
          "sign",
          "position",
          "avg_entry_price",
          "position_value",
          "unrealized_pnl",
          "realized_pnl",
          "liquidation_price",
          "margin_mode",
          "margin_set_flag",
          "allocated_margin",
          "total_discount"
        ]
      },
      "DailyReturn": {
        "type": "object",
        "properties": {
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "daily_return": {
            "type": "number",
            "format": "double",
            "example": "0.0001"
          }
        },
        "title": "DailyReturn",
        "required": [
          "timestamp",
          "daily_return"
        ]
      },
      "DetailedAccount": {
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
          "account_type": {
            "type": "integer",
            "format": "uint8",
            "example": "1"
          },
          "account_trading_mode": {
            "type": "integer",
            "format": "uint8",
            "example": "1",
            "description": "Classic=0 and Unified=1"
          },
          "index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "cancel_all_time": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "total_order_count": {
            "type": "integer",
            "format": "int64",
            "example": "100"
          },
          "total_isolated_order_count": {
            "type": "integer",
            "format": "int64",
            "example": "100"
          },
          "pending_order_count": {
            "type": "integer",
            "format": "int64",
            "example": "100"
          },
          "available_balance": {
            "type": "string",
            "example": "19995"
          },
          "status": {
            "type": "integer",
            "format": "uint8",
            "example": "1"
          },
          "collateral": {
            "type": "string",
            "example": "46342"
          },
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "name": {
            "type": "string"
          },
          "description": {
            "type": "string"
          },
          "can_invite": {
            "type": "boolean",
            "format": "boolean",
            "description": " Remove After FE uses L1 meta endpoint"
          },
          "referral_points_percentage": {
            "type": "string",
            "description": " Remove After FE uses L1 meta endpoint"
          },
          "positions": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/AccountPosition"
            }
          },
          "assets": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/AccountAsset"
            }
          },
          "total_asset_value": {
            "type": "string",
            "example": "19995"
          },
          "cross_asset_value": {
            "type": "string",
            "example": "19995"
          },
          "pool_info": {
            "$ref": "#/components/schemas/PublicPoolInfo"
          },
          "shares": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PublicPoolShare"
            }
          },
          "created_at": {
            "type": "integer",
            "format": "int64"
          },
          "transaction_time": {
            "type": "integer",
            "format": "int64"
          },
          "pending_unlocks": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/PendingUnlock"
            }
          },
          "approved_integrators": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ApprovedIntegrator"
            }
          },
          "can_rfq": {
            "type": "boolean",
            "format": "boolean"
          },
          "cross_initial_margin_requirement": {
            "type": "string",
            "example": "5998.500000"
          },
          "cross_maintenance_margin_requirement": {
            "type": "string",
            "example": "2999.250000"
          },
          "can_rfq_market_ids": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "metadata": {
            "$ref": "#/components/schemas/SubAccountMetadata"
          },
          "agent_enabled": {
            "type": "boolean",
            "format": "boolean"
          }
        },
        "title": "DetailedAccount",
        "required": [
          "code",
          "account_type",
          "index",
          "l1_address",
          "cancel_all_time",
          "total_order_count",
          "total_isolated_order_count",
          "pending_order_count",
          "available_balance",
          "status",
          "collateral",
          "transaction_time",
          "account_index",
          "name",
          "description",
          "can_invite",
          "referral_points_percentage",
          "can_rfq",
          "can_rfq_market_ids",
          "agent_enabled",
          "created_at",
          "metadata",
          "positions",
          "assets",
          "total_asset_value",
          "cross_asset_value",
          "cross_initial_margin_requirement",
          "cross_maintenance_margin_requirement",
          "pool_info",
          "shares"
        ]
      },
      "DetailedAccounts": {
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
          "total": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "accounts": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/DetailedAccount"
            }
          },
          "next_cursor": {
            "type": "string"
          }
        },
        "title": "DetailedAccounts",
        "required": [
          "code",
          "total",
          "accounts",
          "next_cursor"
        ]
      },
      "PublicPoolInfo": {
        "type": "object",
        "properties": {
          "status": {
            "type": "integer",
            "format": "uint8",
            "example": "0"
          },
          "operator_fee": {
            "type": "string",
            "example": "100"
          },
          "min_operator_share_rate": {
            "type": "string",
            "example": "200"
          },
          "total_shares": {
            "type": "integer",
            "format": "int64",
            "example": "100000"
          },
          "operator_shares": {
            "type": "integer",
            "format": "int64",
            "example": "20000"
          },
          "annual_percentage_yield": {
            "type": "number",
            "format": "double",
            "example": "20.5000"
          },
          "daily_returns": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/DailyReturn"
            }
          },
          "share_prices": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/SharePrice"
            }
          },
          "sharpe_ratio": {
            "type": "number",
            "format": "double"
          },
          "strategies": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Strategy"
            }
          }
        },
        "title": "PublicPoolInfo",
        "required": [
          "status",
          "operator_fee",
          "min_operator_share_rate",
          "total_shares",
          "operator_shares",
          "annual_percentage_yield",
          "daily_returns",
          "share_prices",
          "sharpe_ratio",
          "strategies"
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
      "SharePrice": {
        "type": "object",
        "properties": {
          "timestamp": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "share_price": {
            "type": "number",
            "format": "double",
            "example": "0.0001"
          }
        },
        "title": "SharePrice",
        "required": [
          "timestamp",
          "share_price"
        ]
      },
      "SubAccountMetadata": {
        "type": "object",
        "properties": {
          "color": {
            "type": "string"
          }
        },
        "title": "SubAccountMetadata",
        "required": [
          "color"
        ]
      },
      "PendingUnlock": {
        "type": "object",
        "properties": {
          "unlock_timestamp": {
            "type": "integer",
            "format": "int64"
          },
          "asset_index": {
            "type": "integer",
            "format": "int16"
          },
          "amount": {
            "type": "string"
          }
        },
        "required": [
          "unlock_timestamp",
          "asset_index",
          "amount"
        ]
      },
      "Strategy": {
        "type": "object",
        "properties": {
          "collateral": {
            "type": "string"
          }
        },
        "required": [
          "collateral"
        ]
      },
      "ApprovedIntegrator": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer",
            "format": "int64",
            "example": "54621"
          },
          "name": {
            "type": "string",
            "example": "Integrator1"
          },
          "max_perps_taker_fee": {
            "type": "integer",
            "format": "int32",
            "example": "10"
          },
          "max_perps_maker_fee": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          },
          "max_spot_taker_fee": {
            "type": "integer",
            "format": "int32",
            "example": "10"
          },
          "max_spot_maker_fee": {
            "type": "integer",
            "format": "int32",
            "example": "1"
          },
          "approval_expiry": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200",
            "description": " Timestamp in milliseconds, after which the integrator is no longer approved"
          }
        },
        "title": "ApprovedIntegrator",
        "required": [
          "account_index",
          "name",
          "max_perps_taker_fee",
          "max_perps_maker_fee",
          "max_spot_taker_fee",
          "max_spot_maker_fee",
          "approval_expiry"
        ]
      }
    }
  }
}
```