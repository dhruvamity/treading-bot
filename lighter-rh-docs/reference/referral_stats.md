---
updatedAt: 2026-08-25T18:06:19.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# referral_stats

Get trade stats summed across a referrer's referred users

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/referral/stats": {
      "get": {
        "summary": "referral_stats",
        "operationId": "referral_stats",
        "tags": [
          "referral"
        ],
        "description": "Get trade stats summed across a referrer's referred users",
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
            "name": "auth",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "l1_address",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "is_eligible",
            "in": "query",
            "required": false,
            "schema": {
              "type": "boolean",
              "format": "boolean",
              "default": "false"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ReferralStats"
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
      "TradeStats": {
        "type": "object",
        "properties": {
          "count": {
            "type": "integer",
            "format": "int64"
          },
          "volume": {
            "type": "string"
          },
          "web_count": {
            "type": "integer",
            "format": "int64"
          },
          "web_volume": {
            "type": "string"
          },
          "mobile_app_count": {
            "type": "integer",
            "format": "int64"
          },
          "mobile_app_volume": {
            "type": "string"
          },
          "mobile_browser_count": {
            "type": "integer",
            "format": "int64"
          },
          "mobile_browser_volume": {
            "type": "string"
          },
          "maker_fees_paid": {
            "type": "string"
          },
          "non_free_taker_volume": {
            "type": "string"
          },
          "non_free_maker_volume": {
            "type": "string"
          },
          "free_taker_volume": {
            "type": "string"
          },
          "taker_fees_paid": {
            "type": "string"
          },
          "free_maker_volume": {
            "type": "string"
          }
        },
        "title": "TradeStats",
        "required": [
          "count",
          "volume",
          "web_count",
          "web_volume",
          "mobile_app_count",
          "mobile_app_volume",
          "mobile_browser_count",
          "mobile_browser_volume",
          "free_maker_volume",
          "free_taker_volume",
          "non_free_maker_volume",
          "non_free_taker_volume",
          "maker_fees_paid",
          "taker_fees_paid"
        ]
      },
      "ReferralStats": {
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
          "stats": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ReferralStat"
            }
          }
        },
        "title": "ReferralStats",
        "required": [
          "code",
          "stats"
        ]
      },
      "ReferralStat": {
        "type": "object",
        "properties": {
          "start_timestamp": {
            "type": "integer",
            "format": "int64"
          },
          "end_timestamp": {
            "type": "integer",
            "format": "int64"
          },
          "trade_stats": {
            "$ref": "#/components/schemas/TradeStats"
          }
        },
        "title": "ReferralStat",
        "required": [
          "start_timestamp",
          "end_timestamp",
          "trade_stats"
        ]
      }
    }
  }
}
```