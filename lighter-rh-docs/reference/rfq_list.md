---
updatedAt: 2026-07-15T17:58:59.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# rfq_list

List RFQs

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/rfq/list": {
      "get": {
        "summary": "rfq_list",
        "operationId": "rfq_list",
        "tags": [
          "account"
        ],
        "description": "List RFQs",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "account_index",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "default": "281474976710655"
            }
          },
          {
            "name": "status",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "opened",
                "order_created",
                "closed"
              ]
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
            "name": "limit",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "default": "20"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/RespListRFQs"
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
      "RFQMetadata": {
        "type": "object",
        "properties": {
          "requested_est_price": {
            "type": "string"
          },
          "requested_max_slippage": {
            "type": "string"
          },
          "requested_slippage": {
            "type": "string"
          },
          "worst_price": {
            "type": "string"
          },
          "mark_price": {
            "type": "string"
          },
          "requested_est_fill_fraction": {
            "type": "string"
          }
        },
        "title": "RFQMetadata",
        "required": [
          "requested_est_price",
          "requested_max_slippage",
          "requested_slippage",
          "requested_est_fill_fraction",
          "mark_price",
          "worst_price"
        ]
      },
      "RFQResponseEntry": {
        "type": "object",
        "properties": {
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "status": {
            "type": "string",
            "enum": [
              "acknowledged",
              "liquidity_provided",
              "not_interested"
            ]
          },
          "responded_at": {
            "type": "integer",
            "format": "int64"
          },
          "updated_at": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "RFQResponseEntry",
        "required": [
          "account_index",
          "status",
          "responded_at",
          "updated_at"
        ]
      },
      "RespListRFQs": {
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
          "rfqs": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/RFQEntry"
            }
          },
          "next_cursor": {
            "type": "string"
          }
        },
        "title": "RespListRFQs",
        "required": [
          "code",
          "rfqs"
        ]
      },
      "RFQEntry": {
        "type": "object",
        "properties": {
          "id": {
            "type": "integer",
            "format": "int64"
          },
          "account_index": {
            "type": "integer",
            "format": "int64"
          },
          "market_index": {
            "type": "integer",
            "format": "int16"
          },
          "direction": {
            "type": "integer",
            "format": "int16"
          },
          "base_amount": {
            "type": "string"
          },
          "quote_amount": {
            "type": "string"
          },
          "status": {
            "type": "string"
          },
          "metadata": {
            "$ref": "#/components/schemas/RFQMetadata"
          },
          "responses": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/RFQResponseEntry"
            }
          },
          "created_at": {
            "type": "integer",
            "format": "int64"
          },
          "updated_at": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "RFQEntry",
        "required": [
          "id",
          "account_index",
          "market_index",
          "direction",
          "base_amount",
          "quote_amount",
          "status",
          "metadata",
          "responses",
          "created_at",
          "updated_at"
        ]
      }
    }
  }
}
```