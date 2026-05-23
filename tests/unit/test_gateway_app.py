from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai_model_serving.errors import ServiceError
from ai_model_serving.contracts import ChatResponseExpectations, validate_chat_response

from ai_model_serving.apps.gateway import create_gateway_app
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
        self.embedding = FakeRuntimeClient({
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": 0}],
        })
        self.risk_adapter = FakeRuntimeClient({"status": "ready", "service": "risk-adapter", "dependencies": []})


class FakeGatewayClients:
    def __init__(self):
        self.main_llm = FakeRuntimeClient({
            "id": "chatcmpl_1",
            "object": "chat.completion",
            "created": 1,
            "model": "local-main",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        }, endpoint=RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1))
        self.embedding = FakeRuntimeClient({
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": 0}],
        }, endpoint=RuntimeEndpoint("local-embed", "http://embed/v1", "local-embed", 1))
        self.embedding_ko = FakeRuntimeClient({
            "object": "list",
            "model": "local-embed-ko",
            "data": [{"object": "embedding", "embedding": [0.1] * 1024, "index": 0}],
        }, endpoint=RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1))
        self.embedding_clients = {"local-embed": self.embedding, "local-embed-ko": self.embedding_ko}
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
        runtime_features={"structured_outputs": {"enabled": True, "backend": "auto", "enable_in_reasoning": True}},
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


def test_gateway_health_ready_and_models():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    assert client.get("/health").json() == {"status": "ok", "service": "gateway"}
    ready = client.get("/ready").json()
    assert ready["status"] == "ready"
    assert ready["phase"] == "serving"
    assert ready["not_ready_dependencies"] == []
    models = client.get("/v1/models", headers=auth_headers()).json()
    by_id = {item["id"]: item for item in models["data"]}
    assert set(by_id) == {"local-main", "local-embed", "local-embed-ko", "risk-prompt"}
    assert by_id["local-main"]["request_parameters"]["temperature"] == {"type": "number", "min": 0, "max": 2}
    assert by_id["local-main"]["request_parameters"]["stream"] == {"type": "boolean"}
    assert by_id["local-embed"]["request_parameters"]["dimensions"]["enum"] == [768, 512, 256, 128]
    assert "retrieval_score" in by_id["local-embed"]["capabilities"]
    assert by_id["local-embed-ko"]["capabilities"] == [
        "embeddings",
        "retrieval_rerank",
        "retrieval_score",
    ]
    assert by_id["risk-prompt"]["request_parameters"] == {}
    assert by_id["risk-prompt"]["fixed_parameters"] == {"max_tokens": 1, "temperature": 0}


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


def test_gateway_ready_forwards_admin_token_to_risk_adapter_readiness():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(admin_settings(), clients))
    response = client.get("/ready", headers={"Authorization": "Bearer admin-test-key"})
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert clients.risk_adapter.last_path == "/ready"
    assert clients.risk_adapter.last_headers == {"authorization": "Bearer admin-test-key"}




def test_gateway_readiness_reflects_risk_adapter_body_status():
    clients = FakeGatewayClients()
    clients.risk_adapter = FakeRuntimeClient(
        get_response={
            "status": "not_ready",
            "service": "risk-adapter",
            "dependencies": [{"name": "risk_prompt_vllm", "status": "not_ready"}],
        },
        endpoint=RuntimeEndpoint("risk-adapter", "http://risk", "risk-adapter", 1),
    )
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["phase"] == "waiting_for_dependencies"
    assert body["not_ready_dependencies"] == ["risk_adapter"]
    assert {item["name"]: item["status"] for item in body["dependencies"]}["risk_adapter"] == "not_ready"
    risk_dependency = next(item for item in body["dependencies"] if item["name"] == "risk_adapter")
    assert risk_dependency["endpoint"] == "http://risk/ready"
    assert "risk_prompt_vllm" in risk_dependency["message"]


def test_gateway_readiness_503_when_embedding_ko_vllm_down():
    clients = FakeGatewayClients()
    clients.embedding_ko = FakeRuntimeClient(
        ready=False,
        get_response={"error": "model not loaded"},
        endpoint=RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1),
    )
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "embedding_ko_vllm" in body["not_ready_dependencies"]
    assert "embedding_ko_vllm" in body["required_not_ready_dependencies"]
    assert "embedding_ko_vllm" not in body["optional_not_ready_dependencies"]


def test_collect_readiness_response_matches_schema():
    schema = json.loads(Path("specs/schemas/readiness_response.schema.json").read_text())
    validator = Draft202012Validator(schema)

    required_client = FakeRuntimeClient(endpoint=RuntimeEndpoint("req", "http://req/v1", "req", 1))
    optional_client = FakeRuntimeClient(
        ready=False,
        get_response={"error": "not ready"},
        endpoint=RuntimeEndpoint("opt", "http://opt/v1", "opt", 1),
    )
    probes = [
        DependencyProbe("required_dep", required_client, "models", required=True),
        DependencyProbe("optional_dep", optional_client, "models", required=False),
    ]

    body = asyncio.run(collect_readiness(service="gateway", probes=probes))

    validator.validate(body)
    assert body["status"] == "ready"
    assert body["required_not_ready_dependencies"] == []
    assert body["optional_not_ready_dependencies"] == ["optional_dep"]
    assert body["not_ready_dependencies"] == ["optional_dep"]


def test_gateway_framework_documentation_endpoints_are_exposed_by_default():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    doc = openapi.json()
    assert doc["info"]["title"] == "AI Model Serving Gateway"
    assert "빠른 시작" in doc["info"]["description"]
    assert "not_ready_dependencies" in doc["info"]["description"]
    assert {tag["name"] for tag in doc["tags"]} >= {"Operations", "Monitoring", "Models", "Chat", "Embeddings", "Risk"}
    assert doc["paths"]["/ready"]["get"]["responses"]["503"]["content"]["application/json"]["example"]["phase"] == "waiting_for_dependencies"
    assert "catalog" in doc["paths"]["/v1/models"]["get"]["description"]
    assert "not_ready_dependencies" in doc["paths"]["/v1/models"]["get"]["description"]
    assert doc["paths"]["/v1/chat/completions"]["post"]["description"]
    chat_examples = doc["paths"]["/v1/chat/completions"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert chat_examples["basic"]["summary"] == "최소 요청 (runtime 기본 sampling)"
    assert "temperature" not in chat_examples["basic"]["value"]
    assert chat_examples["deterministic_smoke"]["value"]["temperature"] == 0
    assert chat_examples["deterministic_smoke"]["value"]["n"] == 1
    assert chat_examples["with_tools"]["value"]["parallel_tool_calls"] is False
    assert chat_examples["with_reasoning"]["value"]["reasoning"] is True
    assert chat_examples["with_image"]["value"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "bearerAuth" in doc["components"]["securitySchemes"]

    with TestClient(create_gateway_app(admin_settings(), FakeGatewayClients())) as admin_client:
        admin_doc = admin_client.get("/openapi.json").json()
    assert "adminBearerAuth" in admin_doc["components"]["securitySchemes"]
    assert admin_doc["paths"]["/ready"]["get"]["security"] == [{"adminBearerAuth": []}]
    assert "401" in admin_doc["paths"]["/ready"]["get"]["responses"]
    assert admin_doc["paths"]["/metrics"]["get"]["security"] == [{"adminBearerAuth": []}]
    assert "401" in admin_doc["paths"]["/metrics"]["get"]["responses"]



def test_gateway_requires_bearer_auth():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/models")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    Draft202012Validator(error_schema()).validate(response.json())
    assert response.json()["error"]["request_id"].startswith("req_")


def test_gateway_forwards_chat_and_embeddings_to_vllm_paths():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    chat = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert chat.status_code == 200
    assert clients.main_llm.last_path == "chat/completions"
    embed = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "dimensions": 768},
    )
    assert embed.status_code == 200
    assert clients.embedding.last_path == "embeddings"


def test_gateway_dense_retrieval_uses_embedding_runtime():
    clients = FakeGatewayClients()
    def embed_response(_path, payload, **_kwargs):
        vectors = {
            "task: search result | query: 검색어": [1.0] + [0.0] * 767,
            "title: none | text: 관련": [1.0] + [0.0] * 767,
            "title: none | text: 무관": [0.0, 1.0] + [0.0] * 766,
        }
        inputs = payload["input"]
        return {
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": vectors[text], "index": index} for index, text in enumerate(inputs)],
        }
    clients.embedding.post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "검색어", "documents": ["관련", "무관"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["score_mode"] == "dense_cosine"
    assert body["scores"] == [{"index": 0, "score": 1.0}, {"index": 1, "score": 0.0}]
    assert clients.embedding.last_path == "embeddings"


def test_gateway_retrieval_forwards_truncate_prompt_tokens_to_embedding_runtime():
    clients = FakeGatewayClients()
    captured_payloads: list[dict] = []

    def embed_response(_path, payload, **_kwargs):
        captured_payloads.append(payload)
        inputs = payload["input"]
        return {
            "object": "list",
            "model": "local-embed",
            "data": [
                {"object": "embedding", "embedding": [1.0] + [0.0] * 767, "index": index}
                for index, _text in enumerate(inputs)
            ],
        }

    clients.embedding.post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))

    score = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "truncate_prompt_tokens": 512},
    )
    rerank = client.post(
        "/v1/retrieval/rerank",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "truncate_prompt_tokens": 512},
    )

    assert score.status_code == 200
    assert rerank.status_code == 200
    assert captured_payloads
    assert all(payload.get("truncate_prompt_tokens") == 512 for payload in captured_payloads)
    assert all("truncation_side" not in payload for payload in captured_payloads)


def test_gateway_retrieval_rejects_truncation_side_before_upstream_call():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "truncation_side": "left"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "truncation_side" in response.json()["error"]["message"]
    assert clients.embedding.last_path is None


def test_gateway_retrieval_truncate_prompt_tokens_can_change_ranking_canary():
    clients = FakeGatewayClients()

    def embed_response(_path, payload, **_kwargs):
        truncated = payload.get("truncate_prompt_tokens") == 1
        data = []
        for index, text in enumerate(payload["input"]):
            if text.startswith("task: search result | query: "):
                vector = [0.0, 1.0] if truncated else [1.0, 0.0]
            elif text.endswith("first"):
                vector = [1.0, 0.0]
            else:
                vector = [0.0, 1.0]
            data.append({"object": "embedding", "embedding": vector + [0.0] * 766, "index": index})
        return {"object": "list", "model": "local-embed", "data": data}

    clients.embedding.post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))
    base_payload = {"model": "local-embed", "query": "long query", "documents": ["first", "second"]}

    before = client.post("/v1/retrieval/rerank", headers=auth_headers(), json=base_payload)
    after = client.post(
        "/v1/retrieval/rerank",
        headers=auth_headers(),
        json={**base_payload, "truncate_prompt_tokens": 1},
    )

    assert before.status_code == 200
    assert after.status_code == 200
    assert [item["index"] for item in before.json()["results"]] == [0, 1]
    assert [item["index"] for item in after.json()["results"]] == [1, 0]


def test_gateway_rejects_retrieval_extra_fields_before_upstream_call():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    score = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "extra": "x"},
    )
    assert score.status_code == 422
    assert score.json()["error"]["code"] == "VALIDATION_ERROR"
    assert clients.embedding.last_path is None

    token_embeddings = client.post(
        "/v1/retrieval/token-embeddings",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "texts": ["문서"], "extra": "x"},
    )
    assert token_embeddings.status_code == 404


def test_gateway_dense_retrieval_rejects_invalid_embedding_upstream_response():
    clients = FakeGatewayClients()
    clients.embedding.post_response = {"object": "list", "model": "local-embed", "data": []}
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "검색어", "documents": ["문서"]},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_token_embeddings_route_removed():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.post(
        "/v1/retrieval/token-embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "texts": ["문서"]},
    )
    assert response.status_code == 404


def test_gateway_rejects_removed_colbert_ko_model():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    for endpoint in ("/v1/retrieval/rerank", "/v1/retrieval/score"):
        response = client.post(
            endpoint,
            headers=auth_headers(),
            json={"model": "local-colbert-ko", "query": "test", "documents": ["doc"]},
        )
        assert response.status_code == 422, f"{endpoint} should reject local-colbert-ko"
        assert response.json()["error"]["code"] == "MODEL_CAPABILITY_MISMATCH"
        assert clients.embedding.last_path is None
        assert clients.embedding_ko.last_path is None


def test_gateway_rejects_removed_late_interaction_maxsim():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    for endpoint in ("/v1/retrieval/rerank", "/v1/retrieval/score"):
        response = client.post(
            endpoint,
            headers=auth_headers(),
            json={"score_mode": "late_interaction_maxsim", "query": "test", "documents": ["doc"]},
        )
        assert response.status_code == 422, f"{endpoint} should reject late_interaction_maxsim"
        assert response.json()["error"]["code"] == "MODEL_CAPABILITY_MISMATCH"


def test_gateway_retrieval_default_model_is_embed_ko():
    clients = FakeGatewayClients()
    captured_inputs: list[list[str]] = []

    def embed_ko_response(_path, payload, **_kwargs):
        inputs = payload["input"] if isinstance(payload.get("input"), list) else [payload["input"]]
        captured_inputs.append(inputs)
        return {
            "object": "list",
            "model": "local-embed-ko",
            "data": [{"object": "embedding", "embedding": [0.1] * 1024, "index": i} for i in range(len(inputs))],
        }
    clients.embedding_ko.post_response = embed_ko_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"query": "대한민국의 수도는?", "documents": ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "local-embed-ko"
    assert body["score_mode"] == "dense_cosine"
    assert clients.embedding_ko.last_path == "embeddings"
    assert clients.embedding.last_path is None
    assert len(captured_inputs) == 2
    assert captured_inputs[0] == ["query: 대한민국의 수도는?"]
    assert captured_inputs[1] == ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]


def test_gateway_local_embed_ko_retrieval_applies_query_prefix_only():
    """local-embed-ko retrieval query에만 'query: ' prefix가 붙고 document에는 붙지 않는다."""
    clients = FakeGatewayClients()
    captured_inputs: list[list[str]] = []

    def embed_ko_response(_path, payload, **_kwargs):
        inputs = payload["input"]
        captured_inputs.append(inputs)
        return {
            "object": "list",
            "model": "local-embed-ko",
            "data": [{"object": "embedding", "embedding": [0.1] * 1024, "index": i} for i in range(len(inputs))],
        }
    clients.embedding_ko.post_response = embed_ko_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "query": "대한민국의 수도는?", "documents": ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "local-embed-ko"
    assert body["score_mode"] == "dense_cosine"
    assert clients.embedding.last_path is None
    assert len(captured_inputs) == 2
    assert captured_inputs[0] == ["query: 대한민국의 수도는?"]
    assert captured_inputs[1] == ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]


def test_gateway_local_embed_retrieval_applies_task_prefix_regression():
    """local-embed(EmbeddingGemma) prompt policy 불변: query는 task prefix, document는 title prefix."""
    clients = FakeGatewayClients()
    captured_inputs: list[list[str]] = []

    def embed_response(_path, payload, **_kwargs):
        inputs = payload["input"]
        captured_inputs.append(inputs)
        return {
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": i} for i in range(len(inputs))],
        }
    clients.embedding.post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "대한민국 수도는?", "documents": ["서울은 대한민국의 수도이다."]},
    )

    assert response.status_code == 200
    assert clients.embedding_ko.last_path is None
    assert len(captured_inputs) == 2
    assert captured_inputs[0] == ["task: search result | query: 대한민국 수도는?"]
    assert captured_inputs[1] == ["title: none | text: 서울은 대한민국의 수도이다."]


def test_gateway_embeddings_does_not_apply_prompt_policy():
    """/v1/embeddings는 prompt policy를 적용하지 않는다 — local-embed-ko 직접 호출 시 prefix 없음."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "input": ["임베딩할 텍스트 예시입니다."]},
    )

    assert response.status_code == 200
    assert clients.embedding_ko.last_payload["input"] == ["임베딩할 텍스트 예시입니다."]
    assert clients.embedding_ko.last_payload["input"] != ["query: 임베딩할 텍스트 예시입니다."]


def test_gateway_rejects_invalid_payloads_before_upstream_call():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    chat = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": []},
    )
    assert chat.status_code == 422
    assert clients.main_llm.last_path is None
    Draft202012Validator(error_schema()).validate(chat.json())

    embed = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": [], "dimensions": 42},
    )
    assert embed.status_code == 422
    assert clients.embedding.last_path is None
    Draft202012Validator(error_schema()).validate(embed.json())

    embed_zero_truncate = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "truncate_prompt_tokens": 0},
    )
    assert embed_zero_truncate.status_code == 422
    assert clients.embedding.last_path is None

    embed_base64 = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "encoding_format": "base64"},
    )
    assert embed_base64.status_code == 422
    assert clients.embedding.last_path is None



def test_gateway_rejects_embedding_upstream_count_mismatch():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello", "world"]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_rejects_embedding_upstream_dimension_mismatch():
    clients = FakeGatewayClients()
    clients.embedding.post_response = {
        "object": "list",
        "model": "local-embed",
        "data": [{"object": "embedding", "embedding": [0.1], "index": 0}],
    }
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "dimensions": 768},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_rejects_embedding_upstream_index_mismatch():
    clients = FakeGatewayClients()
    clients.embedding.post_response = {
        "object": "list",
        "model": "local-embed",
        "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": 1}],
    }
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "dimensions": 768},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"



def test_gateway_accepts_bounded_multimodal_chat_and_enforces_model_token_cap():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    multimodal = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this image"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAAAAAAAA"}},
                    ],
                }
            ],
        },
    )
    assert multimodal.status_code == 200
    assert clients.main_llm.last_payload["messages"][0]["content"][1]["type"] == "image_url"

    remote_image = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]},
    )
    assert remote_image.status_code == 422

    invalid_base64 = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,not-base64"}}]}]},
    )
    assert invalid_base64.status_code == 422

    unsupported_mime = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/gif;base64,AA=="}}]}]},
    )
    assert unsupported_mime.status_code == 422

    too_large_image = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAAAAAAAAAAAAAA"}}]}]},
    )
    assert too_large_image.status_code == 422


    oversized_dimensions = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAACAAAAAgACAIAAAAAAAAA"}}]}]},
    )
    assert oversized_dimensions.status_code == 422

    too_many = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 2048},
    )
    assert too_many.status_code == 422


def test_gateway_rejects_invalid_upstream_response_schema():
    clients = FakeGatewayClients()
    clients.main_llm = FakeRuntimeClient({"object": "chat.completion", "model": "wrong", "choices": []})
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_forwards_risk_assessments_to_internal_risk_adapter():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/risk/assessments",
        headers=auth_headers(),
        json={"prompt": "hello"},
    )
    assert response.status_code == 200
    assert clients.risk_adapter.last_path == "/v1/risk/assessments"
    assert clients.risk_adapter.last_payload == {"prompt": "hello"}
    assert clients.risk_adapter.last_headers == {"authorization": "Bearer internal-test-key"}


def test_gateway_preserves_detector_disabled_from_risk_adapter():
    clients = FakeGatewayClients()

    def disabled_response(_path, _payload, **_kwargs):
        raise ServiceError("DETECTOR_DISABLED", "Risk detector is not enabled: prompt", False, 410)

    clients.risk_adapter.post_response = disabled_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/risk/assessments",
        headers=auth_headers(),
        json={"prompt": "hello"},
    )

    assert response.status_code == 410
    body = response.json()
    assert body["error"]["code"] == "DETECTOR_DISABLED"
    assert body["error"]["retryable"] is False


def test_gateway_siren_retired_returns_410_without_forwarding():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/risk/detectors/siren/assessments",
        headers=auth_headers(),
        json={"prompt": "hello"},
    )
    assert response.status_code == 410
    body = response.json()
    assert body["error"]["code"] == "DETECTOR_RETIRED"
    assert body["error"]["retryable"] is False
    assert clients.risk_adapter.last_path is None  # never forwarded
    Draft202012Validator(error_schema()).validate(body)


def test_gateway_siren_retired_returns_410_with_empty_body():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/risk/detectors/siren/assessments",
        headers=auth_headers(),
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "DETECTOR_RETIRED"
    assert clients.risk_adapter.last_path is None


def test_gateway_validates_risk_payload_before_forwarding():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/risk/assessments",
        headers=auth_headers(),
        json={"prompt": "hello", "action": "block"},
    )
    assert response.status_code == 422
    assert clients.risk_adapter.last_path is None


def test_gateway_metrics_records_http_and_upstream_counts():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    response = client.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    metrics = response.text
    assert not metrics.rstrip().endswith("# EOF")
    assert 'http_requests_total{route="/v1/chat/completions",service="gateway",status_code="200"}' in metrics
    assert 'upstream_request_duration_seconds_count{path="chat/completions",service="gateway",target="local-main"}' in metrics


def test_gateway_error_uses_incoming_request_id():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/models", headers={"x-request-id": "req_from_client"})
    assert response.status_code == 401
    assert response.json()["error"]["request_id"] == "req_from_client"



def test_gateway_unhandled_exception_uses_common_error_schema():
    client = TestClient(create_gateway_app(settings(), ExplodingGatewayClients()), raise_server_exceptions=False)
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    Draft202012Validator(error_schema()).validate(response.json())


def test_gateway_rejects_oversized_request_body():
    cfg = settings()
    cfg = AppSettings(
        app_env=cfg.app_env,
        project_version=cfg.project_version,
        security=cfg.security,
        gateway_timeout_seconds=cfg.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=cfg.risk_adapter_timeout_seconds,
        main_llm=cfg.main_llm,
        embedding=cfg.embedding,
        risk_prompt=cfg.risk_prompt,
        risk_adapter_base_url=cfg.risk_adapter_base_url,
        max_request_body_bytes=32,
        public_models=cfg.public_models,
    )
    client = TestClient(create_gateway_app(cfg, FakeGatewayClients()))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "x" * 100}]},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    Draft202012Validator(error_schema()).validate(response.json())


def test_gateway_records_safe_validation_rejection_metric_for_image_errors():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]},
    )
    metrics = client.get("/metrics").text
    assert 'request_validation_rejections_total{reason="image_input",service="gateway"}' in metrics


def test_gateway_accepts_streaming_and_rejects_tool_calling_contracts():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    streaming = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert streaming.status_code == 200
    assert streaming.headers["content-type"].startswith("text/event-stream")
    assert streaming.headers["cache-control"] == "no-cache"
    assert streaming.headers["x-accel-buffering"] == "no"
    body = streaming.content.decode()
    assert "data: [DONE]" in body
    assert clients.main_llm.last_path == "chat/completions"
    assert clients.main_llm.last_payload["stream"] is True
    assert clients.main_llm.last_payload["stream_options"] == {"include_usage": True}

    clients.main_llm.last_path = None
    streaming_string = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": "true", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert streaming_string.status_code == 422
    assert clients.main_llm.last_path is None

    invalid_stream_options = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "stream_options": {"include_usage": "yes"}, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert invalid_stream_options.status_code == 422
    assert clients.main_llm.last_path is None

    stream_options_without_stream = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert stream_options_without_stream.status_code == 422
    assert clients.main_llm.last_path is None

    streaming_false = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": False, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert streaming_false.status_code == 200
    clients.main_llm.last_path = None

    tools = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "tools": [], "messages": [{"role": "user", "content": "hello"}]},
    )
    assert tools.status_code == 422
    assert clients.main_llm.last_path is None

    tool_role = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "tool", "content": "hello"}]},
    )
    assert tool_role.status_code == 422
    assert clients.main_llm.last_path is None

    tool_calls = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "assistant", "content": "hello", "tool_calls": []}]},
    )
    assert tool_calls.status_code == 422
    assert clients.main_llm.last_path is None


def test_gateway_streaming_reports_usage_and_chunk_metrics():
    clients = FakeGatewayClients()
    clients.main_llm.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
        b'data: [DONE]\n\n',
    ]
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert '"usage"' in response.content.decode()

    metrics = client.get("/metrics").text
    assert 'streaming_chunks_total{service="gateway",target="local-main"}' in metrics
    assert 'streaming_bytes_total{service="gateway",target="local-main"}' in metrics
    assert 'streaming_usage_events_total{service="gateway",target="local-main"} 1.0' in metrics


def test_gateway_streaming_emits_sse_error_before_first_chunk():
    clients = FakeGatewayClients()
    clients.main_llm = StreamingErrorRuntimeClient(fail_after_first_chunk=False)
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "event: error" in body
    assert "UPSTREAM_TIMEOUT" in body
    assert "data: [DONE]" in body

    metrics = client.get("/metrics").text
    assert 'streaming_errors_total{code="UPSTREAM_TIMEOUT",phase="before_first_chunk",service="gateway",target="local-main"} 1.0' in metrics


def test_gateway_streaming_emits_sse_error_after_partial_chunk():
    clients = FakeGatewayClients()
    clients.main_llm = StreamingErrorRuntimeClient(fail_after_first_chunk=True)
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "partial" in body
    assert "event: error" in body
    assert "data: [DONE]" in body

    metrics = client.get("/metrics").text
    assert 'streaming_errors_total{code="UPSTREAM_TIMEOUT",phase="mid_stream",service="gateway",target="local-main"} 1.0' in metrics



def test_gateway_accepts_bounded_tool_calling_when_enabled():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(tool_calling_settings(), clients))
    payload = {
        "model": "local-main",
        "messages": [{"role": "user", "content": "서울 날씨를 확인해줘"}],
        "max_tokens": 32,
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 40,
        "min_p": 0.0,
        "repetition_penalty": 1.05,
        "seed": 7,
        "n": 1,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather by city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=payload)
    assert response.status_code == 200
    assert clients.main_llm.last_payload["tools"][0]["function"]["name"] == "get_weather"

    parallel = dict(payload)
    parallel["parallel_tool_calls"] = True
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=parallel)
    assert response.status_code == 422

    multi_choice = dict(payload)
    multi_choice["n"] = 2
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=multi_choice)
    assert response.status_code == 422

    unknown = dict(payload)
    unknown["unknown_sampler"] = 1
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=unknown)
    assert response.status_code == 422

    message_unknown = dict(payload)
    message_unknown["messages"] = [{"role": "user", "content": "hello", "cache_hint": "unsafe"}]
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=message_unknown)
    assert response.status_code == 422

    part_unknown = dict(payload)
    part_unknown["messages"] = [{"role": "user", "content": [{"type": "text", "text": "hello", "extra": True}]}]
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=part_unknown)
    assert response.status_code == 422

    tool_unknown = dict(payload)
    tool_unknown["tools"] = [{"type": "function", "function": {"name": "get_weather", "x-extra": 1}}]
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=tool_unknown)
    assert response.status_code == 422

    choice_without_tools = dict(payload)
    choice_without_tools.pop("tools")
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=choice_without_tools)
    assert response.status_code == 422

    choice_mismatch = dict(payload)
    choice_mismatch["tool_choice"] = {"type": "function", "function": {"name": "lookup_stock"}}
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=choice_mismatch)
    assert response.status_code == 422


def test_gateway_maps_reasoning_opt_in_to_vllm_chat_template_kwargs():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(tool_calling_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "분석해줘"}],
            "reasoning": True,
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    assert "reasoning" not in clients.main_llm.last_payload
    assert clients.main_llm.last_payload["chat_template_kwargs"] == {"enable_thinking": True}

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning": False,
        },
    )
    assert response.status_code == 200
    assert "reasoning" not in clients.main_llm.last_payload
    assert "chat_template_kwargs" not in clients.main_llm.last_payload

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning": "yes",
        },
    )
    assert response.status_code == 422


def test_gateway_accepts_upstream_tool_call_response_schema():
    clients = FakeGatewayClients()
    clients.main_llm = FakeRuntimeClient({
        "id": "chatcmpl_tool",
        "object": "chat.completion",
        "created": 1,
        "model": "local-main",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_weather",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": "{\"city\":\"Seoul\"}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    })
    client = TestClient(create_gateway_app(tool_calling_settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "서울 날씨"}],
            "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
            "tool_choice": "auto",
        },
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"


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


def test_gateway_response_format_text_and_json_object_contracts():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    text = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "response_format": {"type": "text"}},
    )
    assert text.status_code == 200
    assert clients.main_llm.last_payload["response_format"] == {"type": "text"}

    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":\"ok\"}"
    json_object = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": {"type": "json_object"}},
    )
    assert json_object.status_code == 200
    assert clients.main_llm.last_payload["response_format"] == {"type": "json_object"}

    missing_instruction = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "response_format": {"type": "json_object"}},
    )
    assert missing_instruction.status_code == 422

    clients.main_llm.post_response["choices"][0]["message"]["content"] = "not json"
    invalid = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": {"type": "json_object"}},
    )
    assert invalid.status_code == 502
    assert invalid.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_json_schema_validation_and_subset_limits():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":\"ok\"}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    valid = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format()},
    )
    assert valid.status_code == 200

    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":1}"
    invalid_response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format()},
    )
    assert invalid_response.status_code == 502
    assert invalid_response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"

    bad_name = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format(name="bad name")},
    )
    assert bad_name.status_code == 422

    disallowed = _json_schema_format({"type": "object", "additionalProperties": False, "allOf": []})
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": disallowed}).status_code == 422

    root_anyof = _json_schema_format({"type": "object", "additionalProperties": False, "anyOf": []})
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": root_anyof}).status_code == 422

    nested_anyof = _json_schema_format(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "number"}]}},
            "required": ["value"],
        }
    )
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"value\":\"ok\"}"
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": nested_anyof}).status_code == 200

    missing_additional = _json_schema_format({"type": "object", "properties": {"answer": {"type": "string"}}})
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": missing_additional}).status_code == 422


def test_gateway_rejects_invalid_json_schema_request_shapes_and_requires_all_properties():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":\"ok\",\"note\":null}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    def post_schema(schema: dict) -> int:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(schema),
            },
        )
        return response.status_code

    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": "string"}},
        "required": "a",
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": [],
        "required": [],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}, "note": {"type": "string"}},
        "required": ["answer"],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
        "required": ["answer", "extra"],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
        "required": ["answer", 1],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string"},
            "note": {"type": ["string", "null"]},
        },
        "required": ["answer", "note"],
    }) == 200


def test_gateway_allows_recursive_local_json_schema_refs():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = json.dumps(
        {"root": {"name": "parent", "children": [{"name": "child", "children": []}]}},
        separators=(",", ":"),
    )
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    recursive_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"root": {"$ref": "#/$defs/node"}},
        "required": ["root"],
        "$defs": {
            "node": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/node"},
                    },
                },
                "required": ["name", "children"],
            }
        },
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(recursive_schema),
        },
    )

    assert response.status_code == 200


def test_gateway_allows_direct_local_self_ref_without_request_rejection():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"self\":null}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    self_ref_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "self": {
                "anyOf": [
                    {"$ref": "#"},
                    {"type": "null"},
                ]
            }
        },
        "required": ["self"],
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(self_ref_schema),
        },
    )

    assert response.status_code == 200


def test_gateway_rejects_external_json_schema_refs():
    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))
    rejected_refs = [
        "https://example.com/schema.json",
        "http://example.com/schema.json",
        "file:///tmp/schema.json",
        "schema.json",
        "/tmp/schema.json",
        123,
    ]

    for ref in rejected_refs:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"external": {"$ref": ref}},
                        "required": ["external"],
                    }
                ),
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "local $ref" in body["error"]["message"]


def test_gateway_rejects_advanced_json_schema_reference_keywords():
    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))

    cases = [
        (
            "$dynamicRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$dynamicRef": "https://example.com/schema.json"}},
                "required": ["x"],
            },
        ),
        (
            "$dynamicRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$dynamicRef": "#/$defs/x"}},
                "required": ["x"],
                "$defs": {"x": {"type": "string"}},
            },
        ),
        (
            "$recursiveRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$recursiveRef": "https://example.com/schema.json"}},
                "required": ["x"],
            },
        ),
        (
            "$recursiveRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$recursiveRef": "#"}},
                "required": ["x"],
            },
        ),
        (
            "$dynamicAnchor",
            {
                "type": "object",
                "additionalProperties": False,
                "$dynamicAnchor": "root",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        ),
        (
            "$recursiveAnchor",
            {
                "type": "object",
                "additionalProperties": False,
                "$recursiveAnchor": True,
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        ),
        (
            "$id",
            {
                "type": "object",
                "additionalProperties": False,
                "$id": "https://example.com/root",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        ),
        (
            "$anchor",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"type": "string", "$anchor": "x"}},
                "required": ["x"],
            },
        ),
    ]

    for keyword, schema in cases:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(schema),
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert keyword in body["error"]["message"]


def test_gateway_allows_schema_annotation_defs_and_non_recursive_local_ref():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"x\":\"ok\"}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"$ref": "#/$defs/value"}},
        "required": ["x"],
        "$defs": {"value": {"type": "string"}},
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(schema),
        },
    )

    assert response.status_code == 200


def test_gateway_allows_disallowed_keyword_names_as_json_property_names():
    cases = [
        ("$id", {"$id": "value"}),
        ("not", {"not": "value"}),
        ("$dynamicRef", {"$dynamicRef": "value"}),
    ]

    for property_name, response_body in cases:
        clients = FakeGatewayClients()
        clients.main_llm.post_response["choices"][0]["message"]["content"] = json.dumps(response_body, separators=(",", ":"))
        client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {property_name: {"type": "string"}},
            "required": [property_name],
        }

        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(schema),
            },
        )

        assert response.status_code == 200


def test_gateway_allows_disallowed_keyword_names_as_defs_names():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"x\":\"value\"}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"$ref": "#/$defs/$id"}},
        "required": ["x"],
        "$defs": {"$id": {"type": "string"}},
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(schema),
        },
    )

    assert response.status_code == 200


def test_gateway_still_rejects_disallowed_keywords_inside_property_schemas():
    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))
    cases = [
        ("$id", {"type": "string", "$id": "https://example.com/nested"}),
        ("$anchor", {"type": "string", "$anchor": "x"}),
        ("$dynamicRef", {"$dynamicRef": "#/$defs/x"}),
        ("not", {"not": {"type": "string"}}),
        ("$ref", {"$ref": "https://example.com/schema.json"}),
    ]

    for keyword, property_schema in cases:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"x": property_schema},
                        "required": ["x"],
                    }
                ),
            },
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        if keyword == "$ref":
            assert "local $ref" in body["error"]["message"]
        else:
            assert keyword in body["error"]["message"]


def test_chat_response_validation_defensively_maps_invalid_expectation_schema():
    try:
        validate_chat_response(
            {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 1,
                "model": "local-main",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{\"answer\":\"ok\"}"}, "finish_reason": "stop"}],
            },
            expected_model="local-main",
            expectations=ChatResponseExpectations(
                response_format_type="json_schema",
                json_schema={"type": "object", "additionalProperties": False, "properties": [], "required": []},
                expect_logprobs=False,
                stream=False,
            ),
        )
    except ServiceError as exc:
        assert exc.code == "UPSTREAM_SCHEMA_ERROR"
        assert exc.status_code == 502
    else:
        raise AssertionError("invalid expectation schema should be mapped to UPSTREAM_SCHEMA_ERROR")


def test_chat_response_validation_defensively_maps_reference_resolution_errors():
    try:
        validate_chat_response(
            {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 1,
                "model": "local-main",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{\"answer\":\"ok\"}"}, "finish_reason": "stop"}],
            },
            expected_model="local-main",
            expectations=ChatResponseExpectations(
                response_format_type="json_schema",
                json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"answer": {"$ref": "#/$defs/missing"}},
                    "required": ["answer"],
                },
                expect_logprobs=False,
                stream=False,
            ),
        )
    except ServiceError as exc:
        assert exc.code == "UPSTREAM_SCHEMA_ERROR"
        assert exc.status_code == 502
        assert "could not be validated" in exc.message
    else:
        raise AssertionError("reference resolution errors should be mapped to UPSTREAM_SCHEMA_ERROR")


def test_gateway_logprobs_logit_bias_and_stream_contracts():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["logprobs"] = {
        "content": [{"token": "ok", "logprob": -0.1, "bytes": [111, 107], "top_logprobs": [{"token": "ok", "logprob": -0.1, "bytes": [111, 107]}]}],
        "refusal": None,
    }
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "logprobs": True, "top_logprobs": 2},
    )
    assert response.status_code == 200
    assert clients.main_llm.last_payload["logprobs"] is True
    assert clients.main_llm.last_payload["top_logprobs"] == 2

    for payload in [
        {"top_logprobs": 1},
        {"logprobs": False, "top_logprobs": 0},
        {"logprobs": True, "top_logprobs": 11},
    ]:
        invalid = client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], **payload})
        assert invalid.status_code == 422

    valid_bias = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "logit_bias": {"42": -1.5}},
    )
    assert valid_bias.status_code == 200
    assert clients.main_llm.last_payload["logit_bias"] == {"42": -1.5}

    for bias in [{"x": 1}, {"1": 101}, {"1": True}, {str(i): 0 for i in range(257)}]:
        invalid = client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "logit_bias": bias})
        assert invalid.status_code == 422

    clients.main_llm.stream_chunks = [b'data: {"choices":[{"delta":{"content":"ok"},"logprobs":{"content":[]}}]}\n\n', b"data: [DONE]\n\n"]
    stream = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}], "logprobs": True},
    )
    assert stream.status_code == 200
    assert '"logprobs"' in stream.content.decode()


def test_gateway_allows_advanced_combinations_and_models_projection():
    clients = FakeGatewayClients()
    clients.main_llm.post_response = {
        "id": "chatcmpl_tool",
        "object": "chat.completion",
        "created": 1,
        "model": "local-main",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
    }
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    base = {
        "model": "local-main",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": _json_schema_format(),
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
        "tool_choice": "auto",
    }
    assert client.post("/v1/chat/completions", headers=auth_headers(), json=base).status_code == 200
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={**base, "reasoning": True}).status_code == 200
    assert "reasoning" not in clients.main_llm.last_payload
    assert clients.main_llm.last_payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={**base, "logit_bias": {"42": 1}}).status_code == 200

    models = client.get("/v1/models", headers=auth_headers()).json()["data"][0]["request_parameters"]
    assert {"response_format", "logprobs", "top_logprobs", "logit_bias"}.issubset(models)

def test_gateway_optionally_protects_admin_endpoints():
    cfg = settings()
    cfg = AppSettings(
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
    client = TestClient(create_gateway_app(cfg, FakeGatewayClients()))

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 401
    assert client.get("/metrics").status_code == 401
    assert client.get("/ready", headers=auth_headers()).status_code == 401
    assert client.get("/metrics", headers=auth_headers()).status_code == 401
    admin_headers = {"Authorization": "Bearer admin-test-key"}
    assert client.get("/ready", headers=admin_headers).status_code == 200
    assert client.get("/metrics", headers=admin_headers).status_code == 200


def test_gateway_rejects_invalid_internal_risk_response_schema():
    clients = FakeGatewayClients()
    clients.risk_adapter = FakeRuntimeClient({
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
        "decision": "allow",
    })
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "hello"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"
    Draft202012Validator(error_schema()).validate(response.json())


def test_gateway_rerank_top_n_limits_results():
    """rerank top_n이 결과 개수를 제한하는지 확인한다."""
    clients = FakeGatewayClients()
    vectors = {
        "query: 쿼리": [1.0] + [0.0] * 1023,
        "d0": [0.1, 0.995] + [0.0] * 1022,
        "d1": [1.0] + [0.0] * 1023,
        "d2": [0.5, 0.866] + [0.0] * 1022,
    }
    clients.embedding_ko.post_response = lambda _path, payload, **_kwargs: {
        "object": "list",
        "model": "local-embed-ko",
        "data": [{"object": "embedding", "embedding": vectors[text], "index": index} for index, text in enumerate(payload["input"])],
    }
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/retrieval/rerank",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "query": "쿼리", "documents": ["d0", "d1", "d2"], "top_n": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 2
    assert [item["index"] for item in body["results"]] == [1, 2]


def test_gateway_score_rejects_top_n():
    """score endpoint에서 top_n을 보내면 422를 반환한다."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "query": "쿼리", "documents": ["문서"], "top_n": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert clients.embedding_ko.last_path is None


# ---------------------------------------------------------------------------
# Embedding user field acceptance tests (P2)
# ---------------------------------------------------------------------------

def test_gateway_embeddings_accepts_user_field():
    """production policy 기반 — user 필드는 허용되지만 upstream으로 전달되지 않는다."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(_settings_with_embedding_policy(_PRODUCTION_EMBEDDING_POLICY), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "user": "test-user-id"},
    )
    assert response.status_code == 200
    assert clients.embedding.last_path == "embeddings"
    assert "user" not in clients.embedding.last_payload


def test_gateway_embeddings_user_field_not_sent_upstream():
    """user 필드는 allow_unlisted=false 환경에서도 허용되고 upstream에서 제거된다."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(_settings_with_embedding_policy(_PRODUCTION_EMBEDDING_POLICY), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": "hello", "user": "abc"},
    )
    assert response.status_code == 200
    assert clients.embedding.last_payload.get("user") is None
    assert clients.embedding.last_payload.get("input") == "hello"


def test_gateway_embeddings_rejects_unsupported_encoding_format():
    """base64 encoding_format은 422를 반환한다 (smoke 기준 미지원)."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "encoding_format": "base64"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert clients.embedding.last_path is None
