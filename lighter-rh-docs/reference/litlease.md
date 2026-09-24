---
updatedAt: 2026-07-15T17:59:50.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# litLease

Submit a LIT lease transfer. The server calculates the required fee based on lease_amount and duration_days, then executes the transfer. Fee formula (integer arithmetic): fee = lease_amount × (annual_rate × 100) × duration_days / (360 × 10000).

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/litLease": {
      "post": {
        "summary": "litLease",
        "operationId": "litLease",
        "tags": [
          "account"
        ],
        "description": "Submit a LIT lease transfer. The server calculates the required fee based on lease_amount and duration_days, then executes the transfer. Fee formula (integer arithmetic): fee = lease_amount × (annual_rate × 100) × duration_days / (360 × 10000).",
        "parameters": [
          {
            "name": "authorization",
            "in": "header",
            "required": false,
            "schema": {
              "type": "string"
            },
            "description": "API token authorization"
          }
        ],
        "requestBody": {
          "required": true,
          "content": {
            "application/x-www-form-urlencoded": {
              "schema": {
                "$ref": "#/components/schemas/ReqLITLease"
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
                  "$ref": "#/components/schemas/TxHash"
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
      "TxHash": {
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
          "tx_hash": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          }
        },
        "title": "TxHash",
        "required": [
          "code",
          "tx_hash"
        ]
      },
      "ReqLITLease": {
        "type": "object",
        "properties": {
          "tx_info": {
            "type": "string",
            "description": "Signed transaction info (JSON with L2 signature, L1 signature, etc.)"
          },
          "lease_amount": {
            "type": "string",
            "description": "Amount of LIT to lease in raw units (1 LIT = 100000000)"
          },
          "duration_days": {
            "type": "integer",
            "description": "Lease duration in days. Must match one of the available lease options.",
            "format": "int32"
          }
        },
        "required": [
          "tx_info",
          "lease_amount",
          "duration_days"
        ]
      }
    }
  }
}
```