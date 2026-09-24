---
updatedAt: 2026-07-15T18:07:14.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# partnerStats

Get partner stats. If timestamps are not provided, all-time stats will be returned.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/partnerStats": {
      "get": {
        "summary": "partnerStats",
        "operationId": "partnerStats",
        "description": "Get partner stats. If timestamps are not provided, all-time stats will be returned.",
        "tags": [
          "account"
        ],
        "parameters": [
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
            "name": "start_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "end_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "period",
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
                  "$ref": "#/components/schemas/PartnerStats"
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
      "PartnerStats": {
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
          "total_fees_earned": {
            "type": "string"
          },
          "total_taker_fees_earned": {
            "type": "string"
          },
          "total_maker_fees_earned": {
            "type": "string"
          },
          "total_volume": {
            "type": "string"
          },
          "total_taker_volume": {
            "type": "string"
          },
          "total_maker_volume": {
            "type": "string"
          },
          "total_trades": {
            "type": "integer",
            "format": "int64"
          },
          "total_taker_trades": {
            "type": "integer",
            "format": "int64"
          },
          "total_maker_trades": {
            "type": "integer",
            "format": "int64"
          },
          "unique_clients": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "PartnerStats",
        "required": [
          "code",
          "total_fees_earned",
          "total_taker_fees_earned",
          "total_maker_fees_earned",
          "total_volume",
          "total_taker_volume",
          "total_maker_volume",
          "total_trades",
          "total_taker_trades",
          "total_maker_trades",
          "unique_clients"
        ]
      }
    }
  }
}
```