from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...app_kernel import readiness_response
from ...logging_policy import record_readiness_failure
from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...api_examples import LOADING_RESPONSE_EXAMPLE, READY_RESPONSE_EXAMPLE
from ...services.readiness import DependencyProbe, collect_readiness
from ...services.runtime_state import RuntimeState
from ...status import NOT_READY, READY
from ...services.sidecar_client import SidecarUnavailableError

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
    settings: Any,
    metrics: Any = None,
    *,
    admin_token: str | None = None,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    runtime_clients = getattr(clients, "runtime_clients_by_service_key", None)
    if runtime_clients is None:
        runtime_clients = getattr(clients, "runtimes", {})
    runtime_state = getattr(clients, "runtime_state", None)

    async def runtime_required(service_key: str) -> bool:
        if runtime_state is None:
            return True
        state = await runtime_state.get(service_key)
        return state != RuntimeState.stopped

    embedding_probes: list[DependencyProbe] = []
    seen_service_keys: set[str] = set()
    for profile in settings.embedding_profiles.values():
        service_key = profile.service_key
        if service_key in seen_service_keys:
            continue
        seen_service_keys.add(service_key)
        client = getattr(clients, service_key, None)
        if client is None and isinstance(runtime_clients, dict):
            client = runtime_clients.get(service_key)
        if client is not None:
            embedding_probes.append(
                DependencyProbe(
                    f"{service_key}_vllm",
                    client,
                    "models",
                    required=await runtime_required(service_key),
                )
            )
    risk_required = await runtime_required("risk_prompt")
    probes = [
        DependencyProbe("main_llm_vllm", clients.main_llm, "models"),
        *embedding_probes,
        DependencyProbe(
            "risk_adapter",
            clients.risk_adapter,
            "/ready",
            {"authorization": f"Bearer {admin_token}"} if admin_token else None,
            _risk_adapter_readiness,
            required=risk_required,
        ),
    ]
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
    async def ready(request: Request) -> JSONResponse:
        admin_token = next(iter(settings.security.admin_api_keys), None)
        body = await _readiness(
            clients,
            settings,
            metrics,
            admin_token=admin_token,
            timeout_seconds=settings.readiness_probe_timeout_seconds,
        )
        record_readiness_failure(request, body)
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
        sidecar = getattr(clients, "sidecar", None)
        if sidecar is not None:
            try:
                # metric projection은 ledger 필드만 읽는다.
                metrics.project_main_model(await sidecar.main_model(observed=False))
            except SidecarUnavailableError:
                metrics.main_model_gate.labels(metrics.service).set(0)
        return metrics.response()

    return router
