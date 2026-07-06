import json
import time
import uuid
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APIStatusError, APITimeoutError, APIConnectionError

from core.plugin import BasePlugin, logger, register
from core.provider import LLMRequest, LLMStreamChunk
from core.agent.message import OpenAIMessage


class OpenAIAPIPlugin(BasePlugin):
    """Expose KiraAI providers as an OpenAI-compatible API endpoint."""

    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)
        self._api_key: str = cfg.get("api_key", "").strip()
        self._default_model: str = cfg.get("default_model", "").strip()
        self._enable_request_log: bool = cfg.get("enable_request_log", True)

    async def initialize(self):
        if self._api_key:
            masked = self._api_key[:4] + "***" if len(self._api_key) > 4 else "***"
            logger.info(f"[openai-api] plugin loaded | api_key={masked} | default_model={self._default_model or '(kira default)'}")
        else:
            logger.warning("[openai-api] plugin loaded | api_key is EMPTY — all requests will be rejected (401). Set an API key in plugin settings.")

    async def terminate(self):
        logger.info("[openai-api] plugin unloaded")

    # ---- Auth helper ----

    def _check_api_key(self, request: Request) -> Optional[JSONResponse]:
        """Validate the API key from the Authorization header. Returns None if OK."""
        if not self._api_key:
            return self._error_response(
                "API key not configured. Set an API key in plugin settings before using this endpoint.",
                "authentication_error", 401,
            )
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            if token == self._api_key:
                return None
        return self._error_response("Invalid API key", "authentication_error", 401)

    @staticmethod
    def _error_response(message: str, error_type: str = "invalid_request_error", status: int = 400) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"message": message, "type": error_type, "code": None}},
        )

    # ---- Model resolution ----

    def _resolve_model(self, model: Optional[str]):
        """
        Resolve a model string to (provider_id, model_id, LLMModelClient).
        Supported formats:
          - 'provider_id:model_id'  — explicit provider routing
          - 'provider_name/model_id' — used when same model exists in multiple providers
          - 'model_id'              — auto-discover across all providers
          - None                    — use plugin default or KiraAI default LLM
        """
        provider_mgr = self.ctx.provider_mgr
        providers_config = self.ctx.config.get("providers", {})

        provider_id = None
        model_id = None

        if model and ":" in model:
            # Format: provider_id:model_id
            parts = model.split(":", 1)
            provider_id, model_id = parts[0], parts[1]
        elif model and "/" in model:
            # Format: provider_name/model_id (from /v1/models disambiguation)
            parts = model.split("/", 1)
            provider_name_hint, model_id = parts[0], parts[1]
            for pid, pcfg in providers_config.items():
                if pcfg.get("name", pid) == provider_name_hint:
                    provider_id = pid
                    break
            if provider_id is None:
                return None, None, None
        elif model:
            # Plain model_id — auto-discover across all providers
            for pid, pcfg in providers_config.items():
                model_config_root = pcfg.get("model_config") or {}
                llm_models = model_config_root.get("llm") or {}
                if model in llm_models:
                    provider_id, model_id = pid, model
                    break
            else:
                return None, None, None
        else:
            # No model specified — use plugin default or KiraAI default
            if self._default_model and ":" in self._default_model:
                parts = self._default_model.split(":", 1)
                provider_id, model_id = parts[0], parts[1]
            else:
                try:
                    client = provider_mgr.get_default_llm()
                    return client.model.provider_id, client.model.model_id, client
                except Exception:
                    return None, None, None

        try:
            client = provider_mgr.get_model_client(provider_id, model_id)
            if client is None:
                return None, None, None
            return provider_id, model_id, client
        except Exception:
            return None, None, None

    # ---- Endpoints ----

    @register.api(method="GET", path="/v1/models", auth=False)
    async def list_models(self, request: Request):
        """GET /api/plugin/openai-api/v1/models — List available LLM models."""
        err = self._check_api_key(request)
        if err:
            return err

        providers_config = self.ctx.config.get("providers", {})

        # Collect all LLM models, detect name conflicts across providers
        all_models: list[tuple[str, str, str]] = []  # (provider_id, provider_name, model_id)
        for provider_id, pcfg in providers_config.items():
            provider_name = pcfg.get("name", provider_id)
            model_config_root = pcfg.get("model_config") or {}
            llm_models = model_config_root.get("llm") or {}
            for model_id in llm_models:
                all_models.append((provider_id, provider_name, model_id))

        data = []
        for provider_id, provider_name, model_id in all_models:
            data.append({
                "id": f"{provider_name}/{model_id}",
                "object": "model",
                "created": 0,
                "owned_by": provider_name,
            })

        return {"object": "list", "data": data}

    @register.api(method="POST", path="/v1/chat/completions", auth=False)
    async def chat_completions(self, request: Request):
        """POST /api/plugin/openai-api/v1/chat/completions — OpenAI-compatible chat completions."""
        err = self._check_api_key(request)
        if err:
            return err

        # Parse request body
        try:
            body = await request.json()
        except Exception:
            return self._error_response("Invalid JSON body")

        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return self._error_response("'messages' is required and must be a non-empty array")

        model_str = body.get("model")
        stream = body.get("stream", False)
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens")
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")

        # Resolve model
        provider_id, model_id, client = self._resolve_model(model_str)
        if client is None:
            return self._error_response(
                f"Model '{model_str}' not found. Use format 'provider_id:model_id' or configure a default model.",
                status=404,
            )

        # Build OpenAI message objects
        try:
            oai_messages = []
            for m in messages:
                oai_messages.append(OpenAIMessage(
                    role=m.get("role", "user"),
                    content=m.get("content"),
                    tool_calls=m.get("tool_calls"),
                    tool_call_id=m.get("tool_call_id"),
                    name=m.get("name"),
                ))
        except Exception as e:
            return self._error_response(f"Invalid message format: {e}")

        # Build tool_choice
        tc = "auto" if tools else "none"
        if tool_choice:
            if isinstance(tool_choice, str):
                tc = tool_choice
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                tc = tool_choice

        req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if self._enable_request_log:
            provider_name = getattr(client.model, "provider_name", provider_id)
            log_parts = [f"model={provider_name}/{model_id}", f"stream={stream}"]
            if tools:
                log_parts.append(f"tools={len(tools)}")
            if temperature is not None:
                log_parts.append(f"temp={temperature}")
            logger.info(f"[openai-api] {req_id} | {'  '.join(log_parts)}")

        if stream:
            return await self._handle_streaming(
                req_id, provider_id, model_id, client, oai_messages,
                tools=tools, tool_choice=tc,
                temperature=temperature, max_tokens=max_tokens,
            )
        else:
            return await self._handle_non_streaming(
                req_id, provider_id, model_id, client, oai_messages,
                tools=tools, tool_choice=tc,
                temperature=temperature, max_tokens=max_tokens,
            )

    # ---- Non-streaming ----

    async def _handle_non_streaming(
        self, req_id: str, provider_id: str, model_id: str, client,
        messages: list, tools=None, tool_choice="none",
        temperature=None, max_tokens=None,
    ) -> JSONResponse:
        request = LLMRequest(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice if tool_choice != "none" else "none",
        )

        # Build overrides from client-provided parameters
        overrides = {}
        if temperature is not None:
            overrides["temperature"] = temperature
        if max_tokens is not None:
            overrides["max_tokens"] = max_tokens

        try:
            response = await client.chat(request, **overrides)
        except APIStatusError as e:
            return self._error_response(f"Provider error: {e.message}", "api_error", e.status_code)
        except (APITimeoutError, APIConnectionError) as e:
            return self._error_response(f"Provider connection error: {e}", "api_error", 502)
        except Exception as e:
            return self._error_response(f"Internal error: {e}", "api_error", 500)

        # Build OpenAI-format response
        message = {"role": "assistant", "content": response.text_response or ""}
        if response.tool_calls:
            message["tool_calls"] = response.tool_calls
        if response.reasoning_content:
            message["reasoning_content"] = response.reasoning_content

        usage = {
            "prompt_tokens": response.input_tokens or 0,
            "completion_tokens": response.output_tokens or 0,
            "total_tokens": (response.input_tokens or 0) + (response.output_tokens or 0),
        }

        result = {
            "id": req_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"{provider_id}:{model_id}",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if response.tool_calls else "stop",
                }
            ],
            "usage": usage,
        }

        return JSONResponse(content=result)

    # ---- Streaming ----

    async def _handle_streaming(
        self, req_id: str, provider_id: str, model_id: str, client,
        messages: list, tools=None, tool_choice="none",
        temperature=None, max_tokens=None,
    ) -> StreamingResponse:
        """Stream chat completions via the provider's chat_stream()."""
        request = LLMRequest(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice if tool_choice != "none" else "none",
        )

        # Build overrides from client-provided parameters
        overrides = {}
        if temperature is not None:
            overrides["temperature"] = temperature
        if max_tokens is not None:
            overrides["max_tokens"] = max_tokens

        created = int(time.time())
        model_label = f"{provider_id}:{model_id}"

        async def event_generator():
            try:
                async for chunk in client.chat_stream(request, **overrides):
                    # Usage-only chunk: no choice delta, just usage data
                    if chunk.usage and not chunk.finish_reason:
                        chunk_dict = {
                            "id": req_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_label,
                            "choices": [],
                            "usage": {
                                "prompt_tokens": chunk.usage.get("input_tokens", 0),
                                "completion_tokens": chunk.usage.get("output_tokens", 0),
                                "total_tokens": (chunk.usage.get("input_tokens", 0) or 0)
                                                + (chunk.usage.get("output_tokens", 0) or 0),
                            },
                        }
                        yield self._sse_chunk(chunk_dict)
                        continue

                    # Build OpenAI-format delta
                    delta: dict = {"role": "assistant"}
                    if chunk.delta_text:
                        delta["content"] = chunk.delta_text
                    if chunk.delta_reasoning:
                        delta["reasoning_content"] = chunk.delta_reasoning
                    if chunk.tool_calls_delta:
                        delta["tool_calls"] = chunk.tool_calls_delta

                    choice = {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": chunk.finish_reason if chunk.is_final else None,
                    }

                    chunk_dict = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_label,
                        "choices": [choice],
                    }

                    yield self._sse_chunk(chunk_dict)
            except APIStatusError as e:
                yield self._sse_chunk({"error": {"message": f"Provider error: {e.message}", "type": "api_error"}})
            except (APITimeoutError, APIConnectionError) as e:
                yield self._sse_chunk({"error": {"message": f"Provider connection error: {e}", "type": "api_error"}})
            except Exception as e:
                yield self._sse_chunk({"error": {"message": f"Internal error: {e}", "type": "api_error"}})
            finally:
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @staticmethod
    def _sse_chunk(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
