---
updatedAt: 2026-07-15T17:58:36.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# rfq_respond

Respond to RFQ. Makers have up to 1 minute to answer an RFQ, after which the user has up to 2 minutes to execute the trade. Quotes are not locked. Makers place orders directly into the order book and can dynamically adjust pricing.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/rfq/respond": {
      "post": {
        "summary": "rfq_respond",
        "operationId": "rfq_respond",
        "tags": [
          "account"
        ],
        "description": "Respond to RFQ. Makers have up to 1 minute to answer an RFQ, after which the user has up to 2 minutes to execute the trade. Quotes are not locked. Makers place orders directly into the order book and can dynamically adjust pricing.",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "requestBody": {
          "required": true,
          "content": {
            "application/x-www-form-urlencoded": {
              "schema": {
                "$ref": "#/components/schemas/ReqRespondToRFQ"
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/RespRespondToRFQ"
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
      "ReqRespondToRFQ": {
        "type": "object",
        "properties": {
          "rfq_id": {
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
          }
        },
        "title": "ReqRespondToRFQ",
        "required": [
          "rfq_id",
          "status"
        ]
      },
      "RespRespondToRFQ": {
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
        "title": "RespRespondToRFQ",
        "required": [
          "code",
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
      }
    }
  }
}
```