from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from ai_model_serving.api.endpoint_spec import (
    GATEWAY_ENDPOINTS,
    RISK_ADAPTER_ENDPOINTS,
    schema_maps_from_specs,
)
from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
from ai_model_serving.openapi_contracts import find_project_root
from ai_model_serving.settings import AppSettings, RuntimeEndpoint, SecuritySettings


# ---------------------------------------------------------------------------
# App factories (minimal settings, no HTTP calls made)
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, endpoint: RuntimeEndpoint):
        self.endpoint = endpoint

    async def post_json(self, path, payload, **kwargs):
        return {}

    async def get_json(self, path, **kwargs):
        return {"object": "list", "data": []}


class _FakeGatewayClients:
    def __init__(self) -> None:
        ep = RuntimeEndpoint("x", "http://x/v1", "x", 1)
        self.main_llm = _FakeClient(ep)
        self.embedding = _FakeClient(ep)
        self.risk_adapter = _FakeClient(RuntimeEndpoint("risk-adapter", "http://risk", "risk-adapter", 1))


class _FakeRiskClients:
    def __init__(self) -> None:
        ep = RuntimeEndpoint("risk-prompt", "http://prompt/v1", "risk-prompt", 1)
        self.prompt = _FakeClient(ep)
        self.detectors = {"prompt": self.prompt}


def _settings() -> AppSettings:
    ep = RuntimeEndpoint("x", "http://x/v1", "x", 1)
    return AppSettings(
        app_env="test",
        project_version="0.1.0",
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
        ),
        gateway_timeout_seconds=1,
        risk_adapter_timeout_seconds=1,
        main_llm=ep,
        embedding=ep,
        risk_prompt=RuntimeEndpoint("risk-prompt", "http://prompt/v1", "risk-prompt", 1),
        risk_adapter_base_url="http://risk",
    )


_INFRA_PREFIXES = ("/docs", "/openapi", "/scalar", "/redoc")


def _app_routes(app) -> dict[tuple[str, str], APIRoute]:
    """Return {(method, path): route} for business APIRoutes (excludes docs/openapi infra)."""
    result: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and not any(
            route.path.startswith(p) for p in _INFRA_PREFIXES
        ):
            for method in route.methods or []:
                result[(method.upper(), route.path)] = route
    return result


# ---------------------------------------------------------------------------
# operation_id uniqueness
# ---------------------------------------------------------------------------

def test_gateway_operation_ids_are_unique() -> None:
    seen: dict[str, str] = {}
    for spec in GATEWAY_ENDPOINTS:
        assert spec.operation_id not in seen, (
            f"Duplicate operation_id '{spec.operation_id}': "
            f"{seen[spec.operation_id]} and {spec.path}"
        )
        seen[spec.operation_id] = spec.path


def test_risk_adapter_operation_ids_are_unique() -> None:
    seen: dict[str, str] = {}
    for spec in RISK_ADAPTER_ENDPOINTS:
        assert spec.operation_id not in seen, (
            f"Duplicate operation_id '{spec.operation_id}': "
            f"{seen[spec.operation_id]} and {spec.path}"
        )
        seen[spec.operation_id] = spec.path


# ---------------------------------------------------------------------------
# Route ↔ spec consistency
# ---------------------------------------------------------------------------

def test_gateway_every_non_removed_spec_has_a_route() -> None:
    routes = _app_routes(create_gateway_app(_settings(), _FakeGatewayClients()))
    for spec in GATEWAY_ENDPOINTS:
        if spec.lifecycle == "removed":
            continue
        assert (spec.method, spec.path) in routes, (
            f"EndpointSpec {spec.method} {spec.path} (lifecycle={spec.lifecycle}) "
            f"has no registered route in the gateway app"
        )


def test_gateway_every_route_has_a_spec() -> None:
    routes = _app_routes(create_gateway_app(_settings(), _FakeGatewayClients()))
    spec_keys = {(s.method, s.path) for s in GATEWAY_ENDPOINTS}
    for method, path in routes:
        assert (method, path) in spec_keys, (
            f"Gateway route {method} {path} has no corresponding EndpointSpec"
        )


def test_risk_adapter_every_non_removed_spec_has_a_route() -> None:
    routes = _app_routes(create_risk_adapter_app(_settings(), _FakeRiskClients()))
    for spec in RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "removed":
            continue
        assert (spec.method, spec.path) in routes, (
            f"EndpointSpec {spec.method} {spec.path} (lifecycle={spec.lifecycle}) "
            f"has no registered route in the risk adapter app"
        )


def test_risk_adapter_every_route_has_a_spec() -> None:
    routes = _app_routes(create_risk_adapter_app(_settings(), _FakeRiskClients()))
    spec_keys = {(s.method, s.path) for s in RISK_ADAPTER_ENDPOINTS}
    for method, path in routes:
        assert (method, path) in spec_keys, (
            f"Risk adapter route {method} {path} has no corresponding EndpointSpec"
        )


def test_removed_specs_have_no_route_in_gateway() -> None:
    routes = _app_routes(create_gateway_app(_settings(), _FakeGatewayClients()))
    for spec in GATEWAY_ENDPOINTS:
        if spec.lifecycle == "removed":
            assert (spec.method, spec.path) not in routes, (
                f"Removed spec {spec.method} {spec.path} must not be registered in gateway app"
            )


def test_removed_specs_have_no_route_in_risk_adapter() -> None:
    routes = _app_routes(create_risk_adapter_app(_settings(), _FakeRiskClients()))
    for spec in RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "removed":
            assert (spec.method, spec.path) not in routes, (
                f"Removed spec {spec.method} {spec.path} must not be registered in risk adapter app"
            )


# ---------------------------------------------------------------------------
# Operation_id wired to routes
# ---------------------------------------------------------------------------

def test_gateway_operation_ids_wired_to_routes() -> None:
    routes = _app_routes(create_gateway_app(_settings(), _FakeGatewayClients()))
    for spec in GATEWAY_ENDPOINTS:
        if spec.lifecycle == "removed":
            continue
        key = (spec.method, spec.path)
        if key not in routes:
            continue  # already caught by route-consistency test
        assert routes[key].operation_id == spec.operation_id, (
            f"{spec.method} {spec.path}: route has operation_id={routes[key].operation_id!r} "
            f"but spec declares {spec.operation_id!r}"
        )


def test_risk_adapter_operation_ids_wired_to_routes() -> None:
    routes = _app_routes(create_risk_adapter_app(_settings(), _FakeRiskClients()))
    for spec in RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "removed":
            continue
        key = (spec.method, spec.path)
        if key not in routes:
            continue
        assert routes[key].operation_id == spec.operation_id, (
            f"{spec.method} {spec.path}: route has operation_id={routes[key].operation_id!r} "
            f"but spec declares {spec.operation_id!r}"
        )


# ---------------------------------------------------------------------------
# Auth scope policy
# ---------------------------------------------------------------------------

def test_health_endpoints_have_no_auth() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.path == "/health":
            assert spec.auth == "none", (
                f"{spec.service} /health must have auth='none', got {spec.auth!r}"
            )


def test_ops_endpoints_require_admin_auth() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.path in ("/ready", "/metrics"):
            assert spec.auth == "admin", (
                f"{spec.service} {spec.path} must have auth='admin', got {spec.auth!r}"
            )


def test_gateway_v1_endpoints_require_public_api_auth() -> None:
    for spec in GATEWAY_ENDPOINTS:
        if spec.path.startswith("/v1/") and spec.exposure != "internal_only":
            assert spec.auth == "public_api", (
                f"Gateway {spec.path} must have auth='public_api', got {spec.auth!r}"
            )


def test_risk_adapter_v1_endpoints_require_internal_service_auth() -> None:
    for spec in RISK_ADAPTER_ENDPOINTS:
        if spec.path.startswith("/v1/") and spec.lifecycle != "removed":
            assert spec.auth == "internal_service", (
                f"Risk Adapter {spec.path} must have auth='internal_service', got {spec.auth!r}"
            )


# ---------------------------------------------------------------------------
# Retired lifecycle policy
# ---------------------------------------------------------------------------

def test_retired_specs_have_410_status() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "retired":
            assert spec.status_code == 410, (
                f"{spec.service} {spec.path}: retired endpoint must have status_code=410, "
                f"got {spec.status_code}"
            )


def test_retired_specs_have_common_error_schema() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "retired":
            assert spec.response_schema == "common_error.schema.json", (
                f"{spec.service} {spec.path}: retired endpoint must have "
                f"response_schema='common_error.schema.json', got {spec.response_schema!r}"
            )


def test_retired_specs_have_replacement() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "retired":
            assert spec.replacement is not None, (
                f"{spec.service} {spec.path}: retired endpoint must declare a replacement path"
            )


def test_stable_specs_have_200_status() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "stable":
            assert spec.status_code == 200, (
                f"{spec.service} {spec.path}: stable endpoint must have status_code=200, "
                f"got {spec.status_code}"
            )


# ---------------------------------------------------------------------------
# schema_maps_from_specs helper (Phase 4)
# ---------------------------------------------------------------------------

def test_schema_maps_from_specs_gateway_request() -> None:
    request_schemas, _ = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    assert request_schemas == {
        ("POST", "/v1/chat/completions"): "chat_completion_request.schema.json",
        ("POST", "/v1/embeddings"): "embedding_request.schema.json",
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/retrieval/rerank"): "retrieval_rerank_request.schema.json",
        ("POST", "/v1/retrieval/score"): "retrieval_score_request.schema.json",
        ("POST", "/v1/retrieval/token-embeddings"): "retrieval_token_embeddings_request.schema.json",
    }


def test_schema_maps_from_specs_gateway_response() -> None:
    _, response_schemas = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    assert response_schemas == {
        ("GET", "/ready"): "readiness_response.schema.json",
        ("GET", "/v1/models"): "model_list_response.schema.json",
        ("POST", "/v1/chat/completions"): "chat_completion_response.schema.json",
        ("POST", "/v1/embeddings"): "embedding_response.schema.json",
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/retrieval/rerank"): "retrieval_rerank_response.schema.json",
        ("POST", "/v1/retrieval/score"): "retrieval_score_response.schema.json",
        ("POST", "/v1/retrieval/token-embeddings"): "retrieval_token_embeddings_response.schema.json",
    }


def test_schema_maps_from_specs_risk_adapter() -> None:
    request_schemas, response_schemas = schema_maps_from_specs(RISK_ADAPTER_ENDPOINTS)
    assert request_schemas == {
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_request.schema.json",
    }
    assert response_schemas == {
        ("GET", "/ready"): "readiness_response.schema.json",
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_response.schema.json",
    }


def test_schema_maps_excludes_retired_and_removed() -> None:
    request_schemas, response_schemas = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    retired_siren = ("POST", "/v1/risk/detectors/siren/assessments")
    assert retired_siren not in request_schemas
    assert retired_siren not in response_schemas


# ---------------------------------------------------------------------------
# Strict schema file existence validation (CI gate)
# ---------------------------------------------------------------------------

def test_spec_schema_files_exist() -> None:
    """Every schema filename declared in any EndpointSpec must exist on disk."""
    schema_dir = find_project_root() / "specs" / "schemas"
    missing: list[str] = []
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        for schema_name in (spec.request_schema, spec.response_schema):
            if schema_name is None:
                continue
            if not (schema_dir / schema_name).exists():
                missing.append(f"{spec.service} {spec.method} {spec.path}: {schema_name}")
    assert not missing, "Schema files declared in EndpointSpec do not exist:\n" + "\n".join(missing)
