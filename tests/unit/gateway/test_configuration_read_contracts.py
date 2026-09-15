from __future__ import annotations

from jsonschema import Draft202012Validator

from ai_model_serving.configuration_schema import (
    CONFIGURATION_APPLY_MODES,
    CONFIGURATION_CONTROL_SURFACES,
    CONFIGURATION_OWNERS,
    CONFIGURATION_RISKS,
    CONFIGURATION_VALUE_TYPES,
)
from ai_model_serving.openapi_contracts import load_contract_schema

from .helpers import FakeGatewayClients, TestClient, create_gateway_app, settings


def _validate(schema_name: str, payload: object) -> None:
    Draft202012Validator(load_contract_schema(schema_name)).validate(payload)


def test_configuration_admin_reads_match_checked_in_contracts() -> None:
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))

    schema = client.get("/admin/config/schema")
    assert schema.status_code == 200
    _validate("configuration_schema_response.schema.json", schema.json())

    effective = client.get("/admin/config/effective")
    assert effective.status_code == 200
    _validate("configuration_effective_response.schema.json", effective.json())
    assert effective.headers["etag"] == f'"config-{effective.json()["revision"]}"'


def test_configuration_read_contract_enums_match_metadata_grammar() -> None:
    schema = load_contract_schema("configuration_schema_response.schema.json")
    properties = schema["$defs"]["item"]["properties"]

    assert set(properties["type"]["enum"]) == set(CONFIGURATION_VALUE_TYPES)
    assert set(properties["owner"]["enum"]) == set(CONFIGURATION_OWNERS)
    assert set(properties["effective_source"]["enum"]) == set(CONFIGURATION_OWNERS)
    assert set(properties["apply_mode"]["enum"]) == set(CONFIGURATION_APPLY_MODES)
    assert set(properties["control_surface"]["enum"]) == set(CONFIGURATION_CONTROL_SURFACES)
    assert set(properties["risk"]["enum"]) == set(CONFIGURATION_RISKS)

    effective = load_contract_schema("configuration_effective_response.schema.json")
    effective_properties = effective["$defs"]["item"]["properties"]
    assert set(effective_properties["owner"]["enum"]) == set(CONFIGURATION_OWNERS)
    assert set(effective_properties["effective_source"]["enum"]) == set(CONFIGURATION_OWNERS)
    assert set(effective_properties["control_surface"]["enum"]) == set(CONFIGURATION_CONTROL_SURFACES)
