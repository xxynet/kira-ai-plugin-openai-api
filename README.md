<div align="center">

# 🔌 OpenAI API Plugin

Expose KiraAI providers as an OpenAI-compatible API endpoint for external tools.

English | [简体中文](README.zh.md)

</div>

Version **1.1.0** targets KiraAI **>=3.0.0-alpha.1,<4.0.0-alpha.1**. For KiraAI v2 (>=2.26.0), use [v1.0.2](https://github.com/xxynet/kira-ai-plugin-openai-api/releases/tag/v1.0.2).

## 📖 Overview

This plugin starts an OpenAI-compatible HTTP server within KiraAI, allowing external tools (Cursor, Continue, ChatBox, etc.) to use KiraAI's configured LLM providers through the standard OpenAI API format.

## ✨ Features

- 🔄 **Full OpenAI API compatibility** — drop-in replacement for `https://api.openai.com/v1`
- 🛠️ **Tool calls** — function calling passthrough with `tools` and `tool_choice`
- 🌊 **Streaming** — real-time token streaming via `stream: true`
- 🧠 **Reasoning models** — supports `reasoning_content` output (DeepSeek, etc.)
- 🔐 **API key authentication** — optional Bearer token protection
- 🎯 **Smart model routing** — resolve stable IDs, current upstream names, and unambiguous migrated aliases

## 🚀 Getting Started

### Enable the Plugin

1. Open KiraAI WebUI → **Plugins**
2. Find **OpenAI API** and enable it
3. Configure the plugin settings (see [Configuration](#%EF%B8%8F-configuration))

### Connect External Tools

Set the API base URL and API key in your external tool:

| Setting | Value |
|---------|-------|
| API Base URL | `http://<kiraai-host>:5267/api/plugin/openai-api` |
| API Key | The key you configured (or leave empty if none) |
| Model | A canonical ID from `GET /v1/models` (e.g. `openai:0123456789abcdef`) |

## ⚙️ Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | sensitive | *(empty)* | API key for external clients. **Required** — all requests are rejected when empty. Clients send it as `Authorization: Bearer <key>` |
| `default_model` | string | *(empty)* | Select an LLM using the v3 model picker; its stable `provider_id:model_id` reference is saved. Leave empty to use KiraAI's default LLM |

> [!WARNING]
> You must set an API key before using this endpoint. With an empty key, all requests will be rejected with a `401` error.

## 📡 API Endpoints

### List Models

```
GET /api/plugin/openai-api/v1/models
```

Returns all LLM models configured in KiraAI.

<details>
<summary>Example Response</summary>

```json
{
  "object": "list",
  "data": [
    {
      "id": "openai:0123456789abcdef",
      "model_name": "gpt-4o",
      "object": "model",
      "created": 0,
      "owned_by": "OpenAI"
    }
  ]
}
```

</details>

### Chat Completions

```
POST /api/plugin/openai-api/v1/chat/completions
```

OpenAI-compatible chat completions endpoint.

<details>
<summary>Example Request</summary>

```json
{
  "model": "openai:0123456789abcdef",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "temperature": 0.7,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string"}
          },
          "required": ["location"]
        }
      }
    }
  ]
}
```

</details>

<details>
<summary>Example Response</summary>

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "openai:0123456789abcdef",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 9,
    "total_tokens": 34
  }
}
```

</details>

## 📋 Model Routing

KiraAI v3 separates a stable internal `model_id` from the upstream `model_name`. `GET /v1/models` returns canonical `provider_id:model_id` IDs and a `model_name` field for the current upstream name. Copy an ID from that endpoint for a reference that survives model/provider display-name changes.

| Format | Example | Behavior |
|--------|---------|----------|
| `provider_id:model_id` | `openai:0123456789abcdef` | Stable, explicit LLM routing (recommended) |
| `provider_id:model_name` | `openai:gpt-4o` | Match the current upstream name within one provider |
| `provider_name/model_id` or `provider_name/model_name` | `OpenAI/gpt-4o` | Match by provider display name; it must identify one model |
| `model_id` or `model_name` | `gpt-4o` | Resolve only when exactly one active LLM matches |
| *(omit)* | — | Use the selected plugin default or KiraAI default LLM |

Unambiguous aliases preserved by KiraAI's v2 configuration migration also work, including old `provider_id:model_name` defaults. This keeps existing external clients usable while they migrate to stable references. Current upstream names containing `/` or `:` are supported; a known `provider_id:` or `provider_name/` prefix selects provider-scoped routing.

Inactive/unavailable providers and non-LLM models are excluded. Unknown or ambiguous references return `404`; specify a canonical ID to disambiguate. If a provider is disabled after resolution, non-streaming requests return `503` and streaming requests emit an error followed by `[DONE]`. Upstream requests still use the framework client's `model_name`; internal IDs are never substituted for upstream model names.

## Development validation

Run tests against a local KiraAI v3 checkout with its dependencies installed. From this plugin directory in PowerShell:

```powershell
$env:KIRAAI_SOURCE = 'D:\path\to\KiraAI'
python -m pytest tests/ -q
```

The tests exercise the actual v3 `ProviderManager` with local fake model clients; no upstream network calls are made.

## 🔗 Related

- [KiraAI Documentation](https://docs.kira-ai.top)
- [KiraAI GitHub](https://github.com/xxynet/KiraAI)
