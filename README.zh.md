<div align="center">

# 🔌 OpenAI API 插件

将 KiraAI 已配置的提供商暴露为 OpenAI 兼容 API 端点，供外部工具调用。

[English](README.md) | 简体中文

</div>

插件 **1.0.2** 支持 KiraAI **>=2.26.0,<3.0.0-alpha.1**。KiraAI v3 调整了模型身份与路由，v3 框架请使用适配 v3 的插件版本。

## 📖 简介

本插件在 KiraAI 内部启动一个 OpenAI 兼容的 HTTP 服务，允许外部工具（Cursor、Continue、ChatBox 等）通过标准 OpenAI API 格式使用 KiraAI 已配置的 LLM 提供商。

## ✨ 功能特性

- 🔄 **完全兼容 OpenAI API** — 可作为 `https://api.openai.com/v1` 的直接替代
- 🛠️ **工具调用** — 支持 `tools` 和 `tool_choice` 的 function calling 透传
- 🌊 **流式输出** — 通过 `stream: true` 实现实时 token 流式传输
- 🧠 **推理模型** — 支持 `reasoning_content` 输出（DeepSeek 等）
- 🔐 **API 密钥认证** — 可选的 Bearer Token 保护
- 🎯 **智能模型路由** — 支持 `provider_id:model_id`、`provider_name/model_id`、纯 `model_id` 三种格式

## 🚀 快速开始

### 启用插件

1. 打开 KiraAI WebUI → **插件**
2. 找到 **OpenAI API** 并启用
3. 配置插件参数（参见 [配置项](#%EF%B8%8F-配置项)）

### 连接外部工具

在外部工具中设置 API 地址和密钥：

| 配置项 | 值 |
|--------|-----|
| API Base URL | `http://<kiraai-host>:5267/api/plugin/openai-api` |
| API Key | 你配置的密钥（如未配置则留空） |
| Model | `provider_name/model_id`（如 `OpenAI/gpt-4o`） |

## ⚙️ 配置项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | sensitive | *（空）* | 外部客户端使用的 API 密钥。**必填**，留空时所有请求将被拒绝。客户端通过 `Authorization: Bearer <key>` 发送 |
| `default_model` | string | *（空）* | 请求中未指定 `model` 时使用的默认模型。格式：`provider_id:model_id`。留空则使用 KiraAI 的默认 LLM |

> [!WARNING]
> 使用本端点前必须设置 API 密钥。密钥为空时，所有请求将返回 `401` 错误。

## 📡 API 端点

### 获取模型列表

```
GET /api/plugin/openai-api/v1/models
```

返回 KiraAI 中已配置的所有 LLM 模型。

<details>
<summary>响应示例</summary>

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

### 聊天补全

```
POST /api/plugin/openai-api/v1/chat/completions
```

兼容 OpenAI 的聊天补全接口。

<details>
<summary>请求示例</summary>

```json
{
  "model": "OpenAI/gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "你好！"}
  ],
  "stream": false,
  "temperature": 0.7,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取当前天气",
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
<summary>响应示例</summary>

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
        "content": "你好！有什么我可以帮你的吗？"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 15,
    "total_tokens": 40
  }
}
```

</details>

## 📋 模型路由

`model` 字段支持三种格式：

| 格式 | 示例 | 行为 |
|------|------|------|
| `provider_id:model_id` | `openai:gpt-4o` | 显式指定提供商 |
| `provider_name/model_id` | `OpenAI/gpt-4o` | 按显示名称匹配 |
| `model_id` | `gpt-4o` | 在所有提供商中自动查找 |
| *（省略）* | — | 使用插件默认值或 KiraAI 默认 LLM |

> [!TIP]
> 使用 `GET /v1/models` 查看所有可用模型及其 `provider_name/model_id` 标识符。

## 🔗 相关链接

- [KiraAI 文档](https://docs.kira-ai.top/zh/)
- [KiraAI GitHub](https://github.com/xxynet/KiraAI)
