---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# orderBookOrders

Get order book orders

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/orderBookOrders": {
      "get": {
        "summary": "orderBookOrders",
        "operationId": "orderBookOrders",
        "tags": [
          "order"
        ],
        "description": "Get order book orders",
        "parameters": [
          {
            "name": "market_id",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer",
              "format": "int16"
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
              "maximum": 250
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/OrderBookOrders"
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
      "OrderBookOrders": {
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
          "total_asks": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "asks": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/SimpleOrder"
            }
          },
          "total_bids": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "bids": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/SimpleOrder"
            }
          }
        },
        "title": "OrderBookOrders",
        "required": [
          "code",
          "total_asks",
          "asks",
          "total_bids",
          "bids"
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
      "SimpleOrder": {
        "type": "object",
        "properties": {
          "order_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "order_id": {
            "type": "string",
            "example": "1"
          },
          "owner_account_index": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "initial_base_amount": {
            "type": "string",
            "example": "0.1"
          },
          "remaining_base_amount": {
            "type": "string",
            "example": "0.1"
          },
          "price": {
            "type": "string",
            "example": "3024.66"
          },
          "order_expiry": {
            "type": "integer",
            "format": "int64",
            "example": "1640995200"
          },
          "transaction_time": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "SimpleOrder",
        "required": [
          "order_index",
          "order_id",
          "owner_account_index",
          "initial_base_amount",
          "remaining_base_amount",
          "price",
          "order_expiry",
          "transaction_time"
        ]
      }
    }
  }
}
```