from __future__ import annotations

from jsonschema import Draft202012Validator

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
