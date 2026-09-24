---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# assetDetails

Get asset details for a specific asset or all assets

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/assetDetails": {
      "get": {
        "summary": "assetDetails",
        "operationId": "assetDetails",
        "tags": [
          "order"
        ],
        "description": "Get asset details for a specific asset or all assets",
        "parameters": [
          {
            "name": "asset_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int16"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/AssetDetails"
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
      "Asset": {
        "type": "object",
        "properties": {
          "asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 1
          },
          "symbol": {
            "type": "string",
            "example": "ETH"
          },
          "l1_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 18
          },
          "decimals": {
            "type": "integer",
            "format": "uint8",
            "example": 18
          },
          "min_transfer_amount": {
            "type": "string",
            "example": "0.01"
          },
          "min_withdrawal_amount": {
            "type": "string",
            "example": "0.01"
          },
          "margin_mode": {
            "type": "string",
            "example": "enabled",
            "enum": [
              "enabled",
              "disabled",
              "priced_only"
            ]
          },
          "index_price": {
            "type": "string",
            "example": "3024.66"
          },
          "l1_address": {
            "type": "string",
            "example": "0x0000000000000000000000000000000000000000"
          },
          "global_supply_cap": {
            "type": "string",
            "example": "1000000"
          },
          "liquidation_fee": {
            "type": "string",
            "example": "0.01"
          },
          "liquidation_threshold": {
            "type": "string",
            "example": "0.8"
          },
          "loan_to_value": {
            "type": "string",
            "example": "0.5"
          },
          "price_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "total_supplied": {
            "type": "string",
            "example": "100"
          },
          "user_supply_cap": {
            "type": "string",
            "example": "1000"
          },
          "liquidation_factor": {
            "type": "string",
            "example": "0.9"
          },
          "multiplier": {
            "type": "string",
            "example": "1.000000000000000000"
          }
        },
        "title": "Asset",
        "required": [
          "asset_id",
          "symbol",
          "l1_decimals",
          "decimals",
          "min_transfer_amount",
          "min_withdrawal_amount",
          "margin_mode",
          "index_price",
          "price_decimals",
          "l1_address",
          "loan_to_value",
          "liquidation_threshold",
          "liquidation_factor",
          "liquidation_fee",
          "global_supply_cap",
          "user_supply_cap",
          "total_supplied",
          "multiplier"
        ]
      },
      "AssetDetails": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32",
            "example": 200
          },
          "message": {
            "type": "string"
          },
          "asset_details": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Asset"
            }
          }
        },
        "title": "AssetDetails",
        "required": [
          "code",
          "asset_details"
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