<div align="center">

# 🔌 OpenAI API Plugin

Expose KiraAI providers as an OpenAI-compatible API endpoint for external tools.

English | [简体中文](README.zh.md)

</div>

Version **1.0.2** supports KiraAI **>=2.26.0,<3.0.0-alpha.1**. KiraAI v3 changes model identity and routing; use a v3-compatible plugin version on v3.

## 📖 Overview

This plugin starts an OpenAI-compatible HTTP server within KiraAI, allowing external tools (Cursor, Continue, ChatBox, etc.) to use KiraAI's configured LLM providers through the standard OpenAI API format.

## ✨ Features

- 🔄 **Full OpenAI API compatibility** — drop-in replacement for `https://api.openai.com/v1`
- 🛠️ **Tool calls** — function calling passthrough with `tools` and `tool_choice`
- 🌊 **Streaming** — real-time token streaming via `stream: true`
- 🧠 **Reasoning models** — supports `reasoning_content` output (DeepSeek, etc.)
- 🔐 **API key authentication** — optional Bearer token protection
- 🎯 **Smart model routing** — resolve models by `provider_id:model_id`, `provider_name/model_id`, or plain `model_id`

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
| Model | `provider_name/model_id` (e.g. `OpenAI/gpt-4o`) |

## ⚙️ Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | sensitive | *(empty)* | API key for external clients. **Required** — all requests are rejected when empty. Clients send it as `Authorization: Bearer <key>` |
| `default_model` | string | *(empty)* | Default model when the request omits the `model` field. Format: `provider_id:model_id`. Leave empty to use KiraAI's default LLM |

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
      "id": "OpenAI/gpt-4o",
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
  "model": "OpenAI/gpt-4o",
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
  "model": "OpenAI:gpt-4o",
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

The `model` field supports three formats:

| Format | Example | Behavior |
|--------|---------|----------|
| `provider_id:model_id` | `openai:gpt-4o` | Explicit provider routing |
| `provider_name/model_id` | `OpenAI/gpt-4o` | Match by display name |
| `model_id` | `gpt-4o` | Auto-discover across all providers |
| *(omit)* | — | Use plugin default or KiraAI default LLM |

> [!TIP]
> Use `GET /v1/models` to see all available models with their `provider_name/model_id` identifiers.

## 🔗 Related

- [KiraAI Documentation](https://docs.kira-ai.top)
- [KiraAI GitHub](https://github.com/xxynet/KiraAI)
