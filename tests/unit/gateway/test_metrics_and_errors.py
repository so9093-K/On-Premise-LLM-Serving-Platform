from __future__ import annotations

from .helpers import *  # noqa: F401,F403
from starlette.requests import Request
from ai_model_serving.logging_policy import safe_request_log_record

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


def test_access_log_records_client_ip_hash_without_metric_label_style_ip():
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "headers": [
            (b"x-request-id", b"req_123"),
            (b"x-forwarded-for", b"203.0.113.10, 10.0.0.1"),
            (b"x-forwarded-proto", b"https"),
        ],
        "client": ("10.0.0.10", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "query_string": b"",
        "path_params": {},
    })

    record = safe_request_log_record(
        service="gateway",
        request=request,
        status_code=200,
        elapsed_seconds=0.01234,
    )

    assert record["client_host"] == "10.0.0.10"
    assert record["client_ip_hash"]
    assert record["forwarded_for_present"] is True
    assert record["forwarded_proto"] == "https"
    assert "203.0.113.10" not in json.dumps(record)
