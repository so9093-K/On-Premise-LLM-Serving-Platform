from __future__ import annotations

from enum import Enum
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...api_examples import (
    RUNTIME_DISABLE_RESPONSE_EXAMPLE,
    RUNTIME_ENABLE_RESPONSE_EXAMPLE,
    RUNTIME_ERROR_404_EXAMPLE,
    RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE,
    RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE,
    RUNTIME_LIST_MIXED_STATE_EXAMPLE,
    RUNTIME_LIST_RESPONSE_EXAMPLE,
    RUNTIME_START_RESPONSE_EXAMPLE,
    RUNTIME_START_WITH_PREREQ_RESPONSE_EXAMPLE,
    RUNTIME_STOP_RESPONSE_EXAMPLE,
    RUNTIME_STOP_WITH_PREREQ_RESPONSE_EXAMPLE,
)
from ...services.runtime_state import CONTROLLABLE_KEYS, RuntimeState, RuntimeStateStore
from ...services.sidecar_client import SidecarClient, SidecarUnavailableError

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


class RuntimeServiceKey(str, Enum):
    embedding = "embedding"
    embedding_ko = "embedding_ko"
    risk_prompt = "risk_prompt"


_CONTAINER_RUNNING = frozenset({"running", "restarting"})
_CONTAINER_STOPPED = frozenset({"exited", "created", "dead"})


def _available_actions(gateway_state: str, container_status: str) -> list[str]:
    actions: list[str] = []
    c_running = container_status in _CONTAINER_RUNNING
    c_stopped = container_status in _CONTAINER_STOPPED

    if gateway_state == "active":
        actions.append("disable")
        if c_running:
            actions.append("stop")
        elif c_stopped:
            actions.append("start")
    elif gateway_state == "disabled":
        actions.append("enable")
        if c_running:
            actions.append("stop")
        elif c_stopped:
            actions.append("start")
    elif gateway_state == "stopped":
        if c_stopped:
            actions.append("start")
        elif c_running:
            actions.extend(["enable", "stop"])
    # starting → no actions available

    return actions


def _container_name(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def build_router(
    admin_dependencies: list,
    state_store: RuntimeStateStore,
    sidecar: SidecarClient | None,
    settings: Any,
) -> APIRouter:
    router = APIRouter()

    # Build container→service_key reverse map once at startup.
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
            gw_state = gateway_states.get(sk, RuntimeState.active).value
            c_status = container_statuses.get(container, "unknown")
            runtimes.append({
                "service_key": sk,
                "container": container,
                "gateway_state": gw_state,
                "container_status": c_status,
                "available_actions": _available_actions(gw_state, c_status),
            })
        return JSONResponse({"runtimes": runtimes})

    _s = _GW[("POST", "/admin/runtimes/{service_key}/disable")]

    @router.post(
        "/admin/runtimes/{service_key}/disable",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {"content": {"application/json": {"example": RUNTIME_DISABLE_RESPONSE_EXAMPLE}}},
            **_ADMIN_401,
        },
    )
    async def disable_runtime(service_key: RuntimeServiceKey) -> JSONResponse:
        await state_store.set(service_key.value, RuntimeState.disabled)
        return JSONResponse({"service_key": service_key.value, "gateway_state": "disabled"})

    _s = _GW[("POST", "/admin/runtimes/{service_key}/enable")]

    @router.post(
        "/admin/runtimes/{service_key}/enable",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {"content": {"application/json": {"example": RUNTIME_ENABLE_RESPONSE_EXAMPLE}}},
            **_ADMIN_401,
        },
    )
    async def enable_runtime(service_key: RuntimeServiceKey) -> JSONResponse:
        await state_store.set(service_key.value, RuntimeState.active)
        return JSONResponse({"service_key": service_key.value, "gateway_state": "active"})

    _s = _GW[("POST", "/admin/runtimes/{service_key}/stop")]

    @router.post(
        "/admin/runtimes/{service_key}/stop",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {"content": {"application/json": {"examples": {
                "stop_single": {"summary": "단일 컨테이너 중지", "value": RUNTIME_STOP_RESPONSE_EXAMPLE},
                "stop_with_prereq": {"summary": "선행 컨테이너 포함 중지", "value": RUNTIME_STOP_WITH_PREREQ_RESPONSE_EXAMPLE},
            }}}},
            404: {"content": {"application/json": {"example": RUNTIME_ERROR_404_EXAMPLE}}},
            503: {"content": {"application/json": {"examples": {
                "no_sidecar": {"summary": "sidecar 미설정", "value": RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE},
                "sidecar_unavailable": {"summary": "sidecar 연결 실패", "value": RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE},
            }}}},
            **_ADMIN_401,
        },
    )
    async def stop_runtime(service_key: RuntimeServiceKey) -> JSONResponse:
        if sidecar is None:
            raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
        ep = settings.runtime_endpoints.get(service_key.value)
        if ep is None:
            raise HTTPException(404, detail=f"runtime endpoint not found: {service_key.value}")
        container = _container_name(ep.base_url)
        await state_store.set(service_key.value, RuntimeState.stopped)
        try:
            stopped = await sidecar.stop(container)
        except SidecarUnavailableError as exc:
            await state_store.set(service_key.value, RuntimeState.disabled)
            raise HTTPException(503, detail=str(exc)) from exc

        return JSONResponse({
            "service_key": service_key.value,
            "gateway_state": "stopped",
            "containers_stopped": stopped,
        })

    _s = _GW[("POST", "/admin/runtimes/{service_key}/start")]

    @router.post(
        "/admin/runtimes/{service_key}/start",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {"content": {"application/json": {"examples": {
                "start_single": {"summary": "단일 컨테이너 시작", "value": RUNTIME_START_RESPONSE_EXAMPLE},
                "start_with_prereq": {"summary": "선행 컨테이너 포함 시작", "value": RUNTIME_START_WITH_PREREQ_RESPONSE_EXAMPLE},
            }}}},
            404: {"content": {"application/json": {"example": RUNTIME_ERROR_404_EXAMPLE}}},
            503: {"content": {"application/json": {"examples": {
                "no_sidecar": {"summary": "sidecar 미설정", "value": RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE},
                "sidecar_unavailable": {"summary": "sidecar 연결 실패", "value": RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE},
            }}}},
            **_ADMIN_401,
        },
    )
    async def start_runtime(service_key: RuntimeServiceKey) -> JSONResponse:
        if sidecar is None:
            raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
        ep = settings.runtime_endpoints.get(service_key.value)
        if ep is None:
            raise HTTPException(404, detail=f"runtime endpoint not found: {service_key.value}")
        container = _container_name(ep.base_url)

        await state_store.set(service_key.value, RuntimeState.starting)
        try:
            started_containers = await sidecar.start(container)
        except SidecarUnavailableError as exc:
            await state_store.set(service_key.value, RuntimeState.stopped)
            raise HTTPException(503, detail=str(exc)) from exc

        # Sync gateway state for any prerequisite services the sidecar started.
        for started_container in started_containers:
            started_key = container_to_key.get(started_container)
            if started_key:
                await state_store.set(started_key, RuntimeState.active)
        # Always set primary key active regardless of sidecar response shape.
        await state_store.set(service_key.value, RuntimeState.active)

        return JSONResponse({
            "service_key": service_key.value,
            "gateway_state": "active",
            "containers_started": started_containers,
        })

    return router
