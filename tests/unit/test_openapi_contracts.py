from __future__ import annotations

from fastapi import FastAPI
import pytest

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
from ai_model_serving.openapi_contracts import install_contract_openapi, load_contract_schema

from tests.unit.gateway.helpers import FakeGatewayClients, settings as gateway_settings
from tests.unit.test_risk_adapter_app import FakeRiskClients, settings as risk_settings


def test_error_catalog_loading_fails_explicitly_when_required_catalog_is_missing(tmp_path, monkeypatch):
    import ai_model_serving.openapi_contracts as contracts

    monkeypatch.setattr(contracts, "find_project_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="error_catalog.yaml"):
        contracts._load_error_catalog()


def test_gateway_generated_openapi_uses_checked_in_request_contracts():
    app = create_gateway_app(gateway_settings(), FakeGatewayClients())
    document = app.openapi()

    chat_schema = document["paths"]["/v1/chat/completions"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    embedding_schema = document["paths"]["/v1/embeddings"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    risk_schema = document["paths"]["/v1/risk/assessments"]["post"]["requestBody"]["content"]["application/json"]["schema"]

    assert chat_schema == load_contract_schema("chat_completion_request.schema.json")
    assert chat_schema["additionalProperties"] is False
    assert chat_schema["required"] == ["model", "messages"]
    assert "messages" in chat_schema["properties"]

    assert embedding_schema == load_contract_schema("embedding_request.schema.json")
    assert embedding_schema["additionalProperties"] is False
    assert embedding_schema["required"] == ["model", "input"]

    assert risk_schema == load_contract_schema("risk_assessment_request.schema.json")
    assert risk_schema["additionalProperties"] is False
    assert risk_schema["required"] == ["prompt"]


def test_gateway_generated_openapi_uses_checked_in_response_contracts():
    app = create_gateway_app(gateway_settings(), FakeGatewayClients())
    document = app.openapi()

    models_schema = document["paths"]["/v1/models"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    chat_schema = document["paths"]["/v1/chat/completions"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    risk_schema = document["paths"]["/v1/risk/assessments"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert models_schema == load_contract_schema("model_list_response.schema.json")
    assert chat_schema == load_contract_schema("chat_completion_response.schema.json")
    assert risk_schema == load_contract_schema("risk_assessment_response.schema.json")
    assert "chat_completion_request.schema.json" not in str(chat_schema)


def test_risk_adapter_generated_openapi_uses_checked_in_risk_contracts():
    app = create_risk_adapter_app(risk_settings(), FakeRiskClients())
    document = app.openapi()

    request_schema = document["paths"]["/v1/risk/assessments"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    response_schema = document["paths"]["/v1/risk/assessments"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert request_schema == load_contract_schema("risk_assessment_request.schema.json")
    assert response_schema == load_contract_schema("risk_assessment_response.schema.json")
    assert request_schema["additionalProperties"] is False
    assert response_schema["additionalProperties"] is False


def test_contract_openapi_installer_preserves_existing_examples():
    app = FastAPI()

    @app.post("/example")
    async def example(payload: dict):
        return payload

    install_contract_openapi(
        app,
        request_schemas={("POST", "/example"): "risk_assessment_request.schema.json"},
        request_examples={("POST", "/example"): {"basic": {"summary": "Basic", "value": {"prompt": "hello"}}}},
    )

    request_body = app.openapi()["paths"]["/example"]["post"]["requestBody"]
    content = request_body["content"]["application/json"]
    assert request_body["required"] is True
    assert content["schema"] == load_contract_schema("risk_assessment_request.schema.json")
    assert content["examples"]["basic"]["value"] == {"prompt": "hello"}


def test_gateway_openapi_security_matches_effective_public_auth():
    from dataclasses import replace
    from ai_model_serving.settings import SecuritySettings

    cfg = gateway_settings()
    secured_doc = create_gateway_app(cfg, FakeGatewayClients()).openapi()
    assert secured_doc["paths"]["/v1/models"]["get"].get("security") == [{"bearerAuth": []}]

    open_cfg = replace(
        cfg,
        security=SecuritySettings(
            api_key_required=False,
            api_keys=frozenset(),
            internal_service_token="internal-test-key",
            auth_mode="local_open",
        ),
    )
    open_doc = create_gateway_app(open_cfg, FakeGatewayClients()).openapi()
    assert "security" not in open_doc["paths"]["/v1/models"]["get"]


def test_risk_adapter_openapi_security_matches_effective_internal_auth():
    from dataclasses import replace
    from ai_model_serving.settings import SecuritySettings

    cfg = risk_settings()
    secured_doc = create_risk_adapter_app(cfg, FakeRiskClients()).openapi()
    assert secured_doc["paths"]["/v1/risk/assessments"]["post"].get("security") == [{"bearerAuth": []}]

    open_cfg = replace(
        cfg,
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            internal_service_auth_required=False,
            auth_mode="local_open",
        ),
    )
    open_doc = create_risk_adapter_app(open_cfg, FakeRiskClients()).openapi()
    assert "security" not in open_doc["paths"]["/v1/risk/assessments"]["post"]


def test_generated_openapi_includes_static_error_response_surface():
    gateway_doc = create_gateway_app(gateway_settings(), FakeGatewayClients()).openapi()
    risk_doc = create_risk_adapter_app(risk_settings(), FakeRiskClients()).openapi()
    expected = {"401", "413", "422", "429", "500", "502", "503", "504"}
    for doc, paths in [
        (gateway_doc, ["/v1/chat/completions", "/v1/embeddings", "/v1/risk/assessments"]),
        (risk_doc, ["/v1/risk/detectors/prompt/assessments", "/v1/risk/assessments"]),
    ]:
        for path in paths:
            responses = doc["paths"][path]["post"]["responses"]
            assert expected.issubset(set(responses))
            assert responses["500"]["content"]["application/json"]["schema"]["title"] == "CommonErrorResponse"
