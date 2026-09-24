---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# referral_points

Get referral points

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/referral/points": {
      "get": {
        "summary": "referral_points",
        "operationId": "referral_points",
        "tags": [
          "referral"
        ],
        "description": "Get referral points",
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
            "required": true,
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
                  "$ref": "#/components/schemas/ReferralPoints"
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
      "ReferralPointEntry": {
        "type": "object",
        "properties": {
          "l1_address": {
            "type": "string"
          },
          "total_points": {
            "type": "number",
            "format": "float",
            "example": "1000.01"
          },
          "week_points": {
            "type": "number",
            "format": "float",
            "example": "1000.01"
          },
          "total_reward_points": {
            "type": "number",
            "format": "float",
            "example": "200"
          },
          "week_reward_points": {
            "type": "number",
            "format": "float",
            "example": "200"
          },
          "reward_point_multiplier": {
            "type": "string",
            "example": "0.1"
          }
        },
        "title": "ReferralPointEntry",
        "required": [
          "l1_address",
          "total_points",
          "week_points",
          "total_reward_points",
          "week_reward_points",
          "reward_point_multiplier"
        ]
      },
      "ReferralPoints": {
        "type": "object",
        "properties": {
          "referrals": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ReferralPointEntry"
            }
          },
          "user_total_points": {
            "type": "number",
            "format": "float",
            "example": "1000"
          },
          "user_last_week_points": {
            "type": "number",
            "format": "float",
            "example": "1000"
          },
          "user_total_referral_reward_points": {
            "type": "number",
            "format": "float",
            "example": "1000"
          },
          "user_last_week_referral_reward_points": {
            "type": "number",
            "format": "float",
            "example": "1000"
          },
          "reward_point_multiplier": {
            "type": "string",
            "example": "0.1"
          }
        },
        "title": "ReferralPoints",
        "required": [
          "referrals",
          "user_total_points",
          "user_last_week_points",
          "user_total_referral_reward_points",
          "user_last_week_referral_reward_points",
          "reward_point_multiplier"
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