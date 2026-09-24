---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# referral_use

Use a referral code. You can change this at a later time.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/referral/use": {
      "post": {
        "summary": "referral_use",
        "operationId": "referral_use",
        "tags": [
          "referral"
        ],
        "description": "Use a referral code. You can change this at a later time.",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": false,
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
                "$ref": "#/components/schemas/ReqUseReferralCode"
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
                  "$ref": "#/components/schemas/ResultCode"
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
      "ReqUseReferralCode": {
        "type": "object",
        "properties": {
          "l1_address": {
            "type": "string"
          },
          "referral_code": {
            "type": "string"
          },
          "discord": {
            "type": "string"
          },
          "telegram": {
            "type": "string"
          },
          "x": {
            "type": "string"
          },
          "signature": {
            "type": "string"
          },
          "source": {
            "type": "string"
          }
        },
        "title": "ReqUseReferralCode",
        "required": [
          "l1_address",
          "referral_code"
        ]
      }
    }
  }
}
```