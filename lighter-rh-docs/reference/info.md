---
updatedAt: 2026-07-01T15:42:52.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# info

Get info of zklighter

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/info": {
      "get": {
        "summary": "info",
        "operationId": "info",
        "tags": [
          "root"
        ],
        "description": "Get info of zklighter",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ZkLighterInfo"
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
      "ZkLighterInfo": {
        "type": "object",
        "properties": {
          "contract_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          }
        },
        "title": "ZkLighterInfo",
        "required": [
          "contract_address"
        ]
      }
    }
  }
}
```