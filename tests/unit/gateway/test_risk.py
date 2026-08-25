"""gateway가 risk-adapter로 A1(prompt_attack) 판정을 포워딩하는 경로를 검증한다:
로깅, 페이로드 전달, 런타임 중지 시 503, forbidden 필드/스키마 위반 거부."""

from __future__ import annotations

import dataclasses
import io
import json
import logging

from .helpers import *  # noqa: F401,F403
from ai_model_serving.services.runtime_state import RuntimeState


def test_gateway_risk_assessment_logs_prompt_and_response_when_flag_enabled():
    # 클라이언트가 실제로 때리는 건 risk-adapter 자체가 아니라 gateway의 프록시
    # 라우트(gateway_risk.py)다 -- Grafana Request Log Explorer에 service=gateway로
    # 찍히는 그 행. risk_adapter_risk.py(내부 전용 라우트)만 고치면 이 행엔
    # 여전히 반영이 안 되므로 별도로 검증한다.
    clients = FakeGatewayClients()
    cfg = dataclasses.replace(settings(), log_request_response_body=True)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(cfg, clients))
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
    assert '"assessment_id": "risk_1"' in record["response_body"]


def test_gateway_risk_assessment_omits_request_response_body_when_flag_disabled():
    clients = FakeGatewayClients()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(settings(), clients))
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


def test_gateway_risk_aggregate_returns_503_when_prompt_runtime_stopped():
    clients = FakeGatewayClients()
    asyncio.run(clients.runtime_state.set("risk_prompt", RuntimeState.stopped))
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/risk/assessments",
        headers=auth_headers(),
        json={"prompt": "hello"},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "MODEL_UNAVAILABLE"
    assert "risk_prompt runtime is stopped" in body["error"]["message"]
    assert clients.risk_adapter.last_path is None


def test_gateway_preserves_detector_disabled_from_risk_adapter():
    clients = FakeGatewayClients()

    def disabled_response(_path, _payload, **_kwargs):
        raise ServiceError("DETECTOR_DISABLED", "Risk detector is not enabled: prompt")

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
