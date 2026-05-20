from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...app_kernel import readiness_response
from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...api_examples import LOADING_RESPONSE_EXAMPLE, READY_RESPONSE_EXAMPLE
from ...services.readiness import DependencyProbe, collect_readiness
from ...status import NOT_READY, READY

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def _risk_adapter_readiness(body: dict[str, Any]) -> tuple[str, str | None]:
    status = READY if body.get("status") == READY else NOT_READY
    if status == READY:
        return status, None
    waiting = [
        item.get("name")
        for item in body.get("dependencies", [])
        if isinstance(item, dict) and item.get("status") != "ready"
    ]
    message = (
        "waiting for risk adapter dependencies: " + ", ".join(str(item) for item in waiting)
        if waiting
        else "risk adapter is not ready"
    )
    return status, message


async def _readiness(
    clients: Any,
    metrics: Any = None,
    *,
    admin_token: str | None = None,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    probes = [
        DependencyProbe("main_llm_vllm", clients.main_llm, "models"),
        DependencyProbe("embedding_vllm", clients.embedding, "models"),
        DependencyProbe(
            "risk_adapter",
            clients.risk_adapter,
            "/ready",
            {"authorization": f"Bearer {admin_token}"} if admin_token else None,
            _risk_adapter_readiness,
        ),
    ]
    embedding_ko = getattr(clients, "embedding_ko", None)
    if embedding_ko is not None:
        probes.append(DependencyProbe("embedding_ko_vllm", embedding_ko, "models", required=True))
    return await collect_readiness(service="gateway", probes=probes, metrics=metrics, timeout_seconds=timeout_seconds)


def build_router(admin_dependencies: list, clients: Any, metrics: Any, settings: Any) -> APIRouter:
    router = APIRouter()

    _s = _GW[("GET", "/ready")]

    @router.get(
        "/ready",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {
                "description": "모든 dependency 준비 완료",
                "content": {"application/json": {"example": READY_RESPONSE_EXAMPLE}},
            },
            401: {"description": "Admin Bearer token 필요"},
            503: {
                "description": "일부 dependency 로딩 중 또는 불가",
                "content": {"application/json": {"example": LOADING_RESPONSE_EXAMPLE}},
            },
        },
    )
    async def ready() -> JSONResponse:
        admin_token = next(iter(settings.security.admin_api_keys), None)
        body = await _readiness(
            clients,
            metrics,
            admin_token=admin_token,
            timeout_seconds=settings.readiness_probe_timeout_seconds,
        )
        return readiness_response(body)

    _s = _GW[("GET", "/metrics")]

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
