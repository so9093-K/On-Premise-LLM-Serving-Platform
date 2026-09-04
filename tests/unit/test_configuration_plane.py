from __future__ import annotations

from dataclasses import replace

import pytest

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.configuration import load_yaml_mapping
from ai_model_serving.configuration_plane import configuration_schema, effective_configuration
from ai_model_serving.settings import SecuritySettings
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients, settings


def test_configuration_projection_has_explicit_ownership_and_never_returns_secret_values() -> None:
    app_settings = settings()
    body = effective_configuration(app_settings)
    secret = next(item for item in body["items"] if item["key"] == "security.api_keys")

    assert {item["key"] for item in configuration_schema()["items"]} == {
        item["key"] for item in body["items"]
    }
    assert secret == {
        "key": "security.api_keys",
        "effective_value": None,
        "effective_source": "secret",
        "owner": "secret",
        "sensitive": True,
        "configured": True,
    }
    assert "test-key" not in str(body)
    values = {item["key"]: item["effective_value"] for item in body["items"]}
    assert values["deployment.control_mode"] == "sidecar"
    assert values["deployment.lifecycle_owner"] == "platform"
    assert "runtime_control" in values["deployment.features"]


def test_configuration_routes_are_admin_protected_and_documented() -> None:
    original = settings()
    app_settings = replace(
        original,
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            admin_api_key_required=True,
            admin_api_keys=frozenset({"admin-key"}),
        ),
    )
    app = create_gateway_app(app_settings, FakeGatewayClients())
    client = TestClient(app)

    assert client.get("/admin/config/schema").status_code == 401
    response = client.get("/admin/config/effective", headers={"Authorization": "Bearer admin-key"})

    assert response.status_code == 200
    assert "/admin/config/schema" in app.openapi()["paths"]
    assert "/admin/config/effective" in app.openapi()["paths"]


def test_configuration_schema_governance_rejects_unknown_projection(monkeypatch) -> None:
    from scripts.validation.governance import model_config

    from pathlib import Path

    document = load_yaml_mapping(
        Path(__file__).resolve().parents[2] / "configs" / "configuration_schema.yaml"
    )
    document["items"][0]["projection"] = "security_admin_api_keys"
    monkeypatch.setattr(model_config, "read_yaml", lambda _: document)

    with pytest.raises(SystemExit, match="projection is not allowlisted"):
        model_config.validate_configuration_schema()


def test_configuration_schema_governance_rejects_secret_policy_drift(monkeypatch) -> None:
    from pathlib import Path

    from scripts.validation.governance import model_config

    document = load_yaml_mapping(
        Path(__file__).resolve().parents[2] / "configs" / "configuration_schema.yaml"
    )
    secret = next(item for item in document["items"] if item["key"] == "security.api_keys")
    secret["sensitive"] = False
    monkeypatch.setattr(model_config, "read_yaml", lambda _: document)

    with pytest.raises(SystemExit, match="secret type and sensitive flag must agree"):
        model_config.validate_configuration_schema()
