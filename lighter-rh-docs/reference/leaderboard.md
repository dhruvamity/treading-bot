---
updatedAt: 2026-08-13T17:24:50.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# leaderboard

Get points leaderboard

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/leaderboard": {
      "get": {
        "summary": "leaderboard",
        "operationId": "leaderboard",
        "tags": [
          "account"
        ],
        "description": "Get points leaderboard",
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
            "name": "type",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "weekly",
                "all",
                "competition"
              ]
            }
          },
          {
            "name": "l1_address",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "competition_id",
            "in": "query",
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
          }
        ],
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Leaderboard"
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
      "Leaderboard": {
        "type": "object",
        "properties": {
          "entries": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/LeaderboardEntry"
            }
          }
        },
        "title": "Leaderboard",
        "required": [
          "entries"
        ]
      },
      "LeaderboardEntry": {
        "type": "object",
        "properties": {
          "l1_address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "points": {
            "type": "number",
            "format": "float",
            "example": "1000.01"
          },
          "entry": {
            "type": "integer",
            "format": "int32"
          },
          "entryId": {
            "type": "integer",
            "format": "int32"
          },
          "metadata": {
            "type": "string",
            "example": "{}"
          }
        },
        "title": "LeaderboardEntry",
        "required": [
          "l1_address",
          "points",
          "entry",
          "entryId",
          "metadata"
        ]
      }
    }
  }
}
```