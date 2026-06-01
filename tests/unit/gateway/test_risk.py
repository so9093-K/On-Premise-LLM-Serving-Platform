from __future__ import annotations

from .helpers import *  # noqa: F401,F403

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

