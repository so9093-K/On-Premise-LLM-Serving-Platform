from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...app_kernel import readiness_response
from ...logging_policy import record_readiness_failure
from ..endpoint_spec import RISK_ADAPTER_ENDPOINTS
from ...api_examples import risk_loading_response_example, risk_ready_response_example
from ...services.readiness import DependencyProbe, collect_readiness

_RA = {(s.method, s.path): s for s in RISK_ADAPTER_ENDPOINTS}


async def _readiness(
    clients: Any,
    metrics: Any = None,
    *,
    settings: Any,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    # 의존성 이름은 detector가 실제로 붙는 runtime의 service_id다. 예전엔
    # f"risk_{key}_vllm"으로 조립했는데, detector key와 runtime은 1:1이 아니라
    # 두 detector가 한 runtime을 공유하면 없는 서비스 이름을 광고하게 된다.
    service_id_by_detector = {
        detector.key: settings.runtime_service_id(detector.service_key)
        for detector in settings.enabled_risk_detectors()
        if detector.detector_type == "vllm" and detector.service_key
    }
    probes = [
        DependencyProbe(service_id_by_detector[key], client, "models")
        for key, client in clients.detectors.items()
        if key in service_id_by_detector
    ]
    return await collect_readiness(
        service="risk-adapter",
        probes=probes,
        metrics=metrics,
        timeout_seconds=timeout_seconds,
    )


def build_router(admin_dependencies: list, clients: Any, metrics: Any, settings: Any) -> APIRouter:
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
                "content": {"application/json": {"example": risk_ready_response_example()}},
            },
            401: {"description": "Admin Bearer token 필요"},
            503: {
                "description": "하나 이상의 detector runtime이 아직 로딩 중이거나 사용할 수 없음",
                "content": {"application/json": {"example": risk_loading_response_example()}},
            },
        },
    )
    async def ready(request: Request) -> JSONResponse:
        body = await _readiness(
            clients,
            metrics,
            settings=settings,
            timeout_seconds=settings.readiness_probe_timeout_seconds,
        )
        record_readiness_failure(request, body)
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
