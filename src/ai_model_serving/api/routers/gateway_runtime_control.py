from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
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
from ...services.sidecar_client import (
    SidecarClient,
    SidecarRequestError,
    SidecarUnavailableError,
)

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}
_MAIN_MODEL_OPERATION_STATES = [
    "pending",
    "preparing",
    "draining",
    "stopping",
    "starting",
    "validating",
    "rolling_back",
    "completed",
    "failed",
    "rollback_failed",
]
_OPERATION_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["id", "requested_profile", "status", "stage", "created_at", "updated_at"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "requested_profile": {"type": "string"},
        "previous_profile": {"type": ["string", "null"]},
        "client_request_id": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": _MAIN_MODEL_OPERATION_STATES},
        "stage": {"type": "string", "enum": _MAIN_MODEL_OPERATION_STATES},
        "error": {"type": ["string", "null"]},
        "rollback_error": {"type": ["string", "null"]},
        "created_at": {"type": "number"},
        "updated_at": {"type": "number"},
    },
}
_ACCEPTED_SCHEMA = {
    "type": "object",
    "required": ["operation_id", "status"],
    "properties": {
        "operation_id": {"type": "string", "format": "uuid"},
        "status": {"type": "string", "const": "pending"},
    },
}
_MAIN_MODEL_STATUS_EXAMPLE = {
    "public_model": "local-main",
    "active_profile": {
        "id": "gemma4-26b-a4b-fp8",
        "display_name": "Gemma 4 26B A4B FP8",
        "served_model_name": "local-main",
        "upstream_model_id": "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic",
        "revision": "8edbb9269ec9c3faad538ee1208a07eb46051f34",
        "compatibility": {"status": "verified", "reasons": []},
        "capabilities": {
            "model_input": ["text", "image"],
            "deployed_input": ["text", "image"],
            "output": ["text"],
            "audio_enabled": False,
        },
    },
    "last_known_good_profile": "gemma4-26b-a4b-fp8",
    "previous_known_good_profile": None,
    "gate": "open",
    "profile_locked": False,
    "boot_profile": "gemma4-26b-a4b-fp8",
    "last_operation": None,
}

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

    async def require_sidecar() -> SidecarClient:
        if sidecar is None:
            raise HTTPException(503, detail="admin sidecar is not configured")
        return sidecar

    @router.get(
        "/admin/main-model",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="활성 메인 모델 조회",
        operation_id="getMainModel",
        responses={
            200: {
                "description": "현재 main-model 상태",
                "content": {
                    "application/json": {"example": _MAIN_MODEL_STATUS_EXAMPLE}
                },
            },
            503: {"description": "Admin Sidecar 연결 또는 상태 파일 오류"},
        },
    )
    async def get_main_model() -> JSONResponse:
        client = await require_sidecar()
        try:
            return JSONResponse(await client.main_model())
        except SidecarUnavailableError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    @router.get(
        "/admin/main-model/profiles",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="메인 모델 프로필 조회",
        operation_id="listMainModelProfiles",
        responses={
            200: {
                "description": "허용된 프로필 목록",
                "content": {
                    "application/json": {
                        "example": {
                            "profiles": [
                                {
                                    **_MAIN_MODEL_STATUS_EXAMPLE["active_profile"],
                                    "active": True,
                                },
                                {
                                    "id": "gemma4-12b-unified-fp8",
                                    "display_name": "Gemma 4 12B Unified FP8",
                                    "served_model_name": "local-main",
                                    "upstream_model_id": "RedHatAI/gemma-4-12B-it-FP8-Dynamic",
                                    "revision": "67e53491df7a281623fa740de61307d5c542b7f4",
                                    "compatibility": {
                                        "status": "unverified",
                                        "reasons": [
                                            "GPU audio/text/image certification has not been executed."
                                        ],
                                    },
                                    "capabilities": {
                                        "model_input": ["text", "image", "audio"],
                                        "deployed_input": ["text", "image"],
                                        "output": ["text"],
                                        "audio_enabled": False,
                                    },
                                    "active": False,
                                },
                            ]
                        }
                    }
                },
            },
            503: {"description": "Admin Sidecar 연결 실패"},
        },
    )
    async def list_main_model_profiles() -> JSONResponse:
        client = await require_sidecar()
        try:
            return JSONResponse({"profiles": await client.main_model_profiles()})
        except SidecarUnavailableError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    @router.post(
        "/admin/main-model/switch",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="메인 모델 전환",
        operation_id="switchMainModel",
        status_code=202,
        responses={
            202: {
                "description": "전환 작업 접수",
                "content": {"application/json": {"schema": _ACCEPTED_SCHEMA}},
            },
            409: {"description": "전환 중, locked, confirmation 필요 또는 request_id 충돌"},
            422: {"description": "잘못된 profile 또는 request"},
            503: {"description": "Admin Sidecar 연결 실패"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["profile"],
                            "properties": {
                                "profile": {
                                    "type": "string",
                                    "description": "configs/main_model_profiles.yaml의 profile ID",
                                },
                                "confirm_unverified": {"type": "boolean", "default": False},
                                "request_id": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                },
                            },
                        },
                        "examples": {
                            "switch_to_12b": {
                                "value": {
                                    "profile": "gemma4-12b-unified-fp8",
                                    "confirm_unverified": True,
                                    "request_id": "ops-20260618-12b",
                                }
                            }
                        },
                    }
                },
            }
        },
    )
    async def switch_main_model(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("profile"), str):
            raise HTTPException(422, detail="profile is required")
        unknown = set(payload) - {"profile", "confirm_unverified", "request_id"}
        if unknown:
            raise HTTPException(422, detail=f"unsupported fields: {sorted(unknown)}")
        client = await require_sidecar()
        try:
            result = await client.switch_main_model(
                payload["profile"],
                confirm_unverified=payload.get("confirm_unverified") is True,
                request_id=payload.get("request_id"),
            )
            return JSONResponse(result, status_code=202)
        except SidecarRequestError as exc:
            raise HTTPException(exc.status_code, detail=exc.detail) from exc
        except SidecarUnavailableError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    @router.post(
        "/admin/main-model/rollback",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="이전 정상 메인 모델로 rollback",
        operation_id="rollbackMainModel",
        status_code=202,
        responses={
            202: {
                "description": "rollback 작업 접수",
                "content": {"application/json": {"schema": _ACCEPTED_SCHEMA}},
            },
            409: {"description": "rollback 대상 없음 또는 다른 전환 진행 중"},
            503: {"description": "Admin Sidecar 연결 실패"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "request_id": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                }
                            },
                        },
                        "example": {"request_id": "ops-20260618-rollback"},
                    }
                },
            }
        },
    )
    async def rollback_main_model(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, detail="request body must be an object")
        unknown = set(payload) - {"request_id"}
        if unknown:
            raise HTTPException(422, detail=f"unsupported fields: {sorted(unknown)}")
        client = await require_sidecar()
        try:
            result = await client.rollback_main_model(request_id=payload.get("request_id"))
            return JSONResponse(result, status_code=202)
        except SidecarRequestError as exc:
            raise HTTPException(exc.status_code, detail=exc.detail) from exc
        except SidecarUnavailableError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    @router.get(
        "/admin/main-model/operations/{operation_id}",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="메인 모델 전환 작업 조회",
        operation_id="getMainModelOperation",
        responses={
            200: {
                "description": "전환 작업 상태",
                "content": {"application/json": {"schema": _OPERATION_RESPONSE_SCHEMA}},
            },
            404: {"description": "operation 없음"},
            503: {"description": "Admin Sidecar 연결 실패"},
        },
    )
    async def get_main_model_operation(request: Request) -> JSONResponse:
        operation_id = str(request.path_params["operation_id"])
        client = await require_sidecar()
        try:
            return JSONResponse(await client.main_model_operation(operation_id))
        except SidecarUnavailableError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    return router
