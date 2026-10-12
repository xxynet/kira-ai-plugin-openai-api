import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from packaging.specifiers import SpecifierSet

from core.config.config_loader import KiraConfig
from core.config.config_field import build_fields
from core.provider import LLMModelClient, LLMResponse, LLMStreamChunk, ModelType, ProviderManager
from core.provider.model_migration import migrate_model_select_fields


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openai_api_v3_plugin", PLUGIN_ROOT / "main.py")
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)
OpenAIAPIPlugin = plugin_module.OpenAIAPIPlugin


class RecordingLLM(LLMModelClient):
    calls = []

    async def chat(self, request, **kwargs):
        self.calls.append((self.model, request, kwargs))
        return LLMResponse(text_response="Hello", input_tokens=2, output_tokens=1)

    async def chat_stream(self, request, **kwargs):
        self.calls.append((self.model, request, kwargs))
        yield LLMStreamChunk(delta_text="Hello")
        yield LLMStreamChunk(is_final=True, finish_reason="stop", usage={"input_tokens": 2, "output_tokens": 1})


@pytest.fixture
def plugin():
    RecordingLLM.calls = []
    manager = object.__new__(ProviderManager)
    manager.kira_config = dict.__new__(KiraConfig)
    manager.kira_config.update({
        "providers": {
            "p1": {
                "name": "Provider One", "status": "active", "model_config_version": 2,
                "model_config": {
                    "llm": {"m1": {"model_name": "gpt-4o", "config": {}, "legacy_ids": ["old-gpt"]}},
                    "tts": {"speech": {"model_name": "speech", "config": {}, "legacy_ids": ["old-gpt"]}},
                },
            },
            "p2": {
                "name": "Provider Two", "status": "inactive", "model_config_version": 2,
                "model_config": {"llm": {"m2": {"model_name": "gpt-4o", "config": {}}}},
            },
        },
        "models": {"default_llm": "p1:m1"},
    })
    # Keep a stale runtime instance to verify the live status check.
    manager._providers = {pid: SimpleNamespace(models={ModelType.LLM: RecordingLLM}) for pid in ("p1", "p2")}
    return OpenAIAPIPlugin(SimpleNamespace(provider_mgr=manager, config=manager.kira_config), {
        "api_key": "local-test-key", "enable_request_log": False,
    })


def entry(plugin):
    return plugin.ctx.provider_mgr.kira_config["providers"]["p1"]["model_config"]["llm"]["m1"]


def enable_second(plugin, same_display_name=False):
    provider = plugin.ctx.provider_mgr.kira_config["providers"]["p2"]
    provider["status"] = "active"
    if same_display_name:
        provider["name"] = "Provider One"


@pytest.mark.parametrize("reference", [
    "p1:m1", "m1", "gpt-4o", "p1:gpt-4o", "Provider One/gpt-4o",
    "Provider One/m1", "old-gpt", "p1:old-gpt", "Provider One/old-gpt",
])
def test_v3_references_use_internal_llm_identity(plugin, reference):
    provider_id, model_id, client = plugin._resolve_model(reference)
    assert (provider_id, model_id) == ("p1", "m1")
    assert isinstance(client, LLMModelClient)
    assert client.model.model_name == "gpt-4o"
    assert client.model.model_type == ModelType.LLM


@pytest.mark.parametrize("reference", [None, ""])
def test_framework_default_uses_internal_identity(plugin, reference):
    assert plugin._resolve_model(reference)[:2] == ("p1", "m1")


@pytest.mark.parametrize("reference", ["p1:m1", "p1:old-gpt", "gpt-4o", "Provider One/gpt-4o"])
def test_plugin_default_uses_the_same_resolver(plugin, reference):
    plugin._default_model = reference
    assert plugin._resolve_model(None)[:2] == ("p1", "m1")


def test_missing_plugin_default_does_not_fall_back_to_another_model(plugin):
    plugin._default_model = "p1:missing"
    assert plugin._resolve_model(None) == (None, None, None)


@pytest.mark.parametrize("reference", ["speech", "p1:speech", "missing", "p2:m2", "p2:gpt-4o", 42, []])
def test_unavailable_or_non_llm_references_are_rejected(plugin, reference):
    assert plugin._resolve_model(reference) == (None, None, None)


def test_duplicate_upstream_names_require_explicit_provider(plugin):
    enable_second(plugin)
    assert plugin._resolve_model("gpt-4o") == (None, None, None)
    assert plugin._resolve_model("p1:gpt-4o")[:2] == ("p1", "m1")
    assert plugin._resolve_model("p2:gpt-4o")[:2] == ("p2", "m2")


def test_duplicate_provider_display_names_do_not_pick_the_first(plugin):
    enable_second(plugin, same_display_name=True)
    assert plugin._resolve_model("Provider One/gpt-4o") == (None, None, None)
    assert plugin._resolve_model("p2:m2")[:2] == ("p2", "m2")


def test_current_name_and_legacy_alias_collision_is_ambiguous(plugin):
    models = plugin.ctx.provider_mgr.kira_config["providers"]["p1"]["model_config"]["llm"]
    models["m3"] = {"model_name": "old-gpt", "config": {}}
    assert plugin._resolve_model("p1:old-gpt") == (None, None, None)
    assert plugin._resolve_model("p1:m1")[:2] == ("p1", "m1")


@pytest.mark.parametrize("upstream_name", ["org/model:variant", "qwen:8b", "org/model"])
def test_upstream_names_with_delimiters(plugin, upstream_name):
    entry(plugin)["model_name"] = upstream_name
    for reference in (upstream_name, f"p1:{upstream_name}", f"Provider One/{upstream_name}"):
        assert plugin._resolve_model(reference)[:2] == ("p1", "m1")


def test_rename_keeps_stable_ids_and_migrated_aliases(plugin):
    entry(plugin)["model_name"] = "renamed-model"
    plugin.ctx.provider_mgr.kira_config["providers"]["p1"]["name"] = "Renamed Provider"
    for reference in ("p1:m1", "old-gpt", "p1:renamed-model", "Renamed Provider/renamed-model"):
        assert plugin._resolve_model(reference)[:2] == ("p1", "m1")


def test_provider_without_llm_implementation_is_excluded(plugin):
    plugin.ctx.provider_mgr._providers["p1"].models = {}
    assert plugin._get_llm_models() == []
    assert plugin._resolve_model("p1:m1") == (None, None, None)


def test_model_picker_migrates_old_default_without_changing_unrelated_fields(plugin):
    schema = json.loads((PLUGIN_ROOT / "schema.json").read_text(encoding="utf-8"))
    config = {"default_model": "p1:old-gpt", "api_key": "unchanged"}
    assert migrate_model_select_fields(config, build_fields(schema), plugin.ctx.provider_mgr.kira_config["providers"])
    assert config == {"default_model": "p1:m1", "api_key": "unchanged"}
    assert set(schema["default_model"]["locales"]) == {"zh"}
    assert schema["default_model"]["model_type"] == "llm"


def test_v3_manifest_rejects_v2_and_v4_prereleases():
    manifest = json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.1.0"
    supported = SpecifierSet(manifest["core_version"], prereleases=True)
    assert all(v in supported for v in ("3.0.0-alpha.1", "3.0.0-rc.1", "3.0.0", "3.1.0"))
    assert all(v not in supported for v in ("2.34.8", "4.0.0-alpha.1", "4.0.0"))


def application(plugin):
    app = FastAPI()
    app.add_api_route("/v1/models", plugin.list_models, methods=["GET"])
    app.add_api_route("/v1/chat/completions", plugin.chat_completions, methods=["POST"])
    return app


@pytest.mark.anyio
async def test_model_listing_is_stable_unique_and_excludes_inactive_and_tts(plugin):
    async with AsyncClient(transport=ASGITransport(application(plugin)), base_url="http://test",
                           headers={"Authorization": "Bearer local-test-key"}) as client:
        data = (await client.get("/v1/models")).json()["data"]
        assert [(item["id"], item["model_name"]) for item in data] == [("p1:m1", "gpt-4o")]
        enable_second(plugin, same_display_name=True)
        data = (await client.get("/v1/models")).json()["data"]
        assert {item["id"] for item in data} == {"p1:m1", "p2:m2"}
        assert all(plugin._resolve_model(item["id"])[2] is not None for item in data)
        entry(plugin)["model_name"] = "renamed-model"
        data = (await client.get("/v1/models")).json()["data"]
        assert data[0]["id"] == "p1:m1" and data[0]["model_name"] == "renamed-model"


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True])
async def test_http_completions_route_upstream_name_and_return_canonical_id(plugin, stream):
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}],
            "stream": stream, "temperature": 0.4, "max_tokens": 7,
            "tools": [{"type": "function", "function": {"name": "local_tool", "parameters": {"type": "object"}}}]}
    async with AsyncClient(transport=ASGITransport(application(plugin)), base_url="http://test",
                           headers={"Authorization": "Bearer local-test-key"}) as client:
        response = await client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    if stream:
        assert response.text.endswith("data: [DONE]\n\n")
        chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        assert all(chunk["model"] == "p1:m1" for chunk in chunks)
        assert chunks[0]["choices"][0]["delta"]["content"] == "Hello"
    else:
        result = response.json()
        assert result["model"] == "p1:m1" and result["choices"][0]["message"]["content"] == "Hello"
    info, request, overrides = RecordingLLM.calls[0]
    assert info.model_id == "m1" and info.model_name == "gpt-4o"
    assert request.messages[0].content == "Hi" and request.tools == body["tools"]
    assert overrides == {"temperature": 0.4, "max_tokens": 7}


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_disabled_after_resolution_stops_the_call(plugin, monkeypatch, stream):
    resolve = plugin._resolve_model

    def disable_after_resolving(reference):
        result = resolve(reference)
        plugin.ctx.provider_mgr.kira_config["providers"]["p1"]["status"] = "inactive"
        return result

    monkeypatch.setattr(plugin, "_resolve_model", disable_after_resolving)
    async with AsyncClient(transport=ASGITransport(application(plugin)), base_url="http://test",
                           headers={"Authorization": "Bearer local-test-key"}) as client:
        response = await client.post("/v1/chat/completions", json={
            "model": "p1:m1", "messages": [{"role": "user", "content": "Hi"}], "stream": stream,
        })
    assert RecordingLLM.calls == []
    if stream:
        assert '"error"' in response.text and response.text.endswith("data: [DONE]\n\n")
    else:
        assert response.status_code == 503 and response.json()["error"]["type"] == "api_error"


@pytest.mark.anyio
async def test_http_auth_invalid_model_and_ambiguous_routing(plugin):
    async with AsyncClient(transport=ASGITransport(application(plugin)), base_url="http://test") as client:
        assert (await client.get("/v1/models")).status_code == 401
        client.headers["Authorization"] = "Bearer local-test-key"
        body = {"model": 123, "messages": [{"role": "user", "content": "Hi"}]}
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 400
        enable_second(plugin)
        body["model"] = "gpt-4o"
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 404
        body["model"] = "p1:m1"
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 200

def test_explicit_internal_id_wins_over_an_upstream_name_collision(plugin):
    models = plugin.ctx.provider_mgr.kira_config["providers"]["p1"]["model_config"]["llm"]
    models["m3"] = {"model_name": "m1", "config": {}}
    assert plugin._resolve_model("p1:m1")[:2] == ("p1", "m1")


def test_disabled_provider_prefix_does_not_fall_back_to_an_upstream_name(plugin):
    entry(plugin)["model_name"] = "p2:m2"
    assert plugin._resolve_model("p2:m2") == (None, None, None)
    entry(plugin)["model_name"] = "Provider Two/gpt-4o"
    assert plugin._resolve_model("Provider Two/gpt-4o") == (None, None, None)

def test_framework_default_accepts_typed_migrated_alias(plugin):
    plugin.ctx.provider_mgr.kira_config["models"]["default_llm"] = "p1:old-gpt"
    assert plugin._resolve_model(None)[:2] == ("p1", "m1")


@pytest.mark.parametrize("reference", [None, "p1:m1"])
def test_missing_runtime_provider_cannot_be_resolved(plugin, reference):
    plugin.ctx.provider_mgr._providers.pop("p1")
    assert plugin._get_llm_models() == []
    assert plugin._resolve_model(reference) == (None, None, None)
