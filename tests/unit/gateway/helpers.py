from __future__ import annotations

import asyncio


import json


from pathlib import Path


from fastapi.testclient import TestClient


from jsonschema import Draft202012Validator


from ai_model_serving.errors import ServiceError


from ai_model_serving.contracts import ChatResponseExpectations, validate_chat_response


from ai_model_serving.apps import gateway as gateway_app_module


from ai_model_serving.apps.gateway import GatewayClients, create_gateway_app


from ai_model_serving.api.routers.gateway_ops import _readiness


from ai_model_serving.services.readiness import DependencyProbe, collect_readiness


from ai_model_serving.settings import AppSettings, EmbeddingProfile, RuntimeEndpoint, SecuritySettings


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
            raise ServiceError("MODEL_UNAVAILABLE", "not ready", True, 503)
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
        raise ServiceError("UPSTREAM_TIMEOUT", "stream read timeout", True, 504)


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
    return (
        {
            "id": "local-main",
            "object": "model",
            "backend": "main_llm_vllm",
            "capabilities": ["chat.completions"],
            "request_parameters": {"temperature": {"type": "number", "min": 0, "max": 2}, "stream": {"type": "boolean"}},
        },
        {
            "id": "local-embed",
            "object": "model",
            "backend": "embedding_vllm",
            "capabilities": ["embeddings", "retrieval_rerank", "retrieval_score"],
            "request_parameters": {"dimensions": {"type": "integer", "enum": [768, 512, 256, 128]}},
        },
        {
            "id": "local-embed-ko",
            "object": "model",
            "backend": "embedding_ko_vllm",
            "capabilities": ["embeddings", "retrieval_rerank", "retrieval_score"],
            "request_parameters": {"dimensions": {"type": "integer", "enum": [1024]}},
        },
        {
            "id": "risk-prompt",
            "object": "model",
            "backend": "risk_adapter",
            "capabilities": ["risk.prompt_attack_signal"],
            "request_parameters": {},
            "fixed_parameters": {"max_tokens": 1, "temperature": 0},
        },
    )


_PRODUCTION_EMBEDDING_POLICY = {
    "allow_unlisted_parameters": False,
    "supported_parameters": ["dimensions", "encoding_format", "truncate_prompt_tokens", "user"],
    "drop_upstream_parameters": ["user"],
    "dimensions": [768, 512, 256, 128],
    "max_truncate_prompt_tokens": 2048,
}


_PRODUCTION_EMBEDDING_KO_POLICY = {
    "allow_unlisted_parameters": False,
    "supported_parameters": ["dimensions", "encoding_format", "truncate_prompt_tokens", "user"],
    "drop_upstream_parameters": ["user", "dimensions"],
    "dimensions": [1024],
    "max_truncate_prompt_tokens": 2048,
    "max_embedding_batch_size": 16,
}


def settings() -> AppSettings:
    endpoint = RuntimeEndpoint("x", "http://runtime/v1", "x", 1)
    return AppSettings(
        app_env="test",
        project_version="0.1.0",
        security=SecuritySettings(api_key_required=True, api_keys=frozenset({"test-key"}), internal_service_token="internal-test-key"),
        gateway_timeout_seconds=1,
        risk_adapter_timeout_seconds=1,
        main_llm=RuntimeEndpoint(
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
            allowed_image_mime_types=("image/png", "image/jpeg", "image/webp"),
        ),
        embedding=RuntimeEndpoint("local-embed", "http://embed/v1", "local-embed", 1, request_parameter_policy=_PRODUCTION_EMBEDDING_POLICY),
        embedding_ko=RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1, request_parameter_policy=_PRODUCTION_EMBEDDING_KO_POLICY),
        risk_prompt=endpoint,
        risk_adapter_base_url="http://risk",
        public_models=public_models(),
        embedding_profiles={
            "local-embed": EmbeddingProfile(
                model="local-embed",
                service_key="embedding",
                upstream_model_id="google/embeddinggemma-300m",
                dimensions=(768, 512, 256, 128),
                default_dimensions=768,
                retrieval_enabled=True,
                score_modes=("dense_cosine",),
                prompt_policy={
                    "retrieval_query": {"mode": "prefix", "prefix": "task: search result | query: "},
                    "retrieval_document": {"mode": "prefix", "prefix": "title: none | text: "},
                },
                request_parameter_policy=_PRODUCTION_EMBEDDING_POLICY,
            ),
            "local-embed-ko": EmbeddingProfile(
                model="local-embed-ko",
                service_key="embedding_ko",
                upstream_model_id="dragonkue/snowflake-arctic-embed-l-v2.0-ko",
                dimensions=(1024,),
                default_dimensions=1024,
                retrieval_enabled=True,
                retrieval_default=True,
                score_modes=("dense_cosine",),
                prompt_policy={
                    "retrieval_query": {"mode": "prefix", "prefix": "query: "},
                    "retrieval_document": {"mode": "none"},
                },
                request_parameter_policy=_PRODUCTION_EMBEDDING_KO_POLICY,
            ),
        },
        embedding_model_routes={"local-embed": "embedding", "local-embed-ko": "embedding_ko"},
    )


def _settings_with_embedding_routes(
    *,
    embedding_profiles: dict[str, EmbeddingProfile],
    embedding_model_routes: dict[str, str],
    runtime_endpoints: dict[str, RuntimeEndpoint] | None = None,
) -> AppSettings:
    base = settings()
    endpoints = dict(base.runtime_endpoints)
    if runtime_endpoints:
        endpoints.update(runtime_endpoints)
    return AppSettings(
        app_env=base.app_env,
        project_version=base.project_version,
        security=base.security,
        gateway_timeout_seconds=base.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=base.risk_adapter_timeout_seconds,
        risk_adapter_base_url=base.risk_adapter_base_url,
        runtime_endpoints=endpoints,
        main_llm=endpoints["main_llm"],
        embedding=endpoints.get("embedding"),
        embedding_ko=endpoints.get("embedding_ko"),
        risk_prompt=endpoints.get("risk_prompt"),
        embedding_profiles=embedding_profiles,
        embedding_model_routes=embedding_model_routes,
        default_embedding_model=base.default_embedding_model,
        default_retrieval_model=base.default_retrieval_model,
        max_request_body_bytes=base.max_request_body_bytes,
        public_models=base.public_models,
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


def _settings_with_embedding_policy(policy: dict) -> AppSettings:
    base = settings()
    embed = RuntimeEndpoint(
        "local-embed", "http://embed/v1", "local-embed", 1,
        request_parameter_policy=policy,
    )
    return AppSettings(
        app_env=base.app_env,
        project_version=base.project_version,
        security=base.security,
        gateway_timeout_seconds=base.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=base.risk_adapter_timeout_seconds,
        main_llm=base.main_llm,
        embedding=embed,
        embedding_ko=base.embedding_ko,
        risk_prompt=base.risk_prompt,
        risk_adapter_base_url=base.risk_adapter_base_url,
        max_request_body_bytes=base.max_request_body_bytes,
        public_models=base.public_models,
        embedding_profiles={
            **base.embedding_profiles,
            "local-embed": EmbeddingProfile(
                model="local-embed",
                service_key="embedding",
                upstream_model_id="google/embeddinggemma-300m",
                dimensions=tuple(policy.get("dimensions", [768, 512, 256, 128])),
                default_dimensions=768,
                retrieval_enabled=True,
                score_modes=("dense_cosine",),
                request_parameter_policy=policy,
            ),
        },
        embedding_model_routes=base.embedding_model_routes,
    )


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
        allowed_image_mime_types=("image/png", "image/jpeg", "image/webp"),
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
            ],
            "max_n": 1,
            "tool_calling": {"enabled": True, "max_tools": 4, "allow_parallel_tool_calls": False},
            "reasoning": {"enabled": True, "default": False},
        },
    )
    return AppSettings(
        app_env=cfg.app_env,
        project_version=cfg.project_version,
        security=cfg.security,
        gateway_timeout_seconds=cfg.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=cfg.risk_adapter_timeout_seconds,
        main_llm=main,
        embedding=cfg.embedding,
        risk_prompt=cfg.risk_prompt,
        risk_adapter_base_url=cfg.risk_adapter_base_url,
        max_request_body_bytes=cfg.max_request_body_bytes,
        public_models=cfg.public_models,
    )


def advanced_chat_settings() -> AppSettings:
    cfg = tool_calling_settings()
    policy = dict(cfg.main_llm.request_parameter_policy or {})
    supported = list(policy["supported_parameters"])
    for field in ["response_format", "logprobs", "top_logprobs", "logit_bias"]:
        if field not in supported:
            supported.append(field)
    policy.update(
        {
            "supported_parameters": supported,
            "response_format": {
                "enabled": True,
                "types": ["text", "json_object", "json_schema"],
                "json_object": {"require_json_instruction": True},
                "json_schema": {
                    "enabled": True,
                    "require_root_object": True,
                    "require_additional_properties_false": True,
                    "strict": {"allowed": True, "require_true": False},
                    "max_schema_bytes": 16384,
                    "max_depth": 8,
                    "max_total_properties": 64,
                    "max_properties_per_object": 32,
                    "max_required": 64,
                    "max_enum_values": 128,
                    "max_enum_string_length": 256,
                    "max_property_name_length": 64,
                    "max_total_schema_string_length": 32768,
                    "disallowed_keywords": [
                        "allOf",
                        "not",
                        "if",
                        "then",
                        "else",
                        "dependentRequired",
                        "dependentSchemas",
                        "patternProperties",
                        "$dynamicRef",
                        "$recursiveRef",
                        "$dynamicAnchor",
                        "$recursiveAnchor",
                        "$id",
                        "$anchor",
                    ],
                    "root_disallowed_keywords": ["anyOf"],
                },
            },
            "logprobs": {"enabled": True, "default": False, "allow_stream": True},
            "top_logprobs": {"min": 0, "max": 10, "requires_logprobs": True},
            "logit_bias": {
                "enabled": True,
                "max_entries": 256,
                "min_bias": -100,
                "max_bias": 100,
                "token_id_min": 0,
                "token_id_semantics": "served_model_tokenizer",
            },
            "combinations": {
                "json_schema_with_tools": {"mode": "capability_gate", "default": "allow"},
                "json_schema_with_reasoning": {"mode": "capability_gate", "default": "allow"},
                "json_schema_with_logit_bias": {"mode": "allow"},
                "logit_bias_with_tools": {"mode": "allow"},
                "logprobs_with_stream": {"mode": "allow"},
            },
        }
    )
    main = RuntimeEndpoint(
        cfg.main_llm.logical_id,
        cfg.main_llm.base_url,
        cfg.main_llm.model,
        cfg.main_llm.timeout_seconds,
        max_output_tokens=cfg.main_llm.max_output_tokens,
        allowed_input_modalities=cfg.main_llm.allowed_input_modalities,
        max_image_inputs=cfg.main_llm.max_image_inputs,
        allowed_image_url_schemes=cfg.main_llm.allowed_image_url_schemes,
        max_image_bytes=cfg.main_llm.max_image_bytes,
        max_image_pixels=cfg.main_llm.max_image_pixels,
        allowed_image_mime_types=cfg.main_llm.allowed_image_mime_types,
        request_parameter_policy=policy,
        runtime_features={"structured_outputs": {"enabled": True, "backend": "xgrammar:disable-any-whitespace", "enable_in_reasoning": True}},
    )
    return AppSettings(
        app_env=cfg.app_env,
        project_version=cfg.project_version,
        security=cfg.security,
        gateway_timeout_seconds=cfg.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=cfg.risk_adapter_timeout_seconds,
        main_llm=main,
        embedding=cfg.embedding,
        risk_prompt=cfg.risk_prompt,
        risk_adapter_base_url=cfg.risk_adapter_base_url,
        max_request_body_bytes=cfg.max_request_body_bytes,
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
    return AppSettings(
        app_env=cfg.app_env,
        project_version=cfg.project_version,
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            admin_api_key_required=True,
            admin_api_keys=frozenset({"admin-test-key"}),
        ),
        gateway_timeout_seconds=cfg.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=cfg.risk_adapter_timeout_seconds,
        main_llm=cfg.main_llm,
        embedding=cfg.embedding,
        risk_prompt=cfg.risk_prompt,
        risk_adapter_base_url=cfg.risk_adapter_base_url,
        max_request_body_bytes=cfg.max_request_body_bytes,
        public_models=cfg.public_models,
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
