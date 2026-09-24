---
updatedAt: 2026-07-01T15:22:51.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# export

Export trades and funding payments, limited to 12 months or 1M trades. END_TS_IN_MS - START_TS_IN_MS should not be larger than 12 months in milliseconds, both timestamps should be greater than or equal to 17 January 2025 00:00:00 UTC (lighter's mainnet genesis)

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/export": {
      "get": {
        "summary": "export",
        "operationId": "export",
        "tags": [
          "order"
        ],
        "description": "Export trades and funding payments, limited to 12 months or 1M trades. END_TS_IN_MS - START_TS_IN_MS should not be larger than 12 months in milliseconds, both timestamps should be greater than or equal to 17 January 2025 00:00:00 UTC (lighter's mainnet genesis)",
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
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "default": "-1"
            }
          },
          {
            "name": "market_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int16",
              "default": "255"
            }
          },
          {
            "name": "type",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string",
              "enum": [
                "funding",
                "trade"
              ]
            }
          },
          {
            "name": "start_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 1735689600000,
              "maximum": 1830297600000
            }
          },
          {
            "name": "end_timestamp",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "format": "int64",
              "minimum": 1735689600000,
              "maximum": 1830297600000
            }
          },
          {
            "name": "side",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "all",
                "long",
                "short"
              ],
              "default": "all"
            }
          },
          {
            "name": "role",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "all",
                "maker",
                "taker"
              ],
              "default": "all"
            }
          },
          {
            "name": "trade_type",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "all",
                "trade",
                "liquidation",
                "deleverage",
                "market-settlement"
              ],
              "default": "all"
            }
          },
          {
            "name": "aggregate",
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
                  "$ref": "#/components/schemas/ExportData"
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
      "ExportData": {
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
          "data_url": {
            "type": "string"
          }
        },
        "title": "ExportData",
        "required": [
          "code",
          "data_url"
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