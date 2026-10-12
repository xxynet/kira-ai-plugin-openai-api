<div align="center">

# 🔌 OpenAI API 插件

将 KiraAI 已配置的提供商暴露为 OpenAI 兼容 API 端点，供外部工具调用。

[English](README.md) | 简体中文

</div>

插件 **1.1.0** 适配 KiraAI **>=3.0.0-alpha.1,<4.0.0-alpha.1**。KiraAI v2（>=2.26.0）请使用 [v1.0.2](https://github.com/xxynet/kira-ai-plugin-openai-api/releases/tag/v1.0.2)。

## 📖 简介

本插件在 KiraAI 内部启动一个 OpenAI 兼容的 HTTP 服务，允许外部工具（Cursor、Continue、ChatBox 等）通过标准 OpenAI API 格式使用 KiraAI 已配置的 LLM 提供商。

## ✨ 功能特性

- 🔄 **完全兼容 OpenAI API** — 可作为 `https://api.openai.com/v1` 的直接替代
- 🛠️ **工具调用** — 支持 `tools` 和 `tool_choice` 的 function calling 透传
- 🌊 **流式输出** — 通过 `stream: true` 实现实时 token 流式传输
- 🧠 **推理模型** — 支持 `reasoning_content` 输出（DeepSeek 等）
- 🔐 **API 密钥认证** — 可选的 Bearer Token 保护
- 🎯 **智能模型路由** — 支持稳定内部 ID、当前上游模型名和迁移后保留的无歧义别名

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
| Model | `GET /v1/models` 返回的规范 ID（如 `openai:0123456789abcdef`） |

## ⚙️ 配置项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | sensitive | *（空）* | 外部客户端使用的 API 密钥。**必填**，留空时所有请求将被拒绝。客户端通过 `Authorization: Bearer <key>` 发送 |
| `default_model` | string | *（空）* | 通过 v3 模型选择器选定 LLM，保存稳定的 `provider_id:model_id` 引用。留空则使用 KiraAI 的默认 LLM |

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

### 聊天补全

```
POST /api/plugin/openai-api/v1/chat/completions
```

兼容 OpenAI 的聊天补全接口。

<details>
<summary>请求示例</summary>

```json
{
  "model": "openai:0123456789abcdef",
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
  "model": "openai:0123456789abcdef",
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

KiraAI v3 将稳定的内部 `model_id` 与上游 `model_name` 分开。`GET /v1/models` 返回规范的 `provider_id:model_id` 标识，并通过 `model_name` 字段提供当前上游名称。建议直接复制该接口返回的 ID，避免修改模型名称或提供商显示名称后失效。

| 格式 | 示例 | 行为 |
|------|------|------|
| `provider_id:model_id` | `openai:0123456789abcdef` | 稳定的显式 LLM 路由（推荐） |
| `provider_id:model_name` | `openai:gpt-4o` | 在指定提供商内匹配当前上游名称 |
| `provider_name/model_id` 或 `provider_name/model_name` | `OpenAI/gpt-4o` | 按提供商显示名称匹配，必须唯一指向一个模型 |
| `model_id` 或 `model_name` | `gpt-4o` | 仅在恰好匹配一个已启用的 LLM 时解析 |
| *（省略）* | — | 使用插件选定的默认模型或 KiraAI 默认 LLM |

同时兼容框架迁移 v2 配置时保留的无歧义别名，包括旧 `provider_id:model_name` 默认配置，使现有外部客户端可以逐步改用稳定引用。上游模型名中可以包含 `/` 或 `:`；已知的 `provider_id:` 或 `provider_name/` 前缀表示按提供商限定路由。

停用或不可用的提供商以及非 LLM 模型不会出现在列表中。不存在或有歧义的引用返回 `404`，请用规范 ID 消除歧义。提供商在解析后被停用时，非流式请求返回 `503`，流式请求输出错误事件后以 `[DONE]` 结束。上游请求仍由框架客户端使用 `model_name`，不会将内部 ID 当成上游名称。

## 开发验证

使用已安装依赖的本地 KiraAI v3 仓库运行测试。在本插件目录中执行 PowerShell：

```powershell
$env:KIRAAI_SOURCE = 'D:\path\to\KiraAI'
python -m pytest tests/ -q
```

测试使用真实 v3 `ProviderManager` 和本地模拟模型客户端，不发出上游网络请求。

## 🔗 相关链接

- [KiraAI 文档](https://docs.kira-ai.top/zh/)
- [KiraAI GitHub](https://github.com/xxynet/KiraAI)
