---
updatedAt: 2026-07-15T18:00:33.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# userReferrals

Get user referrals

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/referral/userReferrals": {
      "get": {
        "summary": "userReferrals",
        "operationId": "referral_userReferrals",
        "tags": [
          "account"
        ],
        "description": "Get user referrals",
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
            "name": "l1_address",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
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
            "name": "stats_start_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "stats_end_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64"
            }
          },
          {
            "name": "limit",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 1,
              "maximum": 300
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
                  "$ref": "#/components/schemas/UserReferrals"
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
      "Referral": {
        "type": "object",
        "properties": {
          "l1_address": {
            "type": "string"
          },
          "referral_code": {
            "type": "string"
          },
          "used_at": {
            "type": "integer",
            "format": "int64"
          },
          "trade_stats": {
            "$ref": "#/components/schemas/TradeStats"
          },
          "tier": {
            "type": "string"
          },
          "source": {
            "type": "string"
          }
        },
        "required": [
          "l1_address",
          "referral_code",
          "used_at",
          "tier",
          "source",
          "trade_stats"
        ]
      },
      "UserReferrals": {
        "type": "object",
        "properties": {
          "code": {
            "type": "integer",
            "format": "int32"
          },
          "message": {
            "type": "string"
          },
          "cursor": {
            "type": "string"
          },
          "referrals": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/Referral"
            }
          },
          "used_code": {
            "type": "string"
          },
          "totals": {
            "$ref": "#/components/schemas/ReferralTotals"
          }
        },
        "required": [
          "code",
          "cursor",
          "used_code",
          "totals",
          "referrals"
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
      "ReferralTotals": {
        "type": "object",
        "properties": {
          "count": {
            "type": "integer",
            "format": "int64"
          },
          "volume": {
            "type": "string"
          },
          "free_maker_volume": {
            "type": "string"
          },
          "free_taker_volume": {
            "type": "string"
          },
          "non_free_maker_volume": {
            "type": "string"
          },
          "non_free_taker_volume": {
            "type": "string"
          },
          "maker_fees_paid": {
            "type": "string"
          },
          "taker_fees_paid": {
            "type": "string"
          },
          "total_referrals": {
            "type": "integer",
            "format": "int64"
          },
          "total_premium_referrals": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "ReferralTotals",
        "required": [
          "count",
          "volume",
          "free_maker_volume",
          "free_taker_volume",
          "non_free_maker_volume",
          "non_free_taker_volume",
          "maker_fees_paid",
          "taker_fees_paid",
          "total_referrals",
          "total_premium_referrals"
        ]
      }
    }
  }
}
```