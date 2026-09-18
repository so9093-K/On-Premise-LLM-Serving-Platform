from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.configuration import load_yaml_mapping
from ai_model_serving.configuration_plane import configuration_schema, effective_configuration
from ai_model_serving.configuration_schema import validate_configuration_schema_document
from ai_model_serving.settings import SecuritySettings
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients, settings


_ROOT = Path(__file__).resolve().parents[2]


def _schema_document() -> dict:
    return load_yaml_mapping(_ROOT / "configs" / "configuration_schema.yaml")


def test_configuration_projection_has_explicit_ownership_and_never_returns_secret_values() -> None:
    app_settings = settings()
    body = effective_configuration(app_settings)
    secret = next(item for item in body["items"] if item["key"] == "security.api_keys")

    assert body["version"] == 2
    assert configuration_schema()["version"] == 2
    assert {item["key"] for item in configuration_schema()["items"]} == {
        item["key"] for item in body["items"]
    }
    assert secret == {
        "key": "security.api_keys",
        "effective_value": None,
        "effective_source": "secret",
        "owner": "secret",
        "control_surface": "secret",
        "editable": False,
        "sensitive": True,
        "configured": True,
    }
    assert "test-key" not in str(body)
    values = {item["key"]: item["effective_value"] for item in body["items"]}
    assert values["deployment.control_mode"] == "runtime_controller"
    assert values["deployment.lifecycle_owner"] == "platform"
    assert "runtime_control" in values["deployment.features"]
    assert values["operational.max_retrieval_documents"] == app_settings.max_retrieval_documents
    assert values["streaming.max_chunks"] == app_settings.streaming_max_chunks


def test_configuration_schema_v2_exposes_form_and_control_metadata_without_projection_ids() -> None:
    body = configuration_schema(write_status={"available": True})
    retrieval = next(
        item for item in body["items"] if item["key"] == "operational.max_retrieval_documents"
    )

    assert "projection" not in retrieval
    assert retrieval["label"] == "Max Retrieval Documents"
    assert retrieval["group"] == "retrieval"
    assert retrieval["type"] == "integer"
    assert retrieval["unit"] == "items"
    assert retrieval["minimum"] == 1
    assert retrieval["maximum"] == 32
    assert retrieval["owner"] == "operator"
    assert retrieval["control_surface"] == "configuration"
    assert retrieval["editable"] is True
    assert retrieval["apply_mode"] == "hot_reload"
    assert retrieval["applicability"] == {"features": ["retrieval"]}


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

    unauthorized = client.get("/admin/config/schema")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"

    schema_response = client.get("/admin/config/schema", headers={"Authorization": "Bearer admin-key"})
    schema_body = schema_response.json()
    retrieval = next(item for item in schema_body["items"] if item["key"] == "operational.max_retrieval_documents")
    # Source-tree/local tests without PLATFORM_STATE_DIR have no durable write plane.
    assert schema_body["write_status"]["available"] is False
    assert schema_body["write_status"]["reason"] == "persistent_state_unavailable"
    assert retrieval["editable"] is False

    response = client.get("/admin/config/effective", headers={"Authorization": "Bearer admin-key"})
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["write_status"]["available"] is False
    assert response.headers["etag"] == '"config-0"'

    paths = app.openapi()["paths"]
    assert "/admin/config/schema" in paths
    assert "/admin/config/effective" in paths
    assert "/admin/config/plans" in paths
    assert "/admin/config" in paths
    assert paths["/admin/config/plans"]["post"]["x-contract-schema"] == "configuration_plan_request.schema.json"
    assert paths["/admin/config/plans"]["post"]["x-response-contract-schema"] == "configuration_plan_response.schema.json"
    assert paths["/admin/config"]["patch"]["x-contract-schema"] == "configuration_apply_request.schema.json"
    assert paths["/admin/config"]["patch"]["x-response-contract-schema"] == "configuration_apply_response.schema.json"


def test_configuration_schema_rejects_unknown_projection() -> None:
    from ai_model_serving.configuration_plane import CONFIGURATION_PROJECTION_IDS

    document = _schema_document()
    document["items"][0]["projection"] = "security_admin_api_keys"

    with pytest.raises(ValueError, match="projection is not allowlisted"):
        validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )


def test_configuration_schema_rejects_secret_policy_drift() -> None:
    from ai_model_serving.configuration_plane import CONFIGURATION_PROJECTION_IDS

    document = _schema_document()
    secret = next(item for item in document["items"] if item["key"] == "security.api_keys")
    secret["sensitive"] = False

    with pytest.raises(ValueError, match="secret type and sensitive flag must agree"):
        validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )


def test_configuration_schema_rejects_numeric_constraints_on_non_numeric_type() -> None:
    from ai_model_serving.configuration_plane import CONFIGURATION_PROJECTION_IDS

    document = _schema_document()
    document["items"][0]["minimum"] = 1

    with pytest.raises(ValueError, match="numeric constraints require integer or number type"):
        validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )


def test_configuration_schema_rejects_editable_non_operator_control() -> None:
    from ai_model_serving.configuration_plane import CONFIGURATION_PROJECTION_IDS

    document = _schema_document()
    deployment = document["items"][0]
    deployment["editable"] = True

    with pytest.raises(ValueError, match="editable values must be operator-owned configuration controls"):
        validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )
