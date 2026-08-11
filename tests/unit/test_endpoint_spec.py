"""EndpointSpec의 보안 기본값을 검증한다.

Route/path/operationId 정합성은 make validate의 generated OpenAPI 비교가 실제 app
산출물 기준으로 확인한다. 여기서는 그와 독립적인 보안 기본값만 유지한다.
"""

from __future__ import annotations

import pytest

from ai_model_serving.api.endpoint_spec import GATEWAY_ENDPOINTS, RISK_ADAPTER_ENDPOINTS


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
