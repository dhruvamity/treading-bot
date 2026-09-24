---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# sendTxBatch

You need to sign the transaction body before sending it to the server. More details can be found here: https://apidocs.lighter.xyz/docs/get-started

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/sendTxBatch": {
      "post": {
        "summary": "sendTxBatch",
        "operationId": "sendTxBatch",
        "tags": [
          "transaction"
        ],
        "description": "You need to sign the transaction body before sending it to the server. More details can be found here: https://apidocs.lighter.xyz/docs/get-started",
        "parameters": [],
        "requestBody": {
          "required": true,
          "content": {
            "application/x-www-form-urlencoded": {
              "schema": {
                "$ref": "#/components/schemas/ReqSendTxBatch"
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
                  "$ref": "#/components/schemas/RespSendTxBatch"
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
      "ReqSendTxBatch": {
        "type": "object",
        "properties": {
          "tx_types": {
            "type": "string"
          },
          "tx_infos": {
            "type": "string"
          }
        },
        "title": "ReqSendTxBatch",
        "required": [
          "tx_types",
          "tx_infos"
        ]
      },
      "RespSendTxBatch": {
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
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "predicted_execution_time_ms": {
            "type": "integer",
            "format": "int64",
            "example": "1751465474"
          },
          "volume_quota_remaining": {
            "type": "integer",
            "format": "int64"
          }
        },
        "title": "RespSendTxBatch",
        "required": [
          "code",
          "tx_hash",
          "predicted_execution_time_ms",
          "volume_quota_remaining"
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