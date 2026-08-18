"""OpenAPI의 동적 인증·예시 보존과 오류 경계를 검증한다.

기본 request/response schema가 checked-in 계약과 일치하는지는 ``make validate``의
OpenAPI snapshot diff가 Gateway와 Risk Adapter 전체에 대해 검사한다.
"""

from __future__ import annotations

from fastapi import FastAPI
import pytest

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
from ai_model_serving.openapi_contracts import install_contract_openapi, load_contract_schema

from tests.unit.gateway.helpers import FakeGatewayClients, settings as gateway_settings
from tests.support.risk_adapter import FakeRiskClients, settings as risk_settings


def test_error_catalog_loading_fails_explicitly_when_required_catalog_is_missing(tmp_path, monkeypatch):
    import ai_model_serving.openapi_contracts as contracts

    monkeypatch.setattr(contracts, "find_project_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="error_catalog.yaml"):
        contracts._load_error_catalog()


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


def test_generated_openapi_uses_common_error_schema_for_server_failures():
    gateway_doc = create_gateway_app(gateway_settings(), FakeGatewayClients()).openapi()
    risk_doc = create_risk_adapter_app(risk_settings(), FakeRiskClients()).openapi()
    for doc, paths in [
        (gateway_doc, ["/v1/chat/completions", "/v1/embeddings", "/v1/risk/assessments"]),
        (risk_doc, ["/v1/risk/detectors/prompt/assessments", "/v1/risk/assessments"]),
    ]:
        for path in paths:
            responses = doc["paths"][path]["post"]["responses"]
            assert responses["500"]["content"]["application/json"]["schema"]["title"] == "CommonErrorResponse"


def test_main_model_switch_examples_cover_every_verified_profile():
    """Scalar UI의 profile 전환 드롭다운은 이 examples 목록이다.

    catalog에 verified profile을 추가하고 여기에 안 넣으면 UI에서 고를 수 없다 --
    E4B가 실제로 그렇게 빠져 있었다. 목록을 catalog와 대조해 드리프트를 막는다.
    """
    from pathlib import Path

    from ai_model_serving.main_model.control import load_main_model_catalog

    root = Path(__file__).resolve().parents[2]
    catalog = load_main_model_catalog(root / "configs/main_model_profiles.yaml", resolve_runtime_images=False)
    verified = {
        profile_id
        for profile_id, profile in catalog.profiles.items()
        if (profile.compatibility or {}).get("status") == "verified"
    }

    app = create_gateway_app(gateway_settings(), FakeGatewayClients())
    examples = app.openapi()["paths"]["/admin/main-model/switch"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    advertised = {example["value"]["profile"] for example in examples.values()}

    assert verified <= advertised, f"verified인데 Scalar 드롭다운에 없는 profile: {sorted(verified - advertised)}"
    assert advertised <= set(catalog.profiles), f"catalog에 없는 profile이 예시에 있음: {sorted(advertised - set(catalog.profiles))}"
