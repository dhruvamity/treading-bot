---
updatedAt: 2026-07-15T17:56:31.000Z
---

Fetch the complete documentation index at: https://apidocs.rh.lighter.xyz/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# layer1BasicInfo

Get zklighter l1 general info, including contract address and rpc info

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "",
    "version": ""
  },
  "paths": {
    "/api/v1/layer1BasicInfo": {
      "get": {
        "summary": "layer1BasicInfo",
        "operationId": "layer1BasicInfo",
        "tags": [
          "info"
        ],
        "description": "Get zklighter l1 general info, including contract address and rpc info",
        "responses": {
          "200": {
            "description": "A successful response.",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/Layer1BasicInfo"
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
      "ContractAddress": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string",
            "example": "1"
          },
          "address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          }
        },
        "title": "ContractAddress",
        "required": [
          "name",
          "address"
        ]
      },
      "L1ProviderInfo": {
        "type": "object",
        "properties": {
          "chainId": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "networkId": {
            "type": "integer",
            "format": "int64",
            "example": "1"
          },
          "latestBlockNumber": {
            "type": "integer",
            "format": "int64",
            "example": "45434"
          }
        },
        "title": "L1ProviderInfo",
        "required": [
          "chainId",
          "networkId",
          "latestBlockNumber"
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
      },
      "ValidatorInfo": {
        "type": "object",
        "properties": {
          "address": {
            "type": "string",
            "example": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
          },
          "is_active": {
            "type": "boolean",
            "format": "boolean",
            "example": "true"
          }
        },
        "title": "ValidatorInfo",
        "required": [
          "address",
          "is_active"
        ]
      },
      "Layer1BasicInfo": {
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
          "l1_providers": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/L1ProviderInfo"
            }
          },
          "l1_providers_health": {
            "type": "boolean",
            "format": "boolean",
            "example": "true"
          },
          "validator_info": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ValidatorInfo"
            }
          },
          "contract_addresses": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/ContractAddress"
            }
          },
          "latest_l1_generic_block": {
            "type": "integer",
            "format": "int64",
            "example": "45434"
          },
          "latest_l1_governance_block": {
            "type": "integer",
            "format": "int64",
            "example": "45434"
          },
          "latest_l1_desert_block": {
            "type": "integer",
            "format": "int64",
            "example": "45434"
          }
        },
        "title": "Layer1BasicInfo",
        "required": [
          "code",
          "l1_providers",
          "l1_providers_health",
          "validator_info",
          "contract_addresses",
          "latest_l1_generic_block",
          "latest_l1_governance_block",
          "latest_l1_desert_block"
        ]
      }
    }
  }
}
```