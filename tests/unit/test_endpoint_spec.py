"""EndpointSpec 레지스트리(GATEWAY_ENDPOINTS/RISK_ADAPTER_ENDPOINTS)가 실제 FastAPI
라우트와 1:1로 맞는지 검증한다: operation_id 유일성, 스펙에 있는데 라우트가
없거나 그 반대인 경우, auth scope 정책, 선언된 schema 파일이 실제로 존재하는지."""

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
# App factory (최소 settings, HTTP 호출 없음)
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
        from ai_model_serving.services.runtime_state import RuntimeStateStore
        ep = RuntimeEndpoint("x", "http://x/v1", "x", 1)
        self.main_llm = _FakeClient(ep)
        self.embedding = _FakeClient(ep)
        self.risk_adapter = _FakeClient(RuntimeEndpoint("risk-adapter", "http://risk", "risk-adapter", 1))
        self.runtime_state = RuntimeStateStore()
        self.sidecar = None


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
    """비즈니스 APIRoute에 대해 {(method, path): route}를 반환한다(docs/openapi 인프라는 제외)."""
    result: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and not any(
            route.path.startswith(p) for p in _INFRA_PREFIXES
        ):
            for method in route.methods or []:
                result[(method.upper(), route.path)] = route
    return result


# ---------------------------------------------------------------------------
# operation_id 유일성
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
# Route ↔ spec 일관성
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


# ---------------------------------------------------------------------------
# Operation_id가 route에 실제로 연결됐는지
# ---------------------------------------------------------------------------

def test_gateway_operation_ids_wired_to_routes() -> None:
    routes = _app_routes(create_gateway_app(_settings(), _FakeGatewayClients()))
    for spec in GATEWAY_ENDPOINTS:
        if spec.lifecycle == "removed":
            continue
        key = (spec.method, spec.path)
        if key not in routes:
            continue  # route-consistency 테스트에서 이미 잡힌다
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
# Auth scope 정책
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


def test_stable_specs_have_success_status() -> None:
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        if spec.lifecycle == "stable":
            assert spec.status_code in {200, 202}, (
                f"{spec.service} {spec.path}: stable endpoint must have status_code=200 or 202, "
                f"got {spec.status_code}"
            )


# ---------------------------------------------------------------------------
# schema_maps_from_specs 헬퍼 (Phase 4)
# ---------------------------------------------------------------------------

def test_schema_maps_from_specs_gateway_request() -> None:
    request_schemas, _ = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    assert request_schemas == {
        ("POST", "/v1/chat/completions"): "chat_completion_request.schema.json",
        ("POST", "/v1/embeddings"): "embedding_request.schema.json",
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/detectors/pii/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/detectors/secret/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/retrieval/rerank"): "retrieval_rerank_request.schema.json",
        ("POST", "/v1/retrieval/score"): "retrieval_score_request.schema.json",
    }


def test_schema_maps_from_specs_gateway_response() -> None:
    _, response_schemas = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    assert response_schemas == {
        ("GET", "/ready"): "readiness_response.schema.json",
        ("GET", "/v1/models"): "model_list_response.schema.json",
        ("POST", "/v1/chat/completions"): "chat_completion_response.schema.json",
        ("POST", "/v1/embeddings"): "embedding_response.schema.json",
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/detectors/pii/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/detectors/secret/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/retrieval/rerank"): "retrieval_rerank_response.schema.json",
        ("POST", "/v1/retrieval/score"): "retrieval_score_response.schema.json",
    }


def test_schema_maps_from_specs_risk_adapter() -> None:
    request_schemas, response_schemas = schema_maps_from_specs(RISK_ADAPTER_ENDPOINTS)
    assert request_schemas == {
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/detectors/pii/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/detectors/secret/assessments"): "risk_assessment_request.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_request.schema.json",
    }
    assert response_schemas == {
        ("GET", "/ready"): "readiness_response.schema.json",
        ("POST", "/v1/risk/detectors/prompt/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/detectors/pii/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/detectors/secret/assessments"): "risk_assessment_response.schema.json",
        ("POST", "/v1/risk/assessments"): "risk_assessment_response.schema.json",
    }


# ---------------------------------------------------------------------------
# schema 파일 존재 여부 엄격 검증 (CI 게이트)
# ---------------------------------------------------------------------------

def test_spec_schema_files_exist() -> None:
    """EndpointSpec에 선언된 모든 schema 파일명은 디스크에 실제로 존재해야 한다."""
    schema_dir = find_project_root() / "specs" / "schemas"
    missing: list[str] = []
    for spec in GATEWAY_ENDPOINTS + RISK_ADAPTER_ENDPOINTS:
        for schema_name in (spec.request_schema, spec.response_schema):
            if schema_name is None:
                continue
            if not (schema_dir / schema_name).exists():
                missing.append(f"{spec.service} {spec.method} {spec.path}: {schema_name}")
    assert not missing, "Schema files declared in EndpointSpec do not exist:\n" + "\n".join(missing)
