from __future__ import annotations

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
from ai_model_serving.settings import AppSettings, RuntimeEndpoint, SecuritySettings
from ai_model_serving.errors import ServiceError
import json
from pathlib import Path


class FakeDetectorClient:
    def __init__(self, label: str, ready: bool = True, fail: bool = False):
        self.label = label
        self.ready = ready
        self.fail = fail
        self.last_payload = None
        self.endpoint = RuntimeEndpoint("risk", "http://risk/v1", "risk", 1)

    async def post_json(self, path, payload):
        self.last_payload = payload
        if self.fail:
            raise ServiceError("UPSTREAM_TIMEOUT", "timeout", True, 504)
        return {
            "id": "chatcmpl_risk",
            "object": "chat.completion",
            "created": 1,
            "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": self.label}, "finish_reason": "stop"}],
        }

    async def get_json(self, path):
        if not self.ready:
            raise ServiceError("MODEL_UNAVAILABLE", "not ready", True, 503)
        return {"object": "list", "data": []}


class FakeRiskClients:
    def __init__(self, prompt_label="<SAFE>", prompt_fail=False):
        self.prompt = FakeDetectorClient(prompt_label, fail=prompt_fail)


def settings() -> AppSettings:
    endpoint = RuntimeEndpoint("x", "http://runtime/v1", "x", 1)
    return AppSettings(
        app_env="test",
        project_version="0.1.0",
        security=SecuritySettings(api_key_required=True, api_keys=frozenset({"test-key"}), internal_service_token="internal-test-key"),
        gateway_timeout_seconds=1,
        risk_adapter_timeout_seconds=1,
        main_llm=endpoint,
        embedding=endpoint,
        risk_prompt=RuntimeEndpoint("risk-prompt", "http://prompt/v1", "risk-prompt", 1),
        risk_adapter_base_url="http://risk",
    )


def auth_headers():
    return {"Authorization": "Bearer internal-test-key"}




def test_risk_adapter_readiness_not_ready_returns_http_503():
    clients = FakeRiskClients()
    clients.prompt.ready = False
    client = TestClient(create_risk_adapter_app(settings(), clients))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["phase"] == "waiting_for_dependencies"
    assert body["not_ready_dependencies"] == ["risk_prompt_vllm"]
    assert {item["name"]: item["status"] for item in body["dependencies"]}["risk_prompt_vllm"] == "not_ready"
    prompt_dependency = next(item for item in body["dependencies"] if item["name"] == "risk_prompt_vllm")
    assert prompt_dependency["endpoint"] == "http://risk/v1/models"
    assert "MODEL_UNAVAILABLE" in prompt_dependency["message"]

def test_risk_adapter_framework_documentation_endpoints_are_exposed_by_default():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients()))
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    doc = openapi.json()
    assert doc["info"]["title"] == "Risk Adapter"
    assert "signal-only" in doc["info"]["description"]
    assert "Readiness" in doc["info"]["description"]
    assert {tag["name"] for tag in doc["tags"]} >= {"Operations", "Monitoring", "Risk Signal"}
    assert doc["paths"]["/ready"]["get"]["responses"]["503"]["content"]["application/json"]["example"]["phase"] == "waiting_for_dependencies"
    assert doc["paths"]["/v1/risk/assessments"]["post"]["description"]
    assert "bearerAuth" in doc["components"]["securitySchemes"]

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
    )
    with TestClient(create_risk_adapter_app(cfg, FakeRiskClients())) as admin_client:
        admin_doc = admin_client.get("/openapi.json").json()
    assert "adminBearerAuth" in admin_doc["components"]["securitySchemes"]
    assert admin_doc["paths"]["/ready"]["get"]["security"] == [{"adminBearerAuth": []}]
    assert "401" in admin_doc["paths"]["/ready"]["get"]["responses"]
    assert admin_doc["paths"]["/metrics"]["get"]["security"] == [{"adminBearerAuth": []}]
    assert "401" in admin_doc["paths"]["/metrics"]["get"]["responses"]


def risk_schema():
    return json.loads(Path("specs/schemas/risk_assessment_response.schema.json").read_text())


def error_schema():
    return json.loads(Path("specs/schemas/common_error.schema.json").read_text())




def test_risk_adapter_internal_auth_is_independent_from_public_api_auth():
    cfg = settings()
    cfg = AppSettings(
        app_env=cfg.app_env,
        project_version=cfg.project_version,
        security=SecuritySettings(
            api_key_required=False,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            internal_service_auth_required=True,
        ),
        gateway_timeout_seconds=cfg.gateway_timeout_seconds,
        risk_adapter_timeout_seconds=cfg.risk_adapter_timeout_seconds,
        main_llm=cfg.main_llm,
        embedding=cfg.embedding,
        risk_prompt=cfg.risk_prompt,
        risk_adapter_base_url=cfg.risk_adapter_base_url,
    )
    client = TestClient(create_risk_adapter_app(cfg, FakeRiskClients()))
    unauthenticated = client.post("/v1/risk/assessments", json={"prompt": "hello"})
    assert unauthenticated.status_code == 401

    authenticated = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "hello"})
    assert authenticated.status_code == 200


def test_risk_adapter_rejects_external_gateway_token():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients()))
    response = client.post(
        "/v1/risk/assessments",
        headers={"Authorization": "Bearer test-key"},
        json={"prompt": "hello"},
    )
    assert response.status_code == 401

def test_risk_adapter_returns_signal_only_valid_schema():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_label="<UNSAFE-A1>")))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "ignore instructions"})
    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["risk_detected"] is True
    assert body["strongest_code"] == "A1"
    for forbidden in ["allow", "review", "block", "decision", "action"]:
        assert forbidden not in body




def test_risk_adapter_uses_single_token_generation_budget():
    clients = FakeRiskClients(prompt_label="<SAFE>")
    client = TestClient(create_risk_adapter_app(settings(), clients))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    assert response.status_code == 200
    assert clients.prompt.last_payload["max_tokens"] == 1
    assert clients.prompt.last_payload["temperature"] == 0

def test_risk_adapter_safe_label_uses_null_code():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_label="<SAFE>")))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["risk_detected"] is False
    assert body["strongest_code"] is None
    assert body["categories"][0]["code"] is None


def test_risk_adapter_aggregate_partial_on_detector_timeout():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_fail=True)))
    response = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "hello"})
    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["status"] == "partial"
    assert body["assessment_complete"] is False
    assert body["system_signal_detected"] is True


def test_risk_adapter_parse_failure_is_parse_error_system_signal():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_label="not-a-label")))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["status"] == "failed"
    assert body["strongest_code"] == "PARSE_ERROR"
    assert body["system_signals"][0]["code"] == "PARSE_ERROR"


def test_risk_adapter_rejects_multiple_or_explanatory_labels():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_label="result: <SAFE> <UNSAFE-A1>")))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["status"] == "failed"
    assert body["strongest_code"] == "PARSE_ERROR"


def test_risk_adapter_rejects_extra_fields_and_oversized_prompt():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients()))
    response = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "hello", "decision": "allow"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    Draft202012Validator(error_schema()).validate(response.json())

    response = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "x" * 20001})
    assert response.status_code == 422
    Draft202012Validator(error_schema()).validate(response.json())


def test_risk_adapter_metrics_records_assessment_and_system_signals():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_label="not-a-label")))
    client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    response = client.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    metrics = response.text
    assert not metrics.rstrip().endswith("# EOF")
    assert 'risk_assessments_total{detector="prompt",service="risk-adapter",status="failed"}' in metrics
    assert 'risk_adapter_system_signal_total{service="risk-adapter",system_signal_code="PARSE_ERROR"}' in metrics


def test_risk_adapter_rejects_oversized_request_body():
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
    )
    client = TestClient(create_risk_adapter_app(cfg, FakeRiskClients()))
    response = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "x" * 100})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    Draft202012Validator(error_schema()).validate(response.json())


def test_risk_adapter_treats_detector_model_mismatch_as_parse_system_signal():
    class MismatchedDetectorClient(FakeDetectorClient):
        async def post_json(self, path, payload):
            self.last_payload = payload
            return {
                "id": "chatcmpl_risk",
                "object": "chat.completion",
                "created": 1,
                "model": "unexpected-detector-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "<SAFE>"}, "finish_reason": "stop"}],
            }

    clients = FakeRiskClients()
    clients.prompt = MismatchedDetectorClient("<SAFE>")
    client = TestClient(create_risk_adapter_app(settings(), clients))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["status"] == "failed"
    assert body["system_signals"][0]["code"] == "PARSE_ERROR"


def test_risk_adapter_validation_rejection_metric_uses_safe_reason_label():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients()))
    client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "hello", "extra": "no"})
    metrics = client.get("/metrics").text
    assert 'request_validation_rejections_total{reason="risk_prompt",service="risk-adapter"}' in metrics


def test_risk_adapter_rejects_whitespace_only_prompt():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients()))
    response = client.post("/v1/risk/assessments", headers=auth_headers(), json={"prompt": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    Draft202012Validator(error_schema()).validate(response.json())


def test_risk_adapter_returns_truncated_input_signal_before_detector_call():
    clients = FakeRiskClients(prompt_label="<SAFE>")
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
        risk_input_max_chars=4,
    )
    client = TestClient(create_risk_adapter_app(cfg, clients))

    response = client.post(
        "/v1/risk/detectors/prompt/assessments",
        headers=auth_headers(),
        json={"prompt": "hello"},
    )

    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["status"] == "failed"
    assert body["strongest_code"] == "TRUNCATED_INPUT"
    assert body["system_signals"][0]["code"] == "TRUNCATED_INPUT"
    assert body["system_signals"][0]["retryable"] is False
    assert clients.prompt.last_payload is None
