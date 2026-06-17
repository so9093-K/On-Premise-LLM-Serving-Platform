from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError
from ...api_examples import (
    RUNTIME_ERROR_404_EXAMPLE,
    RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE,
    RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE,
    RUNTIME_LIST_MIXED_STATE_EXAMPLE,
    RUNTIME_LIST_RESPONSE_EXAMPLE,
    RUNTIME_TRANSITION_NOOP_EXAMPLE,
    RUNTIME_TRANSITION_TO_ACTIVE_EXAMPLE,
    RUNTIME_TRANSITION_TO_ACTIVE_WITH_PREREQ_EXAMPLE,
    RUNTIME_TRANSITION_TO_STOPPED_EXAMPLE,
    RUNTIME_TRANSITION_TO_STOPPED_WITH_PREREQ_EXAMPLE,
)
from ...services.runtime_state import CONTROLLABLE_KEYS, RuntimeState, RuntimeStateStore
from ...services.sidecar_client import SidecarClient, SidecarUnavailableError

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}

_DESIRED_STATE_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["desired_state"],
                    "properties": {
                        "desired_state": {
                            "type": "string",
                            "enum": ["active", "stopped"],
                            "description": "목표 상태: active(서비스 시작) 또는 stopped(컨테이너 중지, VRAM 회수)",
                        }
                    },
                },
                "examples": {
                    "to_stopped": {"summary": "중지 (VRAM 회수)", "value": {"desired_state": "stopped"}},
                    "to_active": {"summary": "시작 (서비스 복구)", "value": {"desired_state": "active"}},
                },
            }
        },
    }
}


def _container_name(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _validate_service_key(service_key: str) -> None:
    if service_key not in CONTROLLABLE_KEYS:
        raise HTTPException(404, detail=f"runtime endpoint not found: {service_key}")


def build_router(
    admin_dependencies: list,
    state_store: RuntimeStateStore,
    sidecar: SidecarClient | None,
    settings: Any,
) -> APIRouter:
    router = APIRouter()

    container_to_key: dict[str, str] = {
        _container_name(ep.base_url): sk
        for sk, ep in settings.runtime_endpoints.items()
        if sk in CONTROLLABLE_KEYS
    }

    def _controllable_runtimes() -> list[dict[str, str]]:
        return [
            {
                "service_key": sk,
                "container": _container_name(ep.base_url),
            }
            for sk, ep in settings.runtime_endpoints.items()
            if sk in CONTROLLABLE_KEYS
        ]

    _ADMIN_401 = (
        {401: {"description": "Admin Bearer token 필요"}}
        if settings.security.admin_api_key_required
        else {}
    )

    _s = _GW[("GET", "/admin/runtimes")]

    @router.get(
        "/admin/runtimes",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {"content": {"application/json": {"examples": {
                "all_active": {"summary": "전체 활성", "value": RUNTIME_LIST_RESPONSE_EXAMPLE},
                "mixed_state": {"summary": "중지/시작 중 포함", "value": RUNTIME_LIST_MIXED_STATE_EXAMPLE},
            }}}},
            **_ADMIN_401,
        },
    )
    async def list_runtimes() -> JSONResponse:
        gateway_states = await state_store.all()
        container_statuses: dict[str, str] = {}
        if sidecar is not None:
            try:
                container_statuses = await sidecar.get_status()
            except SidecarUnavailableError:
                container_statuses = {}

        runtimes = []
        for item in _controllable_runtimes():
            sk = item["service_key"]
            container = item["container"]
            state = gateway_states.get(sk, RuntimeState.active).value
            c_status = container_statuses.get(container, "unknown")
            runtimes.append({
                "service_key": sk,
                "container": container,
                "state": state,
                "container_status": c_status,
            })
        return JSONResponse({"runtimes": runtimes})

    _s = _GW[("PATCH", "/admin/runtimes/{service_key}")]

    @router.patch(
        "/admin/runtimes/{service_key}",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {"content": {"application/json": {"examples": {
                "to_stopped": {"summary": "중지 (VRAM 회수)", "value": RUNTIME_TRANSITION_TO_STOPPED_EXAMPLE},
                "to_stopped_prereq": {"summary": "중지 — 선행 컨테이너 포함", "value": RUNTIME_TRANSITION_TO_STOPPED_WITH_PREREQ_EXAMPLE},
                "to_active": {"summary": "시작 (서비스 복구)", "value": RUNTIME_TRANSITION_TO_ACTIVE_EXAMPLE},
                "to_active_prereq": {"summary": "시작 — 선행 컨테이너 포함", "value": RUNTIME_TRANSITION_TO_ACTIVE_WITH_PREREQ_EXAMPLE},
                "noop": {"summary": "이미 목표 상태 (no-op)", "value": RUNTIME_TRANSITION_NOOP_EXAMPLE},
            }}}},
            404: {"content": {"application/json": {"example": RUNTIME_ERROR_404_EXAMPLE}}},
            409: {"description": "런타임이 전환 중입니다. 잠시 후 다시 시도하세요."},
            503: {"content": {"application/json": {"examples": {
                "no_sidecar": {"summary": "sidecar 미설정", "value": RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE},
                "sidecar_unavailable": {"summary": "sidecar 연결 실패", "value": RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE},
            }}}},
            **_ADMIN_401,
        },
        openapi_extra=_DESIRED_STATE_SCHEMA,
    )
    async def transition_runtime(service_key: str, request: Request) -> JSONResponse:
        _validate_service_key(service_key)
        payload = await request.json()
        desired_state = payload.get("desired_state") if isinstance(payload, dict) else None
        if desired_state not in ("active", "stopped"):
            raise HTTPException(422, detail="desired_state must be 'active' or 'stopped'")

        current_state = await state_store.get(service_key)

        if desired_state == "active":
            if current_state in (RuntimeState.active, RuntimeState.starting):
                return JSONResponse({"service_key": service_key, "state": "active", "changed": False})
            if sidecar is None:
                raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
            ep = settings.runtime_endpoints.get(service_key)
            if ep is None:
                raise HTTPException(404, detail=f"runtime endpoint not found: {service_key}")
            container = _container_name(ep.base_url)
            await state_store.set(service_key, RuntimeState.starting)
            try:
                started_containers = await sidecar.start(container)
            except SidecarUnavailableError as exc:
                await state_store.set(service_key, RuntimeState.stopped)
                raise HTTPException(503, detail=str(exc)) from exc
            for started_container in started_containers:
                started_key = container_to_key.get(started_container)
                if started_key:
                    await state_store.set(started_key, RuntimeState.active)
            await state_store.set(service_key, RuntimeState.active)
            return JSONResponse({
                "service_key": service_key,
                "state": "active",
                "containers_started": started_containers,
            })

        else:  # desired_state == "stopped"
            if current_state == RuntimeState.stopped:
                return JSONResponse({"service_key": service_key, "state": "stopped", "changed": False})
            if current_state == RuntimeState.starting:
                raise HTTPException(409, detail="runtime is currently starting; wait and retry")
            if sidecar is None:
                raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
            ep = settings.runtime_endpoints.get(service_key)
            if ep is None:
                raise HTTPException(404, detail=f"runtime endpoint not found: {service_key}")
            container = _container_name(ep.base_url)
            await state_store.set(service_key, RuntimeState.stopped)
            try:
                stopped = await sidecar.stop(container)
            except SidecarUnavailableError as exc:
                await state_store.set(service_key, RuntimeState.active)
                raise HTTPException(503, detail=str(exc)) from exc
            return JSONResponse({
                "service_key": service_key,
                "state": "stopped",
                "containers_stopped": stopped,
            })

    # Retired endpoints — 410 Gone
    _s_disable = _GW[("POST", "/admin/runtimes/{service_key}/disable")]
    _s_enable = _GW[("POST", "/admin/runtimes/{service_key}/enable")]
    _s_stop = _GW[("POST", "/admin/runtimes/{service_key}/stop")]
    _s_start = _GW[("POST", "/admin/runtimes/{service_key}/start")]

    @router.post(
        "/admin/runtimes/{service_key}/disable",
        dependencies=admin_dependencies,
        tags=[_s_disable.tag],
        summary=_s_disable.summary,
        operation_id=_s_disable.operation_id,
        description=_s_disable.description,
        responses={410: {"description": "사용 중단됨"}, **_ADMIN_401},
        include_in_schema=True,
    )
    async def disable_runtime(service_key: str) -> None:
        raise ServiceError("GONE", "이 endpoint는 retired 상태입니다. PATCH /admin/runtimes/{service_key} 를 사용하세요.", False, 410)

    @router.post(
        "/admin/runtimes/{service_key}/enable",
        dependencies=admin_dependencies,
        tags=[_s_enable.tag],
        summary=_s_enable.summary,
        operation_id=_s_enable.operation_id,
        description=_s_enable.description,
        responses={410: {"description": "사용 중단됨"}, **_ADMIN_401},
        include_in_schema=True,
    )
    async def enable_runtime(service_key: str) -> None:
        raise ServiceError("GONE", "이 endpoint는 retired 상태입니다. PATCH /admin/runtimes/{service_key} 를 사용하세요.", False, 410)

    @router.post(
        "/admin/runtimes/{service_key}/stop",
        dependencies=admin_dependencies,
        tags=[_s_stop.tag],
        summary=_s_stop.summary,
        operation_id=_s_stop.operation_id,
        description=_s_stop.description,
        responses={410: {"description": "사용 중단됨"}, **_ADMIN_401},
        include_in_schema=True,
    )
    async def stop_runtime(service_key: str) -> None:
        raise ServiceError("GONE", "이 endpoint는 retired 상태입니다. PATCH /admin/runtimes/{service_key} 를 사용하세요.", False, 410)

    @router.post(
        "/admin/runtimes/{service_key}/start",
        dependencies=admin_dependencies,
        tags=[_s_start.tag],
        summary=_s_start.summary,
        operation_id=_s_start.operation_id,
        description=_s_start.description,
        responses={410: {"description": "사용 중단됨"}, **_ADMIN_401},
        include_in_schema=True,
    )
    async def start_runtime(service_key: str) -> None:
        raise ServiceError("GONE", "이 endpoint는 retired 상태입니다. PATCH /admin/runtimes/{service_key} 를 사용하세요.", False, 410)

    return router
