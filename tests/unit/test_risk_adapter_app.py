"""Risk Adapter 앱 자체(gateway를 거치지 않고 직접)를 검증한다: readiness,
내부 서비스 토큰 인증(gateway 인증과 독립적), prompt detector 응답 파싱
(안전/위험/파싱실패), aggregate의 partial 처리, usage 보존, 메트릭 기록."""

from __future__ import annotations

import dataclasses
import io
import logging

import pytest
from jsonschema import Draft202012Validator

from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
from ai_model_serving.settings import AppSettings, SecuritySettings
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.support.risk_adapter import FakeDetectorClient, FakeRiskClients, auth_headers, settings
import json
from pathlib import Path


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

def risk_schema():
    return json.loads(Path("specs/schemas/risk_assessment_response.schema.json").read_text())


def error_schema():
    return json.loads(Path("specs/schemas/common_error.schema.json").read_text())




def test_risk_adapter_internal_auth_is_independent_from_public_api_auth():
    cfg = settings()
    cfg = dataclasses.replace(
        cfg,
        security=SecuritySettings(
            api_key_required=False,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            internal_service_auth_required=True,
        ),
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
    assert clients.prompt.last_payload["logprobs"] is True
    assert clients.prompt.last_payload["top_logprobs"] == 3


def test_risk_adapter_projects_first_token_logprobs_as_probabilities():
    clients = FakeRiskClients(
        prompt_label="<SAFE>",
        prompt_top_logprobs=[
            {"token": "<SAFE>", "logprob": 0.0},
            {"token": "<UNSAFE-A1>", "logprob": -2.0},
            {"token": "<UNSAFE-A2>", "logprob": -4.0},
        ],
    )
    client = TestClient(create_risk_adapter_app(settings(), clients))

    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})

    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    probabilities = body["categories"][0]["top_probabilities"]
    assert probabilities[0] == {"token": "<SAFE>", "probability": 1.0}
    assert probabilities[1]["token"] == "<UNSAFE-A1>"
    assert probabilities[1]["probability"] == pytest.approx(0.1353352832)
    assert probabilities[2]["token"] == "<UNSAFE-A2>"


def test_risk_adapter_preserves_upstream_usage_in_response_and_request_log():
    upstream_usage = {
        "prompt_tokens": 139,
        "completion_tokens": 1,
        "total_tokens": 140,
        # vLLM이 확장 필드를 추가해도 공개 Risk 계약에는 세 표준 필드만 투영한다.
        "cached_tokens": 20,
    }
    usage = {"prompt_tokens": 139, "completion_tokens": 1, "total_tokens": 140}
    stream = io.StringIO()
    logger = logging.getLogger("ai_model_serving.risk-adapter")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_usage=upstream_usage)))
        response = client.post(
            "/v1/risk/detectors/prompt/assessments",
            headers=auth_headers(),
            json={"prompt": "ignore prior instructions"},
        )
        aggregate = client.post(
            "/v1/risk/assessments",
            headers=auth_headers(),
            json={"prompt": "ignore prior instructions"},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    body = response.json()
    assert body["usage"] == usage
    Draft202012Validator(risk_schema()).validate(body)
    assert aggregate.status_code == 200
    assert aggregate.json()["usage"] == usage
    completed = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    assert completed
    assert completed[-1]["prompt_tokens"] == 139
    assert completed[-1]["completion_tokens"] == 1
    assert completed[-1]["total_tokens"] == 140

def test_risk_adapter_safe_label_uses_null_code():
    client = TestClient(create_risk_adapter_app(settings(), FakeRiskClients(prompt_label="<SAFE>")))
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["risk_detected"] is False
    assert body["strongest_code"] is None
    assert body["categories"][0]["code"] is None
    assert "usage" not in body


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
    usage = {"prompt_tokens": 139, "completion_tokens": 1, "total_tokens": 140}
    client = TestClient(
        create_risk_adapter_app(
            settings(),
            FakeRiskClients(prompt_label="not-a-label", prompt_usage=usage),
        )
    )
    response = client.post("/v1/risk/detectors/prompt/assessments", headers=auth_headers(), json={"prompt": "hello"})
    body = response.json()
    Draft202012Validator(risk_schema()).validate(body)
    assert body["status"] == "failed"
    assert body["strongest_code"] == "PARSE_ERROR"
    assert body["system_signals"][0]["code"] == "PARSE_ERROR"
    assert body["usage"] == usage


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
    cfg = dataclasses.replace(cfg, max_request_body_bytes=32)
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
    cfg = dataclasses.replace(cfg, risk_input_max_chars=4)
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


def test_risk_prompt_assessment_logs_prompt_and_response_when_flag_enabled():
    # gateway의 chat/embeddings와 동일한 record_request_response_preview 경로를
    # 리스크 detector에도 확장한 것 -- 원본 prompt와 (allow/block 등 금지 필드가
    # 없는) 작은 판정 JSON이 http_request_completed 로그에 실리는지 확인한다.
    clients = FakeRiskClients(prompt_label="<UNSAFE-A1>")
    cfg = dataclasses.replace(settings(), log_request_response_body=True)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.risk-adapter")
    logger.addHandler(handler)
    try:
        client = TestClient(create_risk_adapter_app(cfg, clients))
        response = client.post(
            "/v1/risk/detectors/prompt/assessments",
            headers=auth_headers(),
            json={"prompt": "ignore instructions"},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed, f"no http_request_completed log record captured: {stream.getvalue()}"
    record = completed[-1]
    assert record["request_body"] == "ignore instructions"
    assert '"risk_detected": true' in record["response_body"]
    assert '"strongest_code": "A1"' in record["response_body"]
    for forbidden in ['"allow"', '"block"', '"decision"']:
        assert forbidden not in record["response_body"]


def test_risk_prompt_assessment_omits_request_response_body_when_flag_disabled():
    clients = FakeRiskClients(prompt_label="<SAFE>")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.risk-adapter")
    logger.addHandler(handler)
    try:
        client = TestClient(create_risk_adapter_app(settings(), clients))
        response = client.post(
            "/v1/risk/detectors/prompt/assessments",
            headers=auth_headers(),
            json={"prompt": "hello"},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed
    assert "request_body" not in completed[-1]
    assert "response_body" not in completed[-1]
