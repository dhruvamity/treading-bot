---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# accountLimits

Get account limits. For more details on account types, see this page: https://apidocs.lighter.xyz/docs/account-types

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/accountLimits": {
      "get": {
        "summary": "accountLimits",
        "operationId": "accountLimits",
        "tags": [
          "account"
        ],
        "description": "Get account limits. For more details on account types, see this page: https://apidocs.lighter.xyz/docs/account-types",
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
            "name": "authorization",
            "in": "header",
            "required": true,
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
                  "$ref": "#/components/schemas/AccountLimits"
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
      "AccountLimits": {
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
          "max_llp_percentage": {
            "type": "integer",
            "format": "int32",
            "example": "25"
          },
          "user_tier": {
            "type": "string",
            "example": "std"
          },
          "can_create_public_pool": {
            "type": "boolean",
            "format": "boolean",
            "example": "true"
          },
          "max_llp_amount": {
            "type": "string",
            "example": "1000000"
          },
          "current_maker_fee_tick": {
            "type": "integer",
            "format": "int32",
            "example": "0"
          },
          "current_taker_fee_tick": {
            "type": "integer",
            "format": "int32",
            "example": "0"
          },
          "effective_lit_stakes": {
            "type": "string",
            "description": "Effective staked LIT shares including active leases."
          },
          "leased_lit": {
            "type": "string",
            "description": "Total actively leased LIT."
          },
          "user_tier_name": {
            "type": "string",
            "example": "standard"
          },
          "user_tier_last_update": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "AccountLimits",
        "required": [
          "code",
          "max_llp_percentage",
          "user_tier",
          "can_create_public_pool",
          "max_llp_amount",
          "current_maker_fee_tick",
          "current_taker_fee_tick",
          "effective_lit_stakes",
          "leased_lit",
          "user_tier_name"
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