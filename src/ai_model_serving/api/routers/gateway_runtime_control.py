from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import default_code_for_status, error_payload, error_response_headers
from ...api_examples import (
    RUNTIME_BUDGET_EXCEEDED_EXAMPLE,
    RUNTIME_ERROR_404_EXAMPLE,
    RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE,
    RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE,
    RUNTIME_ERROR_503_TRANSITIONING_EXAMPLE,
    RUNTIME_LIST_MIXED_STATE_EXAMPLE,
    RUNTIME_LIST_RESPONSE_EXAMPLE,
    RUNTIME_TRANSITION_NOOP_EXAMPLE,
    RUNTIME_TRANSITION_TO_ACTIVE_EXAMPLE,
    RUNTIME_TRANSITION_TO_ACTIVE_WITH_PREREQ_EXAMPLE,
    RUNTIME_TRANSITION_TO_STOPPED_EXAMPLE,
    RUNTIME_TRANSITION_TO_STOPPED_WITH_PREREQ_EXAMPLE,
)
from ...services.runtime_state import RuntimeState, RuntimeStateStore
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
_OPERATION_ID_EXAMPLE = "41cf50bb-60b2-4dbc-b38a-7dd07da91d97"
_OPERATION_LOOKUP_DESCRIPTION = (
    "비동기 전환 작업의 상태를 조회한다. `operation_id`는 `POST /admin/main-model/switch`의 202 "
    "응답에서 받은 값이며, `completed`·`failed`·`rollback_failed`에 도달할 때까지 폴링한다. 가장 "
    "최근 작업은 `GET /admin/main-model`의 `last_operation`으로도 확인할 수 있고, 진행 상태는 "
    "Grafana `Main-model Control` 대시보드의 Latest Operation State 패널에서 실시간으로 볼 수 있다."
)
_OPERATION_ID_PARAMETER = {
    "name": "operation_id",
    "in": "path",
    "required": True,
    "description": "switch 202 응답의 `operation_id` (UUID).",
    "schema": {"type": "string", "format": "uuid"},
    "example": _OPERATION_ID_EXAMPLE,
}
_OPERATION_RESPONSE_EXAMPLES = {
    "in_progress": {
        "summary": "진행 중 (validating)",
        "value": {
            "id": _OPERATION_ID_EXAMPLE,
            "requested_profile": "gemma4-12b-unified-fp8",
            "previous_profile": "gemma4-26b-a4b-fp8",
            "client_request_id": "ops-20260622-12b",
            "status": "validating",
            "stage": "validating",
            "error": None,
            "rollback_error": None,
            "created_at": 1782086105.78,
            "updated_at": 1782086230.12,
        },
    },
    "completed": {
        "summary": "완료",
        "value": {
            "id": _OPERATION_ID_EXAMPLE,
            "requested_profile": "gemma4-12b-unified-fp8",
            "previous_profile": "gemma4-26b-a4b-fp8",
            "client_request_id": "ops-20260622-12b",
            "status": "completed",
            "stage": "completed",
            "error": None,
            "rollback_error": None,
            "created_at": 1782086105.78,
            "updated_at": 1782086258.20,
        },
    },
    "failed": {
        "summary": "실패 후 이전 프로필로 rollback",
        "value": {
            "id": _OPERATION_ID_EXAMPLE,
            "requested_profile": "gemma4-12b-unified-fp8",
            "previous_profile": "gemma4-26b-a4b-fp8",
            "client_request_id": "ops-20260622-12b",
            "status": "failed",
            "stage": "validating",
            "error": "runtime validation failed: /v1/models did not report local-main",
            "rollback_error": None,
            "created_at": 1782086105.78,
            "updated_at": 1782086240.55,
        },
    },
}
_ACCEPTED_SCHEMA = {
    "type": "object",
    "required": ["operation_id", "status", "reused"],
    "properties": {
        "operation_id": {"type": "string", "format": "uuid"},
        "status": {
            "type": "string",
            "description": "operation 현재 stage/status. 새 전환은 pending으로 시작하고, 동일 request_id 재생 시 기존 작업의 status(예: completed)를 그대로 반환한다.",
        },
        "reused": {
            "type": "boolean",
            "description": "true면 동일 request_id의 기존 작업을 반환한 것이며 새 전환은 시작되지 않았다.",
        },
        "message": {
            "type": "string",
            "description": "운영자용 설명 — 재생 여부와 진행 상황 조회 위치를 안내한다.",
        },
    },
}
_MAIN_MODEL_STATUS_EXAMPLE = {
    "public_model": "local-main",
    "active_profile": {
        "id": "gemma4-12b-unified-fp8",
        "display_name": "Gemma 4 12B Unified FP8",
        "served_model_name": "local-main",
        "upstream_model_id": "RedHatAI/gemma-4-12B-it-FP8-Dynamic",
        "revision": "67e53491df7a281623fa740de61307d5c542b7f4",
        "compatibility": {"status": "verified"},
        "capabilities": {
            "deployed_input": ["text", "image", "audio", "video"],
        },
        # 이 예시는 API 형식만 보여준다. 실제 runtime image는 활성 profile과
        # 배포 시 주입되는 immutable digest에서 결정되므로 특정 배포 registry/digest를
        # OpenAPI 예시에 고정하지 않는다.
        "runtime_image": "registry.example.com/vllm-unified@sha256:" + "0" * 64,
        "vram_fraction": 0.76,
    },
    "last_known_good_profile": "gemma4-12b-unified-fp8",
    "previous_known_good_profile": None,
    "gate": "open",
    "runtime_state": "active",
    "profile_locked": False,
    "boot_profile": "gemma4-12b-unified-fp8",
    "last_operation": None,
    "observed_runtime": {
        "status": "ready",
        "container_state": "running",
        "health": "healthy",
        "profile_id": "gemma4-12b-unified-fp8",
        "error": None,
        "observed_at": 1782086258.2,
    },
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
                        },
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": "active 전환 시 GPU 예산이 부족하면 우선순위가 낮은 런타임을 자동 정지해 공간을 확보합니다. 기본값은 false로, 부족하면 409와 정지 계획을 반환합니다.",
                        },
                    },
                },
                "examples": {
                    "to_stopped": {"summary": "중지 (VRAM 회수)", "value": {"desired_state": "stopped"}},
                    "to_active": {"summary": "시작 (서비스 복구)", "value": {"desired_state": "active"}},
                    "to_active_force": {"summary": "시작 + 자동 축출", "value": {"desired_state": "active", "force": True}},
                },
            }
        },
    }
}


def _container_name(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _sidecar_unavailable_response(exc: SidecarUnavailableError) -> JSONResponse:
    # gateway_inference.py의 SidecarUnavailableError 처리와 동일한 code/retryable로
    # 맞춘다. 이전에는 이 파일 전체가 raise HTTPException(503, ...)만 썼는데,
    # StarletteHTTPException 제네릭 핸들러(app_kernel.py)는 retryable=False +
    # code=MODEL_UNAVAILABLE로 나가서, 동일한 sidecar 장애가 /v1/chat/completions에서는
    # retryable=True(Retry-After 포함)로, /admin/runtimes 계열에서는 retryable=False로
    # 클라이언트에 다르게 보였다.
    payload = error_payload("MAIN_MODEL_CONTROL_UNAVAILABLE", str(exc), True)
    return JSONResponse(
        payload,
        status_code=503,
        headers=error_response_headers("MAIN_MODEL_CONTROL_UNAVAILABLE", payload, retry_after_seconds=5),
    )


def _runtime_transitioning_response() -> JSONResponse:
    # 위 _sidecar_unavailable_response와 같은 이유로 raise HTTPException(503, ...)을
    # 쓰지 않는다: 제네릭 핸들러는 retryable=False로 응답하는데, 이 메시지("wait and
    # retry")와 code(MODEL_UNAVAILABLE, 카탈로그 기준 retryable=true)는 둘 다 재시도를
    # 전제로 한다 -- retryable=False로 나가면 메시지와 machine-readable 필드가 서로
    # 모순된다.
    message = "runtime is currently starting; wait and retry"
    payload = error_payload("MODEL_UNAVAILABLE", message, True)
    return JSONResponse(
        payload,
        status_code=503,
        headers=error_response_headers("MODEL_UNAVAILABLE", payload, retry_after_seconds=5),
    )


def _sidecar_request_error_response(exc: SidecarRequestError) -> JSONResponse:
    """Sidecar의 구조화된 작업 거부를 Gateway 표준 오류 envelope로 전달한다."""
    detail = exc.detail
    raw_detail = detail if isinstance(detail, dict) else {}
    specific_code = raw_detail.get("code") if isinstance(raw_detail.get("code"), str) else None
    message = raw_detail.get("message") if isinstance(raw_detail.get("message"), str) else str(detail)

    # GPU 예산 거부는 호출자가 plan.stop을 읽어 다음 동작을 결정해야 하므로 독립된
    # 플랫폼 code로 승격한다. 나머지 409(전환 lock·확인 필요·멱등 키 충돌)는
    # 공통 CONFLICT와 details.reason 조합으로 표현해, 내부 sidecar code를 외부
    # 플랫폼 code 집합에 무분별하게 늘리지 않는다.
    if exc.status_code == 409:
        code = "GPU_BUDGET_EXCEEDED" if specific_code == "GPU_BUDGET_EXCEEDED" else "CONFLICT"
    else:
        # main-model switch의 profile 없음(404)·입력/호환성 오류(422)도 같은
        # sidecar client를 거친다. 409 전용 CONFLICT를 붙이면 HTTP 상태와 code의
        # canonical status가 다시 불일치하므로, 그 외에는 공통 status 매핑을 따른다.
        code = default_code_for_status(exc.status_code)
    details: dict[str, Any] = {}
    if specific_code and code != "GPU_BUDGET_EXCEEDED":
        details["reason"] = specific_code
    for key, value in raw_detail.items():
        if key not in {"code", "message"}:
            details[key] = value
    payload = error_payload(code, message, False, details=details or None)
    return JSONResponse(
        payload,
        status_code=exc.status_code,
        headers=error_response_headers(code, payload),
    )


def _budget_participant(budget: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    for participant in (budget or {}).get("participants", []):
        if participant.get("key") == key:
            return participant
    return None


def _budget_fraction(budget: dict[str, Any] | None, key: str) -> float | None:
    participant = _budget_participant(budget, key)
    return participant.get("vram_fraction") if participant else None


def _main_participant(
    budget: dict[str, Any] | None, secondary_containers: set[str]
) -> dict[str, Any] | None:
    """존재하면 보조 런타임이 아닌 main model 예산 참여자를 반환한다."""
    for participant in (budget or {}).get("participants", []):
        if participant.get("key") not in secondary_containers:
            return participant
    return None


def _validate_service_key(service_key: str, state_store: RuntimeStateStore) -> None:
    if service_key not in state_store.controllable_keys:
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
        if sk in state_store.controllable_keys
    }

    def _controllable_runtimes() -> list[dict[str, str]]:
        return [
            {
                "service_key": sk,
                "container": _container_name(ep.base_url),
            }
            for sk, ep in settings.runtime_endpoints.items()
            if sk in state_store.controllable_keys
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
        gateway_records = await state_store.all_records()
        container_statuses: dict[str, str] = {}
        budget: dict[str, Any] | None = None
        main_model: dict[str, Any] | None = None
        if sidecar is not None:
            try:
                container_statuses = await sidecar.get_status()
            except SidecarUnavailableError:
                container_statuses = {}
            try:
                budget = await sidecar.gpu_budget()
            except SidecarUnavailableError:
                budget = None
            try:
                main_model = await sidecar.main_model()
            except SidecarUnavailableError:
                main_model = None

        runtimes = []
        for item in _controllable_runtimes():
            sk = item["service_key"]
            container = item["container"]
            record = gateway_records.get(sk)
            state = record.state.value if record is not None else RuntimeState.active.value
            c_status = container_statuses.get(container, "unknown")
            sk_participant = _budget_participant(budget, container)
            runtime = {
                "service_key": sk,
                "container": container,
                "state": state,
                "container_status": c_status,
                "vram_fraction": _budget_fraction(budget, container),
                # 이 런타임이 담당하는 역할; 여기서 축출이 발생하면 무엇이 저하되는지
                "criticality": (sk_participant or {}).get("criticality"),
            }
            if record is not None:
                if record.reason:
                    runtime["state_reason"] = record.reason
                if record.source:
                    runtime["state_source"] = record.source
                if record.updated_at:
                    runtime["state_updated_at"] = record.updated_at
            runtimes.append(runtime)
        # 메인 채팅 모델은 공유 GPU 예산에서 1급 참여자다.
        if main_model is not None:
            active_profile = main_model.get("active_profile") or {}
            main_participant = _main_participant(budget, set(container_to_key))
            runtimes.append({
                "service_key": "main",
                "container": main_participant.get("key") if main_participant else None,
                "state": main_model.get("runtime_state", "active"),
                "container_status": (
                    ("running" if main_participant.get("active") else "stopped")
                    if main_participant
                    else "unknown"
                ),
                "vram_fraction": active_profile.get("vram_fraction"),
                "criticality": (main_participant or {}).get("criticality", "primary_user_path"),
                "gate": main_model.get("gate"),
                "active_profile": active_profile.get("id"),
            })
        body: dict[str, Any] = {"runtimes": runtimes}
        if budget is not None:
            body["budget"] = {
                "ceiling": budget.get("ceiling"),
                "used": budget.get("used"),
                "free": budget.get("free"),
            }
        return JSONResponse(body)

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
                "to_stopped": {"summary": "보조 중지 (VRAM 회수)", "value": RUNTIME_TRANSITION_TO_STOPPED_EXAMPLE},
                "to_stopped_prereq": {"summary": "보조 중지 — 실제 컨테이너 상태 재확인", "value": RUNTIME_TRANSITION_TO_STOPPED_WITH_PREREQ_EXAMPLE},
                "to_active": {"summary": "보조 시작 (서비스 복구)", "value": RUNTIME_TRANSITION_TO_ACTIVE_EXAMPLE},
                "to_active_prereq": {"summary": "보조 시작 — 선행 컨테이너 포함", "value": RUNTIME_TRANSITION_TO_ACTIVE_WITH_PREREQ_EXAMPLE},
                "noop": {"summary": "이미 목표 상태 (no-op)", "value": RUNTIME_TRANSITION_NOOP_EXAMPLE},
                "main_stopped": {"summary": "메인 정지 (key=main, VRAM 회수)", "value": {"service_key": "main", "state": "stopped", "evicted": []}},
                "main_active": {"summary": "메인 시작 (key=main)", "value": {"service_key": "main", "state": "active", "evicted": []}},
                "main_active_force": {"summary": "메인 시작 + 보조 축출 (force)", "value": {"service_key": "main", "state": "active", "evicted": ["risk-prompt-vllm", "embedding-ko-vllm"]}},
            }}}},
            404: {"content": {"application/json": {"example": RUNTIME_ERROR_404_EXAMPLE}}},
            409: {
                "description": "GPU 예산 초과. 표준 error.code=GPU_BUDGET_EXCEEDED와 error.details.plan.stop을 반환한다. force=true로 자동 축출.",
                "content": {"application/json": {"examples": {
                    "budget_exceeded": {"summary": "GPU 예산 초과 + 정지 계획", "value": RUNTIME_BUDGET_EXCEEDED_EXAMPLE},
                }}},
            },
            503: {"content": {"application/json": {"examples": {
                "no_sidecar": {"summary": "sidecar 미설정", "value": RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE},
                "sidecar_unavailable": {"summary": "sidecar 연결 실패", "value": RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE},
                "in_progress": {"summary": "전환 중 (재시도 가능)", "value": RUNTIME_ERROR_503_TRANSITIONING_EXAMPLE},
            }}}},
            **_ADMIN_401,
        },
        openapi_extra=_DESIRED_STATE_SCHEMA,
    )
    async def transition_runtime(service_key: str, request: Request) -> JSONResponse:
        payload = await request.json()
        desired_state = payload.get("desired_state") if isinstance(payload, dict) else None
        if desired_state not in ("active", "stopped"):
            raise HTTPException(422, detail="desired_state must be 'active' or 'stopped'")
        force = bool(payload.get("force")) if isinstance(payload, dict) else False

        # 메인 채팅 모델도 동일 fleet의 예산 참여자이므로, 동일한 desired_state
        # verb로 정지/시작된다. 프로필 변경은 여전히 main 전용 POST
        # /admin/main-model/switch가 담당한다. drain/gate/canary 의미론은
        # sidecar에 있으며, 여기서는 단순히 dispatch만 한다.
        if service_key == "main":
            if sidecar is None:
                raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
            try:
                if desired_state == "active":
                    result = await sidecar.main_start(force=force)
                    for evicted_container in result.get("evicted", []):
                        evicted_key = container_to_key.get(evicted_container)
                        if evicted_key:
                            await state_store.set(
                                evicted_key,
                                RuntimeState.stopped,
                                reason="evicted_by_main_start",
                                source="runtime_control",
                            )
                else:
                    result = await sidecar.main_stop()
            except SidecarRequestError as exc:  # GPU 예산 admission 거부
                return _sidecar_request_error_response(exc)
            except SidecarUnavailableError as exc:
                return _sidecar_unavailable_response(exc)
            return JSONResponse({
                "service_key": "main",
                "state": result.get("runtime_state", desired_state),
                "evicted": result.get("evicted", []),
            })

        _validate_service_key(service_key, state_store)
        current_state = await state_store.get(service_key)
        ep = settings.runtime_endpoints.get(service_key)
        if ep is None:
            raise HTTPException(404, detail=f"runtime endpoint not found: {service_key}")
        container = _container_name(ep.base_url)

        if desired_state == "active":
            if current_state == RuntimeState.active and sidecar is None:
                return JSONResponse({"service_key": service_key, "state": "active", "changed": False})
            if current_state == RuntimeState.active and sidecar is not None:
                try:
                    actual = (await sidecar.get_status()).get(container)
                except SidecarUnavailableError as exc:
                    return _sidecar_unavailable_response(exc)
                if actual == "running":
                    return JSONResponse({"service_key": service_key, "state": "active", "changed": False})
            if current_state == RuntimeState.starting:
                # stop 경로(아래)와 동일한 근거: 실제 컨테이너 상태를 재확인하지
                # 않고 "active"라고 응답하면 아직 준비되지 않은 런타임을 준비된
                # 것처럼 노출하게 된다. retryable=True가 실제로 나가야 하므로
                # _runtime_transitioning_response()를 쓴다(위 주석 참고).
                return _runtime_transitioning_response()
            if sidecar is None:
                raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
            await state_store.set(
                service_key,
                RuntimeState.starting,
                reason="operator_start_requested",
                source="runtime_control",
            )
            try:
                result = await sidecar.start(container, force=force)
            except SidecarRequestError as exc:
                # GPU 예산 admission 거부: 상태 코드와 축출 계획을 그대로 노출한다.
                await state_store.set(
                    service_key,
                    RuntimeState.stopped,
                    reason="start_rejected",
                    source="runtime_control",
                )
                return _sidecar_request_error_response(exc)
            except SidecarUnavailableError as exc:
                await state_store.set(
                    service_key,
                    RuntimeState.stopped,
                    reason="start_sidecar_unavailable",
                    source="runtime_control",
                )
                return _sidecar_unavailable_response(exc)
            started_containers = list(result.get("started", []))
            evicted_containers = list(result.get("evicted", []))
            for evicted_container in evicted_containers:
                evicted_key = container_to_key.get(evicted_container)
                if evicted_key:
                    await state_store.set(
                        evicted_key,
                        RuntimeState.stopped,
                        reason="evicted_by_runtime_start",
                        source="runtime_control",
                    )
            for started_container in started_containers:
                started_key = container_to_key.get(started_container)
                if started_key:
                    await state_store.set(
                        started_key,
                        RuntimeState.active,
                        reason="started_by_runtime_control",
                        source="runtime_control",
                    )
            await state_store.set(
                service_key,
                RuntimeState.active,
                reason="started_by_runtime_control",
                source="runtime_control",
            )
            return JSONResponse({
                "service_key": service_key,
                "state": "active",
                "containers_started": started_containers,
                "evicted": evicted_containers,
            })

        else:  # desired_state == "stopped"
            if current_state == RuntimeState.stopped and sidecar is None:
                return JSONResponse({"service_key": service_key, "state": "stopped", "changed": False})
            if current_state == RuntimeState.stopped and sidecar is not None:
                try:
                    actual = (await sidecar.get_status()).get(container)
                except SidecarUnavailableError as exc:
                    return _sidecar_unavailable_response(exc)
                if actual != "running":
                    return JSONResponse({"service_key": service_key, "state": "stopped", "changed": False})
            if current_state == RuntimeState.starting:
                # 일시적인 "아직 준비 안 됨, 재시도" 상태. retryable=True가 실제로
                # 나가야 하므로 _runtime_transitioning_response()를 쓴다(위 주석 참고).
                return _runtime_transitioning_response()
            if sidecar is None:
                raise HTTPException(503, detail="admin sidecar is not configured (ADMIN_SIDECAR_URL missing)")
            await state_store.set(
                service_key,
                RuntimeState.stopped,
                reason="operator_stop_requested",
                source="runtime_control",
            )
            try:
                stopped = await sidecar.stop(container)
            except SidecarUnavailableError as exc:
                await state_store.set(
                    service_key,
                    RuntimeState.active,
                    reason="stop_sidecar_unavailable",
                    source="runtime_control",
                )
                return _sidecar_unavailable_response(exc)
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
                "description": "마지막 검증 control-plane 상태와 Docker에서 방금 관측한 main-model 상태",
                "content": {
                    "application/json": {"examples": {
                        "active": {
                            "summary": "서비스 가능 (gate open, Docker health healthy)",
                            "value": _MAIN_MODEL_STATUS_EXAMPLE,
                        },
                        "stopped": {
                            "summary": "정지됨 (VRAM 회수, gate closed)",
                            "value": {**_MAIN_MODEL_STATUS_EXAMPLE, "gate": "closed", "runtime_state": "stopped", "observed_runtime": {"status": "stopped", "container_state": "exited", "health": None, "profile_id": "gemma4-12b-unified-fp8", "error": None, "observed_at": 1782086258.2}},
                        },
                        "switch_in_progress": {
                            "summary": "전환 중 (gate closed, 작업 진행)",
                            "value": {
                                **_MAIN_MODEL_STATUS_EXAMPLE,
                                "gate": "closed",
                                "last_operation": {
                                    "id": "8f3c2b10-0000-4000-8000-000000000001",
                                    "requested_profile": "gemma4-12b-unified-fp8",
                                    "previous_profile": "gemma4-26b-a4b-fp8",
                                    "status": "validating",
                                    "stage": "validating",
                                },
                                "observed_runtime": {"status": "starting", "container_state": "running", "health": "starting", "profile_id": "gemma4-12b-unified-fp8", "error": None, "observed_at": 1782086258.2},
                            },
                        },
                    }}
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
            return _sidecar_unavailable_response(exc)

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
                                    "id": "gemma4-26b-a4b-fp8",
                                    "display_name": "Gemma 4 26B A4B FP8",
                                    "served_model_name": "local-main",
                                    "upstream_model_id": "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic",
                                    "revision": "8edbb9269ec9c3faad538ee1208a07eb46051f34",
                                    "compatibility": {"status": "verified"},
                                    "capabilities": {
                                        "deployed_input": ["text", "image"],
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
            return _sidecar_unavailable_response(exc)

    @router.post(
        "/admin/main-model/switch",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="메인 모델 전환",
        description=(
            "메인 모델 프로필을 비동기로 전환한다(202 + operation_id). "
            "`request_id`는 선택적 멱등 키다 — 진행 중이거나 방금 끝난 동일 작업이 있으면 "
            "새 전환을 시작하지 않고 그 작업을 반환하며 응답 `reused=true`로 표시한다(재시도 "
            "안전용이라 일정 시간 뒤에는 만료된다). 새 전환을 원하면 매번 고유한 "
            "`request_id`를 쓰거나 생략한다. 진행 상황은 응답 `operation_id`로 "
            "`GET /admin/main-model/operations/{operation_id}` 또는 "
            "`GET /admin/main-model`의 `last_operation`에서 확인한다."
        ),
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
                            "gemma4_12b": {
                                "summary": "Gemma 4 12B (verified)",
                                "value": {
                                    "profile": "gemma4-12b-unified-fp8",
                                },
                            },
                            "gemma4_26b": {
                                "summary": "Gemma 4 26B (verified)",
                                "value": {
                                    "profile": "gemma4-26b-a4b-fp8",
                                },
                            },
                            "qwen2_5_omni_7b_thinker": {
                                "summary": "Qwen 2.5 Omni 7B Thinker (verified)",
                                "value": {
                                    "profile": "qwen2.5-omni-7b-thinker",
                                },
                            },
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
            return _sidecar_request_error_response(exc)
        except SidecarUnavailableError as exc:
            return _sidecar_unavailable_response(exc)

    @router.get(
        "/admin/main-model/operations/{operation_id}",
        dependencies=admin_dependencies,
        tags=["Runtime Control"],
        summary="메인 모델 전환 작업 조회",
        description=_OPERATION_LOOKUP_DESCRIPTION,
        operation_id="getMainModelOperation",
        responses={
            200: {
                "description": "전환 작업 상태",
                "content": {
                    "application/json": {
                        "schema": _OPERATION_RESPONSE_SCHEMA,
                        "examples": _OPERATION_RESPONSE_EXAMPLES,
                    }
                },
            },
            404: {"description": "operation 없음 (id 오타이거나 만료/미존재)"},
            503: {"description": "Admin Sidecar 연결 실패"},
        },
        openapi_extra={"parameters": [_OPERATION_ID_PARAMETER]},
    )
    async def get_main_model_operation(request: Request) -> JSONResponse:
        operation_id = str(request.path_params["operation_id"])
        client = await require_sidecar()
        try:
            return JSONResponse(await client.main_model_operation(operation_id))
        except SidecarUnavailableError as exc:
            return _sidecar_unavailable_response(exc)

    # 메인 정지/시작은 별도 엔드포인트가 아니다: 메인 모델도 예산 참여자이며
    # 통일된 fleet verb인 PATCH /admin/runtimes/main {desired_state}로
    # 정지/시작된다. main 전용 프로필 변경(switch)만 /admin/main-model
    # 아래에 있다.

    return router
