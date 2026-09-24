---
updatedAt: 2026-09-04T12:43:29.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# export_historicalTrades

Export historical trades, updated daily at 8 PM UTC. Requires a one-time 300 USDG L2Transfer to 0x4FD058F25bE85E459ec552cA8e4C696FD1D34125. Returns a presigned S3 URL valid for 1 hour.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/export/historicalTrades": {
      "get": {
        "summary": "export_historicalTrades",
        "operationId": "export_historicalTrades",
        "tags": [
          "order"
        ],
        "description": "Export historical trades, updated daily at 8 PM UTC. Requires a one-time 300 USDG L2Transfer to 0x4FD058F25bE85E459ec552cA8e4C696FD1D34125. Returns a presigned S3 URL valid for 1 hour.",
        "parameters": [
          {
            "name": "authorization",
            "in": "query",
            "description": " make required after integ is done",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "auth",
            "in": "query",
            "description": " made optional to support header auth clients",
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
            "name": "date",
            "in": "query",
            "description": " UTC day, YYYY-MM-DD",
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
                  "$ref": "#/components/schemas/HistoricalTradesExportData"
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
      "HistoricalTradesExportData": {
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
          },
          "expires_at": {
            "type": "string",
            "example": "2025-01-02T00:00:00Z"
          }
        },
        "title": "HistoricalTradesExportData",
        "required": [
          "code",
          "data_url",
          "expires_at"
        ]
      }
    }
  }
}
```