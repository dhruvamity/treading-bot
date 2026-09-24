---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# orderBooks

Get order books metadata.<hr>**Response Description:**<br><br>1) **Taker and maker fees** are in percentage.<br>2) **Min base amount:** The amount of base token that can be traded in a single order.<br>3) **Min quote amount:** The amount of quote token that can be traded in a single order.<br>4) **Supported size decimals:** The number of decimal places that can be used for the size of the order.<br>5) **Supported price decimals:** The number of decimal places that can be used for the price of the order.<br>6) **Supported quote decimals:** Size Decimals + Quote Decimals.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/orderBooks": {
      "get": {
        "summary": "orderBooks",
        "operationId": "orderBooks",
        "tags": [
          "order"
        ],
        "description": "Get order books metadata.<hr>**Response Description:**<br><br>1) **Taker and maker fees** are in percentage.<br>2) **Min base amount:** The amount of base token that can be traded in a single order.<br>3) **Min quote amount:** The amount of quote token that can be traded in a single order.<br>4) **Supported size decimals:** The number of decimal places that can be used for the size of the order.<br>5) **Supported price decimals:** The number of decimal places that can be used for the price of the order.<br>6) **Supported quote decimals:** Size Decimals + Quote Decimals.",
        "parameters": [
          {
            "name": "market_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int16",
              "default": "255"
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
                "spot",
                "perp"
              ],
              "default": "all"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/OrderBooks"
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
      "OrderBook": {
        "type": "object",
        "properties": {
          "symbol": {
            "type": "string",
            "example": "ETH"
          },
          "market_id": {
            "type": "integer",
            "format": "int16",
            "example": 0
          },
          "market_type": {
            "type": "string",
            "example": "perp",
            "enum": [
              "perp",
              "spot"
            ]
          },
          "base_asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 0
          },
          "quote_asset_id": {
            "type": "integer",
            "format": "int16",
            "example": 0
          },
          "status": {
            "type": "string",
            "example": "active",
            "enum": [
              "inactive",
              "active"
            ]
          },
          "taker_fee": {
            "type": "string",
            "example": "0.0001"
          },
          "maker_fee": {
            "type": "string",
            "example": "0.0000"
          },
          "liquidation_fee": {
            "type": "string",
            "example": "0.01"
          },
          "min_base_amount": {
            "type": "string",
            "example": "0.01"
          },
          "min_quote_amount": {
            "type": "string",
            "example": "0.1"
          },
          "supported_size_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "supported_price_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "supported_quote_decimals": {
            "type": "integer",
            "format": "uint8",
            "example": "4"
          },
          "order_quote_limit": {
            "type": "string",
            "example": "281474976.710655"
          },
          "is_maker_fee_enabled": {
            "type": "boolean",
            "example": true
          },
          "is_taker_fee_enabled": {
            "type": "boolean",
            "example": true
          },
          "created_at": {
            "type": "string",
            "example": "1640995200000"
          },
          "multiplier": {
            "type": "string",
            "example": "1.000000000000000000"
          }
        },
        "title": "OrderBook",
        "required": [
          "symbol",
          "market_id",
          "market_type",
          "base_asset_id",
          "quote_asset_id",
          "status",
          "taker_fee",
          "is_taker_fee_enabled",
          "maker_fee",
          "is_maker_fee_enabled",
          "liquidation_fee",
          "min_base_amount",
          "min_quote_amount",
          "order_quote_limit",
          "supported_size_decimals",
          "supported_price_decimals",
          "supported_quote_decimals",
          "created_at",
          "multiplier"
        ]
      },
      "OrderBooks": {
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
          "order_books": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/OrderBook"
            }
          }
        },
        "title": "OrderBooks",
        "required": [
          "code",
          "order_books"
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