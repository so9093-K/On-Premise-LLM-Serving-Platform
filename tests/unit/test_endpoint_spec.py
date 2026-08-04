"""EndpointSpec의 보안 기본값과 선언 schema 파일을 검증한다.

Route/path/operationId 정합성은 make validate의 generated OpenAPI 비교가 실제 app
산출물 기준으로 확인한다. 여기서는 그와 독립적인 보안 기본값만 유지한다.
"""

from __future__ import annotations

import pytest

from ai_model_serving.api.endpoint_spec import GATEWAY_ENDPOINTS, RISK_ADAPTER_ENDPOINTS
from ai_model_serving.openapi_contracts import find_project_root


# Auth scope 정책

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


@pytest.mark.parametrize(
    "endpoints,expected_auth",
    [
        pytest.param(GATEWAY_ENDPOINTS, "public_api", id="gateway"),
        pytest.param(RISK_ADAPTER_ENDPOINTS, "internal_service", id="risk-adapter"),
    ],
)
def test_v1_endpoints_require_correct_auth_tier(endpoints, expected_auth) -> None:
    for spec in endpoints:
        if not spec.path.startswith("/v1/") or spec.lifecycle == "removed":
            continue
        if spec.exposure == "internal_only":
            continue
        assert spec.auth == expected_auth, (
            f"{spec.service} {spec.path} must have auth={expected_auth!r}, got {spec.auth!r}"
        )


# schema 파일 존재 여부

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
