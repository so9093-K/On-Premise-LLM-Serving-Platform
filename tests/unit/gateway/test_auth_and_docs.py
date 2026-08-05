"""gateway의 인증(bearer token 필수 여부)과 FastAPI 문서(/docs, /redoc, /openapi.json)
노출 정책을 검증한다: public/admin 엔드포인트별 보안 스키마가 실제로 다른지 확인한다."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gateway_requires_bearer_auth():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/models")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    Draft202012Validator(error_schema()).validate(response.json())
    assert response.json()["error"]["request_id"].startswith("req_")


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
