from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...app_kernel import readiness_response
from ..endpoint_spec import RISK_ADAPTER_ENDPOINTS
from ...api_examples import RISK_LOADING_RESPONSE_EXAMPLE, RISK_READY_RESPONSE_EXAMPLE
from ...services.readiness import DependencyProbe, collect_readiness

_RA = {(s.method, s.path): s for s in RISK_ADAPTER_ENDPOINTS}


async def _readiness(clients: Any, metrics: Any = None) -> dict[str, Any]:
    probes = [
        DependencyProbe(f"risk_{key}_vllm", client, "models")
        for key, client in clients.detectors.items()
    ]
    return await collect_readiness(
        service="risk-adapter",
        probes=probes,
        metrics=metrics,
    )


def build_router(admin_dependencies: list, clients: Any, metrics: Any) -> APIRouter:
    router = APIRouter()

    _s = _RA[("GET", "/ready")]

    @router.get(
        "/ready",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {
                "description": "모든 detector runtime이 준비됨",
                "content": {"application/json": {"example": RISK_READY_RESPONSE_EXAMPLE}},
            },
            401: {"description": "Admin Bearer token 필요"},
            503: {
                "description": "하나 이상의 detector runtime이 아직 로딩 중이거나 사용할 수 없음",
                "content": {"application/json": {"example": RISK_LOADING_RESPONSE_EXAMPLE}},
            },
        },
    )
    async def ready() -> JSONResponse:
        body = await _readiness(clients, metrics)
        return readiness_response(body)

    _s = _RA[("GET", "/metrics")]

    @router.get(
        "/metrics",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "Admin Bearer token 필요"}},
    )
    async def metrics_endpoint():
        return metrics.response()

    return router
