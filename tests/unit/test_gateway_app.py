from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai_model_serving.errors import ServiceError

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.settings import AppSettings, RuntimeEndpoint, SecuritySettings


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
            "capabilities": ["embeddings"],
            "request_parameters": {"dimensions": {"type": "integer", "enum": [768, 512, 256, 128]}},
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
        embedding=RuntimeEndpoint("local-embed", "http://embed/v1", "local-embed", 1),
        risk_prompt=endpoint,
        risk_adapter_base_url="http://risk",
        public_models=public_models(),
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
            ],
            "max_n": 1,
            "tool_calling": {"enabled": True, "max_tools": 4, "allow_parallel_tool_calls": False},
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
    assert set(by_id) == {"local-main", "local-embed", "risk-prompt"}
    assert by_id["local-main"]["request_parameters"]["temperature"] == {"type": "number", "min": 0, "max": 2}
    assert by_id["local-main"]["request_parameters"]["stream"] == {"type": "boolean"}
    assert by_id["local-embed"]["request_parameters"]["dimensions"]["enum"] == [768, 512, 256, 128]
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
    metrics = client.get("/metrics").text
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
