"""gateway 단위 테스트 전체가 공유하는 fake client/설정 픽스처 모음.

FakeGatewayClients/FakeRuntimeClient로 실제 vLLM/risk-adapter 없이 gateway
앱을 띄우고, settings()/tool_calling_settings()/advanced_chat_settings() 등으로
각 테스트가 필요한 정책 조합을 만든다."""

from __future__ import annotations

import asyncio
from dataclasses import replace


import json


from pathlib import Path

import yaml


from tests.support.asgi import InlineASGITestClient as TestClient


from jsonschema import Draft202012Validator


from ai_model_serving.errors import ServiceError


from ai_model_serving.contracts import ChatResponseExpectations, validate_chat_response


from ai_model_serving.apps import gateway as gateway_app_module


from ai_model_serving.apps.gateway import GatewayClients, create_gateway_app


from ai_model_serving.api.routers.gateway_ops import _readiness


from ai_model_serving.services.readiness import DependencyProbe, collect_readiness


from ai_model_serving.settings import AppSettings, EmbeddingProfile, RuntimeEndpoint, SecuritySettings
from ai_model_serving.deployment_target import load_deployment_target
from ai_model_serving.domain import ModelRegistry


_ROOT = Path(__file__).resolve().parents[3]
_DYNAMIC_TARGET = load_deployment_target(
    _ROOT / "configs/deployment_targets.yaml", "linux-nvidia-dynamic"
)
_MODEL_CATALOG = yaml.safe_load((_ROOT / "configs/model_catalog.yaml").read_text(encoding="utf-8"))
_MODEL_SERVING = yaml.safe_load((_ROOT / "configs/model_serving.yaml").read_text(encoding="utf-8"))


class FakeRuntimeClient:
    def __init__(self, post_response=None, ready=True, get_response=None, endpoint: RuntimeEndpoint | None = None, stream_chunks=None):
        self.post_response = post_response or {}
        self.get_response = get_response
        self.ready = ready
        self.last_path = None
        self.last_payload = None
        self.last_headers = None
        self.endpoint = endpoint or RuntimeEndpoint("fake", "http://runtime/v1", "fake", 1)
        self.stream_chunks = stream_chunks or [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b'data: [DONE]\n\n']

    async def post_json(self, path, payload, **kwargs):
        self.last_path = path
        self.last_payload = payload
        self.last_headers = kwargs.get("headers")
        if callable(self.post_response):
            return self.post_response(path, payload, **kwargs)
        return self.post_response

    async def get_json(self, path, **kwargs):
        self.last_path = path
        self.last_headers = kwargs.get("headers")
        if not self.ready:
            from ai_model_serving.errors import ServiceError
            raise ServiceError("MODEL_UNAVAILABLE", "not ready")
        if self.get_response is not None:
            return self.get_response
        return {"object": "list", "data": []}

    async def stream_bytes(self, path, payload, **kwargs):
        self.last_path = path
        self.last_payload = payload
        self.last_headers = kwargs.get("headers")
        for chunk in self.stream_chunks:
            yield chunk


class ExplodingRuntimeClient:
    endpoint = RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1)

    async def post_json(self, path, payload, **kwargs):
        raise RuntimeError("boom")

    async def get_json(self, path, **kwargs):
        return {"object": "list", "data": []}


class StreamingErrorRuntimeClient(FakeRuntimeClient):
    def __init__(self, *, fail_after_first_chunk: bool = False):
        super().__init__(endpoint=RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1))
        self.fail_after_first_chunk = fail_after_first_chunk

    async def stream_bytes(self, path, payload, **kwargs):
        self.last_path = path
        self.last_payload = payload
        self.last_headers = kwargs.get("headers")
        if self.fail_after_first_chunk:
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise ServiceError("UPSTREAM_TIMEOUT", "stream read timeout")


class ExplodingGatewayClients:
    def __init__(self):
        self.main_llm = ExplodingRuntimeClient()
        embedding = FakeRuntimeClient({
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": 0}],
        })
        self.embedding_clients = {"local-embed": embedding}
        self.runtime_clients_by_service_key = {"embedding": embedding}
        self.runtimes = {"main_llm": self.main_llm, "embedding": embedding}
        self.risk_adapter = FakeRuntimeClient({"status": "ready", "service": "risk-adapter", "dependencies": []})
        self.runtimes["risk_adapter"] = self.risk_adapter
        from ai_model_serving.services.runtime_state import RuntimeStateStore
        self.runtime_state = RuntimeStateStore()
        self.sidecar = None


class FakeGatewayClients:
    def __init__(self):
        self.main_llm = FakeRuntimeClient({
            "id": "chatcmpl_1",
            "object": "chat.completion",
            "created": 1,
            "model": "local-main",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        }, endpoint=RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1))
        embedding = FakeRuntimeClient({
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": 0}],
        }, endpoint=RuntimeEndpoint("local-embed", "http://embed/v1", "local-embed", 1))
        embedding_ko = FakeRuntimeClient({
            "object": "list",
            "model": "local-embed-ko",
            "data": [{"object": "embedding", "embedding": [0.1] * 1024, "index": 0}],
        }, endpoint=RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1))
        self.embedding_clients = {"local-embed": embedding, "local-embed-ko": embedding_ko}
        self.runtime_clients_by_service_key = {
            "embedding": embedding,
            "embedding_ko": embedding_ko,
        }
        self.risk_adapter = FakeRuntimeClient(
            {
                "assessment_id": "risk_1",
                "status": "completed",
                "risk_detected": False,
                "attention_required": False,
                "model_risk_detected": False,
                "system_signal_detected": False,
                "assessment_complete": True,
                "strongest_code": None,
                "message": "No model risk signal detected.",
                "categories": [],
                "system_signals": [],
            },
            get_response={"status": "ready", "service": "risk-adapter", "dependencies": []},
            endpoint=RuntimeEndpoint("risk-adapter", "http://risk", "risk-adapter", 1),
        )
        self.runtimes = {
            "main_llm": self.main_llm,
            "embedding": embedding,
            "embedding_ko": embedding_ko,
            "risk_adapter": self.risk_adapter,
        }
        from ai_model_serving.services.runtime_state import RuntimeStateStore
        self.runtime_state = RuntimeStateStore()
        self.sidecar = None


def public_models():
    return ModelRegistry(_MODEL_CATALOG, _MODEL_SERVING).public_model_response_items()


# Gateway 테스트는 실제 운영 정책을 복사하지 않는다. 정책 자체의 정합성은
# make validate가 맡고, 여기서는 그 정책을 소비할 때의 동작만 확인한다.
_MAIN_MODEL_PROFILES = yaml.safe_load((_ROOT / "configs/main_model_profiles.yaml").read_text(encoding="utf-8"))
# 활성 default profile이 실제로 쓰는 chat parameter 정책이다. 이전에는 이 블록들을
# 픽스처에 그대로 베껴 두어서, 프로필에 항목이 늘어도 픽스처는 조용히 옛 정책으로
# 남았다(실제로 stream_options가 빠진 채 드리프트해 있었다).
_PRODUCTION_CHAT_POLICY = _MAIN_MODEL_PROFILES["profiles"][
    _MAIN_MODEL_PROFILES["default_profile"]
]["gateway_policy"]["request_parameter_policy"]
_PRODUCTION_EMBEDDING_POLICY = _MODEL_SERVING["models"]["embedding"]["request_parameter_policy"]
_PRODUCTION_EMBEDDING_KO_POLICY = _MODEL_SERVING["models"]["embedding_ko"]["request_parameter_policy"]


def settings() -> AppSettings:
    endpoint = RuntimeEndpoint("x", "http://runtime/v1", "x", 1)
    main_llm = RuntimeEndpoint(
        "local-main", "http://main/v1", "local-main", 1,
        max_output_tokens=1024, allowed_input_modalities=("text", "image"),
        max_image_inputs=1, allowed_image_url_schemes=("data",), max_image_bytes=200,
        max_image_pixels=4,
        allowed_image_mime_types=("image/png", "image/jpeg", "image/webp", "image/avif", "image/jp2", "image/gif", "image/bmp", "image/tiff", "image/x-tiff"),
    )
    embedding = RuntimeEndpoint("local-embed", "http://embed/v1", "local-embed", 1, request_parameter_policy=_PRODUCTION_EMBEDDING_POLICY)
    embedding_ko = RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1, request_parameter_policy=_PRODUCTION_EMBEDDING_KO_POLICY)
    return AppSettings(
        app_env="test",
        project_version="0.1.0",
        deployment_target=_DYNAMIC_TARGET,
        security=SecuritySettings(api_key_required=True, api_keys=frozenset({"test-key"}), internal_service_token="internal-test-key"),
        gateway_timeout_seconds=1,
        risk_adapter_timeout_seconds=1,
        runtime_endpoints={"main_llm": main_llm, "embedding": embedding, "embedding_ko": embedding_ko, "risk_prompt": endpoint},
        required_runtime_keys=frozenset({"main_llm", "embedding", "embedding_ko", "risk_prompt"}),
        controllable_runtime_keys=frozenset({"embedding", "embedding_ko", "risk_prompt"}),
        risk_adapter_base_url="http://risk",
        public_models=public_models(),
        embedding_profiles={
            "local-embed": EmbeddingProfile(
                model="local-embed",
                service_key="embedding",
                default_dimensions=768,
                retrieval_enabled=True,
                prompt_policy={
                    "retrieval_query": {"mode": "prefix", "prefix": "task: search result | query: "},
                    "retrieval_document": {"mode": "prefix", "prefix": "title: none | text: "},
                },
                request_parameter_policy=_PRODUCTION_EMBEDDING_POLICY,
            ),
            "local-embed-ko": EmbeddingProfile(
                model="local-embed-ko",
                service_key="embedding_ko",
                default_dimensions=1024,
                retrieval_enabled=True,
                prompt_policy={
                    "retrieval_query": {"mode": "prefix", "prefix": "query: "},
                    "retrieval_document": {"mode": "none"},
                },
                request_parameter_policy=_PRODUCTION_EMBEDDING_KO_POLICY,
            ),
        },
        default_embedding_model="local-embed",
        default_retrieval_model="local-embed-ko",
    )


def _settings_with_embedding_profiles(
    *,
    embedding_profiles: dict[str, EmbeddingProfile],
    runtime_endpoints: dict[str, RuntimeEndpoint] | None = None,
) -> AppSettings:
    base = settings()
    endpoints = dict(base.runtime_endpoints)
    if runtime_endpoints:
        endpoints.update(runtime_endpoints)
    return replace(
        base,
        runtime_endpoints=endpoints,
        embedding_profiles=embedding_profiles,
    )


class SpyVLLMClient(FakeRuntimeClient):
    created_endpoints: list[RuntimeEndpoint] = []
    closed_endpoints: list[str] = []

    def __init__(self, endpoint: RuntimeEndpoint):
        super().__init__(endpoint=endpoint)
        self.endpoint = endpoint
        self.closed = False
        self.__class__.created_endpoints.append(endpoint)

    async def aclose(self):
        self.closed = True
        self.__class__.closed_endpoints.append(self.endpoint.logical_id)


def tool_calling_settings() -> AppSettings:
    cfg = settings()
    main = RuntimeEndpoint(
        "local-main",
        "http://main/v1",
        "local-main",
        1,
        max_output_tokens=1024,
        allowed_input_modalities=("text", "image"),
        max_image_inputs=1,
        allowed_image_url_schemes=("data",),
        max_image_bytes=200,
        max_image_pixels=4,
        allowed_image_mime_types=(
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/avif",
            "image/jp2",
            "image/gif",
            "image/bmp",
            "image/tiff",
            "image/x-tiff",
        ),
        request_parameter_policy={
            "allow_unlisted_parameters": False,
            "supported_parameters": [
                "stream",
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "min_p",
                "presence_penalty",
                "frequency_penalty",
                "repetition_penalty",
                "stop",
                "seed",
                "n",
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "reasoning",
                "user",
            ],
            "drop_upstream_parameters": ["user"],
            "max_n": 1,
            "tool_calling": {"enabled": True, "max_tools": 4, "allow_parallel_tool_calls": False},
            "reasoning": {
                "enabled": True,
                "default": False,
                "upstream_chat_template_kwargs": {"enable_thinking": True},
            },
        },
    )
    return replace(
        cfg,
        runtime_endpoints={**cfg.runtime_endpoints, "main_llm": main},
    )


def advanced_chat_settings() -> AppSettings:
    cfg = tool_calling_settings()
    policy = dict(cfg.runtime("main_llm").request_parameter_policy or {})
    supported = list(policy["supported_parameters"])
    for field in ["response_format", "logprobs", "top_logprobs", "logit_bias"]:
        if field not in supported:
            supported.append(field)
    policy.update(
        {
            "supported_parameters": supported,
            **{
                key: _PRODUCTION_CHAT_POLICY[key]
                for key in ("response_format", "logprobs", "top_logprobs", "logit_bias")
            },
        }
    )
    main = RuntimeEndpoint(
        cfg.runtime("main_llm").logical_id,
        cfg.runtime("main_llm").base_url,
        cfg.runtime("main_llm").model,
        cfg.runtime("main_llm").timeout_seconds,
        max_output_tokens=cfg.runtime("main_llm").max_output_tokens,
        allowed_input_modalities=cfg.runtime("main_llm").allowed_input_modalities,
        max_image_inputs=cfg.runtime("main_llm").max_image_inputs,
        allowed_image_url_schemes=cfg.runtime("main_llm").allowed_image_url_schemes,
        max_image_bytes=cfg.runtime("main_llm").max_image_bytes,
        max_image_pixels=cfg.runtime("main_llm").max_image_pixels,
        allowed_image_mime_types=cfg.runtime("main_llm").allowed_image_mime_types,
        request_parameter_policy=policy,
    )
    return replace(
        cfg,
        runtime_endpoints={**cfg.runtime_endpoints, "main_llm": main},
        public_models=(
            {
                "id": "local-main",
                "object": "model",
                "backend": "main_llm_vllm",
                "capabilities": ["chat.completions"],
                "request_parameters": {
                    "response_format": {"type": "object", "allowed_types": ["text", "json_object", "json_schema"]},
                    "logprobs": {"type": "boolean", "default": False, "allow_stream": True},
                    "top_logprobs": {"type": "integer", "min": 0, "max": 10, "requires": {"logprobs": True}},
                    "logit_bias": {"type": "object", "max_entries": 256, "value_min": -100, "value_max": 100, "token_id_semantics": "served_model_tokenizer"},
                },
            },
        ),
    )


def auth_headers():
    return {"Authorization": "Bearer test-key"}


def error_schema():
    return json.loads(Path("specs/schemas/common_error.schema.json").read_text())


def admin_settings() -> AppSettings:
    cfg = settings()
    return replace(
        cfg,
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            admin_api_key_required=True,
            admin_api_keys=frozenset({"admin-test-key"}),
        ),
    )


def _json_schema_format(schema: dict | None = None, *, name: str = "answer") -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema
            or {
                "type": "object",
                "additionalProperties": False,
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    }


__all__ = [name for name in globals() if not name.startswith("__")]
