import json
import time
import uuid
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APIStatusError, APITimeoutError, APIConnectionError

from core.plugin import BasePlugin, logger, register
from core.provider import LLMRequest, LLMStreamChunk, LLMModelClient, ModelInfo, ModelType, ProviderAPIError
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

    def _get_llm_models(self) -> list[ModelInfo]:
        """Read active, available LLMs through the v3 provider manager."""
        manager = self.ctx.provider_mgr
        return [
            info
            for provider_id, provider in manager.get_all_providers().items()
            if manager.get_provider(provider_id) is not None and ModelType.LLM in provider.models
            for info in manager.get_model_infos(provider_id)
            if info.model_type == ModelType.LLM
        ]

    def _resolve_model(self, model: Optional[str]):
        """Resolve stable IDs, upstream names, or legacy aliases to an LLM."""
        manager = self.ctx.provider_mgr
        if model is None or model == "":
            model = self._default_model
            if not model:
                try:
                    client = manager.get_default_llm()
                    return client.model.provider_id, client.model.model_id, client
                except Exception:
                    return None, None, None
        if not isinstance(model, str):
            return None, None, None

        models = self._get_llm_models()
        providers = self.ctx.config.get("providers", {})
        provider_ids = {info.provider_id for info in models}
        candidates = {(provider_id, model) for provider_id in provider_ids}
        prefix, separator, identifier = model.partition(":")
        if separator and prefix in providers:
            candidates = {(prefix, identifier)}
        else:
            # Match known provider prefixes without splitting slashes in upstream names.
            qualified = {
                (provider_id, model[len(provider.get("name", provider_id)) + 1:])
                for provider_id, provider in providers.items()
                if model.startswith(f"{provider.get('name', provider_id)}/")
            }
            if qualified:
                candidates = qualified

        available = {(info.provider_id, info.model_id) for info in models}
        matches = {}
        for provider_id, identifier in candidates:
            info = manager.get_model_info(provider_id, identifier, ModelType.LLM)
            if info is not None and (provider_id, info.model_id) in available:
                matches[(provider_id, info.model_id)] = info
                if identifier == info.model_id:
                    continue
            for info in models:
                if info.provider_id == provider_id and info.model_name == identifier:
                    matches[(provider_id, info.model_id)] = info
        # Never silently route an ambiguous name to whichever provider appears first.
        if len(matches) != 1:
            return None, None, None
        info = next(iter(matches.values()))
        try:
            client = manager.get_model_client(info.provider_id, info.model_id, ModelType.LLM)
            if not isinstance(client, LLMModelClient):
                return None, None, None
            return client.model.provider_id, client.model.model_id, client
        except Exception:
            return None, None, None

    # ---- Endpoints ----

    @register.api(method="GET", path="/v1/models", auth=False)
    async def list_models(self, request: Request):
        """GET /api/plugin/openai-api/v1/models — List available LLM models."""
        err = self._check_api_key(request)
        if err:
            return err

        data = [
            {
                "id": f"{info.provider_id}:{info.model_id}",
                "model_name": info.model_name,
                "object": "model",
                "created": 0,
                "owned_by": info.provider_name,
            }
            for info in self._get_llm_models()
        ]

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
        if model_str is not None and not isinstance(model_str, str):
            return self._error_response("'model' must be a string")
        stream = body.get("stream", False)
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens")
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")

        # Resolve model
        provider_id, model_id, client = self._resolve_model(model_str)
        if client is None:
            return self._error_response(
                f"Model '{model_str}' not found, unavailable, or ambiguous. Use an ID from /v1/models or configure a default model.",
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
        except ProviderAPIError as e:
            return self._error_response(f"Provider unavailable: {e}", "api_error", 503)
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
            except ProviderAPIError as e:
                yield self._sse_chunk({"error": {"message": f"Provider unavailable: {e}", "type": "api_error"}})
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
