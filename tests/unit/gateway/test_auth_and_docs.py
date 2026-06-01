from __future__ import annotations

from .helpers import *  # noqa: F401,F403

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

