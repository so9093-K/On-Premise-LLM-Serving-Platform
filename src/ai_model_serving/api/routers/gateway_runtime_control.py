from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError, error_response, request_id_for
from ..error_responses import runtime_controller_request_error_response, runtime_controller_unavailable_response
from ...api_examples import (
    RUNTIME_BUDGET_EXCEEDED_EXAMPLE,
    RUNTIME_ERROR_404_EXAMPLE,
    RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE,
    RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE,
    RUNTIME_ERROR_503_TRANSITIONING_EXAMPLE,
    RUNTIME_LIST_MIXED_STATE_EXAMPLE,
    MAIN_MODEL,
    RUNTIME_LIST_RESPONSE_EXAMPLE,
    RUNTIME_TRANSITION_NOOP_EXAMPLE,
    RUNTIME_TRANSITION_TO_ACTIVE_EXAMPLE,
    RUNTIME_TRANSITION_TO_ACTIVE_WITH_PREREQ_EXAMPLE,
    RUNTIME_TRANSITION_TO_STOPPED_EXAMPLE,
    RUNTIME_TRANSITION_TO_STOPPED_WITH_PREREQ_EXAMPLE,
    alternate_main_model_profile_id,
    default_main_model_profile_id,
    main_model_profile_example,
)
from ...security import AdminAuthContext
from ...runtime_transition_history import (
    RuntimeTransitionHistoryCursorError,
    RuntimeTransitionHistoryNotFound,
    RuntimeTransitionHistoryStore,
    RuntimeTransitionHistoryUnavailable,
)
from ...services.runtime_state import RuntimeState, RuntimeStateStore
from ...services.runtime_controller_client import (
    RuntimeControllerClient,
    RuntimeControllerRequestError,
    RuntimeControllerUnavailableError,
)
from ...runtime_transition_contract import (
    parse_runtime_transition_request,
    project_sidecar_runtime_plan,
)

# 예시의 모델 신원은 configs/main_model_profiles.yaml에서 그대로 온다. 손으로
# 적어두면 default_profile이나 체크포인트 pin이 바뀔 때 /docs와 OpenAPI 스냅샷만
# 옛 모델을 계속 광고한다.
_DEFAULT_PROFILE_ID = default_main_model_profile_id()
_ALTERNATE_PROFILE_ID = alternate_main_model_profile_id()

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}
_OPERATION_ID_EXAMPLE = "41cf50bb-60b2-4dbc-b38a-7dd07da91d97"
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
            "requested_profile": _DEFAULT_PROFILE_ID,
            "previous_profile": _ALTERNATE_PROFILE_ID,
            "client_request_id": "ops-20260622-12b",
            "status": "validating",
            "stage": "validating",
            "error": None,
            "rollback_error": None,
            "recovered_after_restart": False,
            "created_at": 1782086105.78,
            "updated_at": 1782086230.12,
        },
    },
    "completed": {
        "summary": "완료",
        "value": {
            "id": _OPERATION_ID_EXAMPLE,
            "requested_profile": _DEFAULT_PROFILE_ID,
            "previous_profile": _ALTERNATE_PROFILE_ID,
            "client_request_id": "ops-20260622-12b",
            "status": "completed",
            "stage": "completed",
            "error": None,
            "rollback_error": None,
            "recovered_after_restart": False,
            "created_at": 1782086105.78,
            "updated_at": 1782086258.20,
        },
    },
    "failed": {
        "summary": "실패 후 이전 프로필로 rollback",
        "value": {
            "id": _OPERATION_ID_EXAMPLE,
            "requested_profile": _DEFAULT_PROFILE_ID,
            "previous_profile": _ALTERNATE_PROFILE_ID,
            "client_request_id": "ops-20260622-12b",
            "status": "failed",
            "stage": "validating",
            "error": "runtime validation failed: /v1/models did not report local-main",
            "rollback_error": None,
            "recovered_after_restart": False,
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
    "public_model": MAIN_MODEL,
    "active_profile": main_model_profile_example(_DEFAULT_PROFILE_ID),
    "last_known_good_profile": _DEFAULT_PROFILE_ID,
    "previous_known_good_profile": None,
    "gate": "open",
    "runtime_state": "active",
    "profile_locked": False,
    "boot_profile": _DEFAULT_PROFILE_ID,
    "last_operation": None,
    "observed_runtime": {
        "status": "ready",
        "container_state": "running",
        "health": "healthy",
        "profile_id": _DEFAULT_PROFILE_ID,
        "error": None,
        "observed_at": 1782086258.2,
    },
}


async def _runtime_request_json(request: Request) -> Any:
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "VALIDATION_ERROR",
            "Request body must be valid JSON",
            param="body",
        ) from exc


def _container_name(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _runtime_transitioning_response() -> JSONResponse:
    # 시작 중에는 일시 오류와 대기 힌트를 함께 반환한다.
    return error_response(
        "MODEL_UNAVAILABLE",
        "runtime is currently starting; wait and retry",
        retry_after_seconds=5,
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


def _switch_examples(
    profile_summaries: tuple[tuple[str, str, str], ...],
) -> dict[str, dict[str, Any]]:
    """profile 전환 예시를 catalog에서 그대로 만든다.

    손으로 적으면 catalog에 profile을 추가할 때 조용히 빠진다(E4B가 실제로 그랬다).
    `unverified` profile은 `confirm_unverified`가 있어야 전환되므로 예시에도 함께
    싣는다 -- 목록에서 감추는 것보다 필요한 요청 모양을 보여주는 편이 낫다.
    """
    examples: dict[str, dict[str, Any]] = {}
    for profile_id, display_name, status in profile_summaries:
        value: dict[str, Any] = {"profile": profile_id}
        if status != "verified":
            value["confirm_unverified"] = True
        examples[profile_id.replace("-", "_").replace(".", "_")] = {
            "summary": f"{display_name} ({status or 'unknown'})",
            "value": value,
        }
    return examples


def _validate_service_key(service_key: str, state_store: RuntimeStateStore) -> None:
    if service_key not in state_store.controllable_keys:
        raise HTTPException(404, detail=f"runtime endpoint not found: {service_key}")


def _admin_actor(request: Request) -> dict[str, str]:
    context = getattr(request.state, "admin_auth_context", None)
    if not isinstance(context, AdminAuthContext):
        context = AdminAuthContext(auth_method="local", actor_id="local_operator")
    return {"auth_method": context.auth_method, "actor_id": context.actor_id}


def _history_service_error(exc: RuntimeTransitionHistoryUnavailable, request: Request) -> ServiceError:
    return ServiceError(
        "RUNTIME_HISTORY_UNAVAILABLE",
        "Runtime transition history is temporarily unavailable.",
        request_id=request_id_for(request),
        details={"reason": exc.reason},
    )


def _begin_runtime_operation(
    history: RuntimeTransitionHistoryStore,
    request: Request,
    *,
    service_key: str,
    desired_state: str,
    force: bool,
    plan_digest: str | None,
    before: dict[str, Any],
) -> str:
    try:
        return history.begin(
            service_key=service_key,
            desired_state=desired_state,
            force=force,
            plan_digest=plan_digest,
            actor=_admin_actor(request),
            request_id=request_id_for(request),
            before=before,
        )
    except RuntimeTransitionHistoryUnavailable as exc:
        raise _history_service_error(exc, request) from exc


def _finish_runtime_operation(
    history: RuntimeTransitionHistoryStore,
    request: Request,
    operation_id: str,
    *,
    status: str,
    phase: str,
    apply_result: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return history.finish(
            operation_id,
            status=status,
            phase=phase,
            apply_result=apply_result,
            verification=verification,
            error=error,
        )
    except RuntimeTransitionHistoryUnavailable as exc:
        raise _history_service_error(exc, request) from exc


def _with_operation_header(response: JSONResponse, operation_id: str) -> JSONResponse:
    response.headers["X-Control-Operation-ID"] = operation_id
    return response


def _runtime_history_query(request: Request) -> tuple[int, str | None]:
    params = request.query_params
    allowed = {"limit", "cursor"}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"unsupported runtime history query parameter(s): {', '.join(unknown)}",
            request_id=request_id_for(request),
            param="query",
        )
    for name in allowed:
        if len(params.getlist(name)) > 1:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"runtime history query parameter must appear at most once: {name}",
                request_id=request_id_for(request),
                param=name,
            )
    raw_limit = params.get("limit")
    if raw_limit is None:
        limit = 50
    elif raw_limit.isascii() and raw_limit.isdecimal() and 1 <= int(raw_limit) <= 200:
        limit = int(raw_limit)
    else:
        raise ServiceError(
            "VALIDATION_ERROR",
            "limit must be an integer between 1 and 200",
            request_id=request_id_for(request),
            param="limit",
        )
    cursor = params.get("cursor")
    if cursor == "":
        raise ServiceError(
            "VALIDATION_ERROR",
            "runtime history cursor is invalid",
            request_id=request_id_for(request),
            param="cursor",
        )
    return limit, cursor


def _secondary_converged(desired_state: str, observed_state: str | None) -> bool:
    if desired_state == "active":
        return observed_state == "running"
    return observed_state in {"exited", "not_found"}


async def _best_effort_secondary_before(
    sidecar: RuntimeControllerClient | None,
    container: str,
    desired_state: str,
) -> dict[str, Any]:
    observed_state: str | None = None
    if sidecar is not None:
        try:
            observed_state = (await sidecar.get_status()).get(container, "not_found")
        except (RuntimeControllerRequestError, RuntimeControllerUnavailableError):
            observed_state = None
    return {"desired_state": desired_state, "observed_state": observed_state}


async def _verify_secondary_transition(
    sidecar: RuntimeControllerClient,
    *,
    container: str,
    desired_state: str,
    started: list[str],
    stopped: list[str],
    evicted: list[str],
) -> dict[str, Any]:
    statuses = await sidecar.get_status()
    observed_state = statuses.get(container, "not_found")
    effects: dict[str, str] = {}
    for name in sorted(set([*started, *stopped, *evicted])):
        effects[name] = statuses.get(name, "not_found")
    effects_converged = all(statuses.get(name, "not_found") == "running" for name in started)
    effects_converged = effects_converged and all(
        statuses.get(name, "not_found") in {"exited", "not_found"}
        for name in [*stopped, *evicted]
    )
    return {
        "converged": _secondary_converged(desired_state, observed_state) and effects_converged,
        "source": "sidecar_container_status",
        "desired_state": desired_state,
        "observed_state": observed_state,
        "effects": effects,
    }


async def _best_effort_main_before(sidecar: RuntimeControllerClient) -> dict[str, Any]:
    try:
        snapshot = await sidecar.main_model()
    except (RuntimeControllerRequestError, RuntimeControllerUnavailableError):
        return {"runtime_state": None, "observed_state": None}
    observed = snapshot.get("observed_runtime") or {}
    return {
        "runtime_state": snapshot.get("runtime_state"),
        "observed_state": observed.get("status"),
        "container_state": observed.get("container_state"),
    }


async def _verify_main_transition(
    sidecar: RuntimeControllerClient,
    *,
    desired_state: str,
    evicted: list[str],
) -> dict[str, Any]:
    snapshot = await sidecar.main_model()
    observed = snapshot.get("observed_runtime") or {}
    observed_status = observed.get("status")
    container_state = observed.get("container_state")
    runtime_state = snapshot.get("runtime_state")
    if desired_state == "active":
        target_converged = (
            runtime_state == "active"
            and observed_status == "ready"
            and container_state == "running"
        )
    else:
        target_converged = (
            runtime_state == "stopped"
            and observed_status == "stopped"
            and container_state != "running"
        )
    effects: dict[str, str] = {}
    effects_converged = True
    if evicted:
        statuses = await sidecar.get_status()
        for name in sorted(set(evicted)):
            effects[name] = statuses.get(name, "not_found")
        effects_converged = all(state in {"exited", "not_found"} for state in effects.values())
    return {
        "converged": target_converged and effects_converged,
        "source": "main_model_observed",
        "desired_state": desired_state,
        "runtime_state": runtime_state,
        "observed_state": observed_status,
        "container_state": container_state,
        "effects": effects,
    }


def _verification_failure(
    request: Request,
    operation_id: str,
    verification: dict[str, Any],
) -> ServiceError:
    return ServiceError(
        "RUNTIME_VERIFICATION_FAILED",
        "Runtime transition apply could not be verified against observed state.",
        request_id=request_id_for(request),
        details={"operation_id": operation_id, "verification": verification},
    )


def build_router(
    admin_dependencies: list,
    state_store: RuntimeStateStore,
    sidecar: RuntimeControllerClient | None,
    settings: Any,
    history_store: RuntimeTransitionHistoryStore,
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
            except RuntimeControllerUnavailableError:
                container_statuses = {}
            try:
                budget = await sidecar.gpu_budget()
            except RuntimeControllerUnavailableError:
                budget = None
            try:
                # 이 목록은 gate·runtime_state·active profile만 쓴다.
                main_model = await sidecar.main_model(observed=False)
            except RuntimeControllerUnavailableError:
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
    runtime_service_keys = [*sorted(state_store.controllable_keys), "main"]
    service_key_path = Path(
        description="GET /admin/runtimes가 반환하는 service_key.",
        examples=[runtime_service_keys[0]],
        json_schema_extra={"enum": runtime_service_keys},
    )

    plan_spec = _GW[("POST", "/admin/runtimes/{service_key}/plans")]

    @router.post(
        "/admin/runtimes/{service_key}/plans",
        dependencies=admin_dependencies,
        tags=[plan_spec.tag],
        summary=plan_spec.summary,
        operation_id=plan_spec.operation_id,
        description=plan_spec.description,
    )
    async def plan_runtime_transition(
        request: Request,
        service_key: str = service_key_path,
    ) -> JSONResponse:
        desired_state, force, _ = parse_runtime_transition_request(
            await _runtime_request_json(request),
            allow_plan_digest=False,
        )
        if sidecar is None:
            raise ServiceError(
                "MAIN_MODEL_CONTROL_UNAVAILABLE",
                "Runtime Controller is not configured",
                retry_after_seconds=5,
            )
        if service_key == "main":
            sidecar_service = "main"
        else:
            _validate_service_key(service_key, state_store)
            ep = settings.runtime_endpoints.get(service_key)
            if ep is None:
                raise ServiceError("NOT_FOUND", f"runtime endpoint not found: {service_key}")
            sidecar_service = _container_name(ep.base_url)
        try:
            raw_plan = await sidecar.runtime_plan(
                sidecar_service,
                desired_state=desired_state,
                force=force,
            )
        except RuntimeControllerRequestError as exc:
            return runtime_controller_request_error_response(exc)
        except RuntimeControllerUnavailableError as exc:
            return runtime_controller_unavailable_response(exc)
        return JSONResponse(
            project_sidecar_runtime_plan(
                raw_plan,
                service_key=service_key,
                container_to_key=container_to_key,
            )
        )


    history_spec = _GW[("GET", "/admin/runtimes/operations")]

    @router.get(
        "/admin/runtimes/operations",
        dependencies=admin_dependencies,
        tags=[history_spec.tag],
        summary=history_spec.summary,
        operation_id=history_spec.operation_id,
        description=history_spec.description,
    )
    async def list_runtime_transition_operations(request: Request) -> JSONResponse:
        limit, cursor = _runtime_history_query(request)
        try:
            return JSONResponse(history_store.page(limit=limit, cursor=cursor))
        except RuntimeTransitionHistoryCursorError as exc:
            raise ServiceError(
                "VALIDATION_ERROR",
                str(exc),
                request_id=request_id_for(request),
                param="cursor",
            ) from exc
        except RuntimeTransitionHistoryUnavailable as exc:
            raise _history_service_error(exc, request) from exc

    history_detail_spec = _GW[("GET", "/admin/runtimes/operations/{operation_id}")]

    @router.get(
        "/admin/runtimes/operations/{operation_id}",
        dependencies=admin_dependencies,
        tags=[history_detail_spec.tag],
        summary=history_detail_spec.summary,
        operation_id=history_detail_spec.operation_id,
        description=history_detail_spec.description,
        openapi_extra={
            "parameters": [
                {
                    "name": "operation_id",
                    "in": "path",
                    "required": True,
                    "description": "Runtime transition operation id (`rt_<32 hex>`).",
                    "schema": {"type": "string", "pattern": "^rt_[0-9a-f]{32}$"},
                }
            ]
        },
    )
    async def get_runtime_transition_operation(request: Request) -> JSONResponse:
        operation_id = str(request.path_params["operation_id"])
        try:
            return JSONResponse(history_store.get(operation_id))
        except RuntimeTransitionHistoryNotFound as exc:
            raise ServiceError(
                "NOT_FOUND",
                "runtime transition operation not found",
                request_id=request_id_for(request),
            ) from exc
        except RuntimeTransitionHistoryUnavailable as exc:
            raise _history_service_error(exc, request) from exc

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
                "no_sidecar": {"summary": "Runtime Controller 미설정", "value": RUNTIME_ERROR_503_NO_SIDECAR_EXAMPLE},
                "sidecar_unavailable": {"summary": "Runtime Controller 연결 실패", "value": RUNTIME_ERROR_503_SIDECAR_UNAVAILABLE_EXAMPLE},
                "in_progress": {"summary": "전환 중 (재시도 가능)", "value": RUNTIME_ERROR_503_TRANSITIONING_EXAMPLE},
            }}}},
            **_ADMIN_401,
        },
    )
    async def transition_runtime(
        request: Request,
        service_key: str = service_key_path,
    ) -> JSONResponse:
        desired_state, force, plan_digest = parse_runtime_transition_request(
            await _runtime_request_json(request),
            allow_plan_digest=True,
        )

        if service_key == "main":
            if sidecar is None:
                raise HTTPException(503, detail="Runtime Controller is not configured (RUNTIME_CONTROLLER_URL missing)")
            before = await _best_effort_main_before(sidecar)
            operation_id = _begin_runtime_operation(
                history_store,
                request,
                service_key=service_key,
                desired_state=desired_state,
                force=force,
                plan_digest=plan_digest,
                before=before,
            )
            try:
                if desired_state == "active":
                    result = (
                        await sidecar.main_start(force=force, plan_digest=plan_digest)
                        if plan_digest
                        else await sidecar.main_start(force=force)
                    )
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
                    result = (
                        await sidecar.main_stop(plan_digest=plan_digest)
                        if plan_digest
                        else await sidecar.main_stop()
                    )
            except RuntimeControllerRequestError as exc:
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="rejected",
                    phase="apply",
                    apply_result=None,
                    verification=None,
                    error={"code": "sidecar_request_rejected", "status_code": exc.status_code},
                )
                return _with_operation_header(runtime_controller_request_error_response(exc), operation_id)
            except RuntimeControllerUnavailableError as exc:
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="apply_failed",
                    phase="apply",
                    apply_result=None,
                    verification=None,
                    error={"code": "sidecar_unavailable"},
                )
                return _with_operation_header(runtime_controller_unavailable_response(exc), operation_id)

            apply_result = {
                "state": result.get("runtime_state", desired_state),
                "evicted": list(result.get("evicted", [])),
            }
            try:
                verification = await _verify_main_transition(
                    sidecar,
                    desired_state=desired_state,
                    evicted=apply_result["evicted"],
                )
            except (RuntimeControllerRequestError, RuntimeControllerUnavailableError):
                verification = {
                    "converged": False,
                    "source": "main_model_observed",
                    "desired_state": desired_state,
                    "reason": "observation_unavailable",
                }
            if not verification["converged"]:
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="verification_failed",
                    phase="verify",
                    apply_result=apply_result,
                    verification=verification,
                    error={"code": "verification_failed"},
                )
                raise _verification_failure(request, operation_id, verification)
            _finish_runtime_operation(
                history_store,
                request,
                operation_id,
                status="verified",
                phase="completed",
                apply_result=apply_result,
                verification=verification,
            )
            return JSONResponse({
                "service_key": "main",
                "state": apply_result["state"],
                "evicted": apply_result["evicted"],
                "operation_id": operation_id,
                "operation_status": "verified",
                "verification": verification,
            })

        _validate_service_key(service_key, state_store)
        current_state = await state_store.get(service_key)
        ep = settings.runtime_endpoints.get(service_key)
        if ep is None:
            raise HTTPException(404, detail=f"runtime endpoint not found: {service_key}")
        container = _container_name(ep.base_url)
        actual: str | None = None

        if desired_state == "active":
            if current_state == RuntimeState.active and sidecar is None and plan_digest is None:
                before = {"desired_state": current_state.value, "observed_state": None}
                operation_id = _begin_runtime_operation(
                    history_store,
                    request,
                    service_key=service_key,
                    desired_state=desired_state,
                    force=force,
                    plan_digest=plan_digest,
                    before=before,
                )
                verification = {
                    "converged": None,
                    "source": "desired_state_only",
                    "desired_state": desired_state,
                    "observed_state": None,
                    "reason": "sidecar_unconfigured",
                }
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="noop",
                    phase="completed",
                    apply_result={"changed": False},
                    verification=verification,
                )
                return JSONResponse({
                    "service_key": service_key,
                    "state": "active",
                    "changed": False,
                    "operation_id": operation_id,
                    "operation_status": "noop",
                    "verification": verification,
                })
            if current_state == RuntimeState.active and sidecar is not None:
                try:
                    actual = (await sidecar.get_status()).get(container, "not_found")
                except RuntimeControllerRequestError as exc:
                    return runtime_controller_request_error_response(exc)
                except RuntimeControllerUnavailableError as exc:
                    return runtime_controller_unavailable_response(exc)
                if actual == "running" and plan_digest is None:
                    operation_id = _begin_runtime_operation(
                        history_store,
                        request,
                        service_key=service_key,
                        desired_state=desired_state,
                        force=force,
                        plan_digest=plan_digest,
                        before={"desired_state": current_state.value, "observed_state": actual},
                    )
                    verification = {
                        "converged": True,
                        "source": "sidecar_container_status",
                        "desired_state": desired_state,
                        "observed_state": actual,
                        "effects": {},
                    }
                    _finish_runtime_operation(
                        history_store,
                        request,
                        operation_id,
                        status="noop",
                        phase="completed",
                        apply_result={"changed": False},
                        verification=verification,
                    )
                    return JSONResponse({
                        "service_key": service_key,
                        "state": "active",
                        "changed": False,
                        "operation_id": operation_id,
                        "operation_status": "noop",
                        "verification": verification,
                    })
            if current_state == RuntimeState.starting:
                return _runtime_transitioning_response()
            if sidecar is None:
                raise HTTPException(503, detail="Runtime Controller is not configured (RUNTIME_CONTROLLER_URL missing)")
            before = {
                "desired_state": current_state.value,
                "observed_state": actual,
            }
            if actual is None:
                observed = await _best_effort_secondary_before(sidecar, container, current_state.value)
                before["observed_state"] = observed["observed_state"]
            operation_id = _begin_runtime_operation(
                history_store,
                request,
                service_key=service_key,
                desired_state=desired_state,
                force=force,
                plan_digest=plan_digest,
                before=before,
            )
            await state_store.set(
                service_key,
                RuntimeState.starting,
                reason="operator_start_requested",
                source="runtime_control",
            )
            try:
                result = (
                    await sidecar.start(container, force=force, plan_digest=plan_digest)
                    if plan_digest
                    else await sidecar.start(container, force=force)
                )
            except RuntimeControllerRequestError as exc:
                await state_store.set(
                    service_key,
                    RuntimeState.stopped,
                    reason="start_rejected",
                    source="runtime_control",
                )
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="rejected",
                    phase="apply",
                    apply_result=None,
                    verification=None,
                    error={"code": "sidecar_request_rejected", "status_code": exc.status_code},
                )
                return _with_operation_header(runtime_controller_request_error_response(exc), operation_id)
            except RuntimeControllerUnavailableError as exc:
                await state_store.set(
                    service_key,
                    RuntimeState.stopped,
                    reason="start_sidecar_unavailable",
                    source="runtime_control",
                )
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="apply_failed",
                    phase="apply",
                    apply_result=None,
                    verification=None,
                    error={"code": "sidecar_unavailable"},
                )
                return _with_operation_header(runtime_controller_unavailable_response(exc), operation_id)
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
            apply_result = {
                "containers_started": started_containers,
                "evicted": evicted_containers,
            }
            try:
                verification = await _verify_secondary_transition(
                    sidecar,
                    container=container,
                    desired_state=desired_state,
                    started=started_containers,
                    stopped=[],
                    evicted=evicted_containers,
                )
            except (RuntimeControllerRequestError, RuntimeControllerUnavailableError):
                verification = {
                    "converged": False,
                    "source": "sidecar_container_status",
                    "desired_state": desired_state,
                    "observed_state": None,
                    "reason": "observation_unavailable",
                }
            if not verification["converged"]:
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="verification_failed",
                    phase="verify",
                    apply_result=apply_result,
                    verification=verification,
                    error={"code": "verification_failed"},
                )
                raise _verification_failure(request, operation_id, verification)
            _finish_runtime_operation(
                history_store,
                request,
                operation_id,
                status="verified",
                phase="completed",
                apply_result=apply_result,
                verification=verification,
            )
            return JSONResponse({
                "service_key": service_key,
                "state": "active",
                **apply_result,
                "operation_id": operation_id,
                "operation_status": "verified",
                "verification": verification,
            })

        if current_state == RuntimeState.stopped and sidecar is None and plan_digest is None:
            before = {"desired_state": current_state.value, "observed_state": None}
            operation_id = _begin_runtime_operation(
                history_store,
                request,
                service_key=service_key,
                desired_state=desired_state,
                force=force,
                plan_digest=plan_digest,
                before=before,
            )
            verification = {
                "converged": None,
                "source": "desired_state_only",
                "desired_state": desired_state,
                "observed_state": None,
                "reason": "sidecar_unconfigured",
            }
            _finish_runtime_operation(
                history_store,
                request,
                operation_id,
                status="noop",
                phase="completed",
                apply_result={"changed": False},
                verification=verification,
            )
            return JSONResponse({
                "service_key": service_key,
                "state": "stopped",
                "changed": False,
                "operation_id": operation_id,
                "operation_status": "noop",
                "verification": verification,
            })
        if current_state == RuntimeState.stopped and sidecar is not None:
            try:
                actual = (await sidecar.get_status()).get(container, "not_found")
            except RuntimeControllerRequestError as exc:
                return runtime_controller_request_error_response(exc)
            except RuntimeControllerUnavailableError as exc:
                return runtime_controller_unavailable_response(exc)
            if actual in {"exited", "not_found"} and plan_digest is None:
                operation_id = _begin_runtime_operation(
                    history_store,
                    request,
                    service_key=service_key,
                    desired_state=desired_state,
                    force=force,
                    plan_digest=plan_digest,
                    before={"desired_state": current_state.value, "observed_state": actual},
                )
                verification = {
                    "converged": True,
                    "source": "sidecar_container_status",
                    "desired_state": desired_state,
                    "observed_state": actual,
                    "effects": {},
                }
                _finish_runtime_operation(
                    history_store,
                    request,
                    operation_id,
                    status="noop",
                    phase="completed",
                    apply_result={"changed": False},
                    verification=verification,
                )
                return JSONResponse({
                    "service_key": service_key,
                    "state": "stopped",
                    "changed": False,
                    "operation_id": operation_id,
                    "operation_status": "noop",
                    "verification": verification,
                })
        if current_state == RuntimeState.starting:
            return _runtime_transitioning_response()
        if sidecar is None:
            raise HTTPException(503, detail="Runtime Controller is not configured (RUNTIME_CONTROLLER_URL missing)")
        before = {"desired_state": current_state.value, "observed_state": actual}
        if actual is None:
            observed = await _best_effort_secondary_before(sidecar, container, current_state.value)
            before["observed_state"] = observed["observed_state"]
        operation_id = _begin_runtime_operation(
            history_store,
            request,
            service_key=service_key,
            desired_state=desired_state,
            force=force,
            plan_digest=plan_digest,
            before=before,
        )
        await state_store.set(
            service_key,
            RuntimeState.stopped,
            reason="operator_stop_requested",
            source="runtime_control",
        )
        try:
            stopped_containers = (
                await sidecar.stop(container, plan_digest=plan_digest)
                if plan_digest
                else await sidecar.stop(container)
            )
        except RuntimeControllerRequestError as exc:
            _finish_runtime_operation(
                history_store,
                request,
                operation_id,
                status="rejected",
                phase="apply",
                apply_result=None,
                verification=None,
                error={"code": "sidecar_request_rejected", "status_code": exc.status_code},
            )
            return _with_operation_header(runtime_controller_request_error_response(exc), operation_id)
        except RuntimeControllerUnavailableError as exc:
            await state_store.set(
                service_key,
                RuntimeState.active,
                reason="stop_sidecar_unavailable",
                source="runtime_control",
            )
            _finish_runtime_operation(
                history_store,
                request,
                operation_id,
                status="apply_failed",
                phase="apply",
                apply_result=None,
                verification=None,
                error={"code": "sidecar_unavailable"},
            )
            return _with_operation_header(runtime_controller_unavailable_response(exc), operation_id)
        stopped = list(stopped_containers)
        apply_result = {"containers_stopped": stopped}
        try:
            verification = await _verify_secondary_transition(
                sidecar,
                container=container,
                desired_state=desired_state,
                started=[],
                stopped=stopped,
                evicted=[],
            )
        except (RuntimeControllerRequestError, RuntimeControllerUnavailableError):
            verification = {
                "converged": False,
                "source": "sidecar_container_status",
                "desired_state": desired_state,
                "observed_state": None,
                "reason": "observation_unavailable",
            }
        if not verification["converged"]:
            _finish_runtime_operation(
                history_store,
                request,
                operation_id,
                status="verification_failed",
                phase="verify",
                apply_result=apply_result,
                verification=verification,
                error={"code": "verification_failed"},
            )
            raise _verification_failure(request, operation_id, verification)
        _finish_runtime_operation(
            history_store,
            request,
            operation_id,
            status="verified",
            phase="completed",
            apply_result=apply_result,
            verification=verification,
        )
        return JSONResponse({
            "service_key": service_key,
            "state": "stopped",
            **apply_result,
            "operation_id": operation_id,
            "operation_status": "verified",
            "verification": verification,
        })

    async def require_sidecar() -> RuntimeControllerClient:
        if sidecar is None:
            raise ServiceError(
                "MAIN_MODEL_CONTROL_UNAVAILABLE",
                "Runtime Controller is not configured",
                retry_after_seconds=5,
            )
        return sidecar

    _s = _GW[("GET", "/admin/main-model")]

    @router.get(
        "/admin/main-model",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
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
                            "value": {**_MAIN_MODEL_STATUS_EXAMPLE, "gate": "closed", "runtime_state": "stopped", "observed_runtime": {"status": "stopped", "container_state": "exited", "health": None, "profile_id": _DEFAULT_PROFILE_ID, "error": None, "observed_at": 1782086258.2}},
                        },
                        "switch_in_progress": {
                            "summary": "전환 중 (gate closed, 작업 진행)",
                            "value": {
                                **_MAIN_MODEL_STATUS_EXAMPLE,
                                "gate": "closed",
                                "last_operation": {
                                    "id": "8f3c2b10-0000-4000-8000-000000000001",
                                    "requested_profile": _DEFAULT_PROFILE_ID,
                                    "previous_profile": _ALTERNATE_PROFILE_ID,
                                    "status": "validating",
                                    "stage": "validating",
                                },
                                "observed_runtime": {"status": "starting", "container_state": "running", "health": "starting", "profile_id": _DEFAULT_PROFILE_ID, "error": None, "observed_at": 1782086258.2},
                            },
                        },
                    }}
                },
            },
            503: {"description": "Runtime Controller 연결 또는 상태 파일 오류"},
        },
    )
    async def get_main_model() -> JSONResponse:
        client = await require_sidecar()
        try:
            return JSONResponse(await client.main_model())
        except RuntimeControllerRequestError as exc:
            # 4xx는 요청이 잘못된 것이다. control plane 장애(503, retryable)로
            # 보고하면 성공할 수 없는 요청을 계속 재시도하게 된다.
            return runtime_controller_request_error_response(exc)
        except RuntimeControllerUnavailableError as exc:
            return runtime_controller_unavailable_response(exc)

    _s = _GW[("GET", "/admin/main-model/profiles")]

    @router.get(
        "/admin/main-model/profiles",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            200: {
                "description": "허용된 프로필 목록",
                "content": {
                    "application/json": {
                        "example": {
                            "profiles": [
                                main_model_profile_example(_DEFAULT_PROFILE_ID, active=True),
                                main_model_profile_example(_ALTERNATE_PROFILE_ID, active=False),
                            ]
                        }
                    }
                },
            },
            503: {"description": "Runtime Controller 연결 실패"},
        },
    )
    async def list_main_model_profiles() -> JSONResponse:
        client = await require_sidecar()
        try:
            return JSONResponse({"profiles": await client.main_model_profiles()})
        except RuntimeControllerRequestError as exc:
            # 4xx는 요청이 잘못된 것이다. control plane 장애(503, retryable)로
            # 보고하면 성공할 수 없는 요청을 계속 재시도하게 된다.
            return runtime_controller_request_error_response(exc)
        except RuntimeControllerUnavailableError as exc:
            return runtime_controller_unavailable_response(exc)

    _s = _GW[("POST", "/admin/main-model/switch")]

    @router.post(
        "/admin/main-model/switch",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        description=_s.description,
        operation_id=_s.operation_id,
        status_code=202,
        responses={
            202: {
                "description": "전환 작업 접수",
                "content": {"application/json": {"schema": _ACCEPTED_SCHEMA}},
            },
            409: {"description": "전환 중, locked, confirmation 필요 또는 request_id 충돌"},
            422: {"description": "잘못된 profile 또는 request"},
            503: {"description": "Runtime Controller 연결 실패"},
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
                        "examples": _switch_examples(settings.main_model_profile_summaries),
                    }
                },
            }
        },
    )
    async def switch_main_model(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("profile"), str):
            raise ServiceError("VALIDATION_ERROR", "profile is required", param="profile")
        unknown = set(payload) - {"profile", "confirm_unverified", "request_id"}
        if unknown:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"unsupported fields: {sorted(unknown)}",
                param=sorted(unknown)[0],
            )
        client = await require_sidecar()
        try:
            result = await client.switch_main_model(
                payload["profile"],
                confirm_unverified=payload.get("confirm_unverified") is True,
                request_id=payload.get("request_id"),
            )
            return JSONResponse(result, status_code=202)
        except RuntimeControllerRequestError as exc:
            return runtime_controller_request_error_response(exc)
        except RuntimeControllerUnavailableError as exc:
            return runtime_controller_unavailable_response(exc)

    _s = _GW[("GET", "/admin/main-model/operations")]

    @router.get(
        "/admin/main-model/operations",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        description=_s.description,
        operation_id=_s.operation_id,
        responses={
            200: {
                "description": "최근 보존된 Main Model profile switch operation (최신 순)",
                "content": {
                    "application/json": {
                        "example": {
                            "items": [_OPERATION_RESPONSE_EXAMPLES["completed"]["value"]]
                        }
                    }
                },
            },
            503: {"description": "Runtime Controller 연결 실패"},
        },
    )
    async def list_main_model_operations() -> JSONResponse:
        client = await require_sidecar()
        try:
            return JSONResponse(await client.main_model_operations())
        except RuntimeControllerRequestError as exc:
            return runtime_controller_request_error_response(exc)
        except RuntimeControllerUnavailableError as exc:
            return runtime_controller_unavailable_response(exc)

    _s = _GW[("GET", "/admin/main-model/operations/{operation_id}")]

    @router.get(
        "/admin/main-model/operations/{operation_id}",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        description=_s.description,
        operation_id=_s.operation_id,
        responses={
            200: {
                "description": "전환 작업 상태",
                "content": {
                    "application/json": {
                        "examples": _OPERATION_RESPONSE_EXAMPLES,
                    }
                },
            },
            404: {"description": "operation 없음 (id 오타이거나 만료/미존재)"},
            503: {"description": "Runtime Controller 연결 실패"},
        },
        openapi_extra={"parameters": [_OPERATION_ID_PARAMETER]},
    )
    async def get_main_model_operation(request: Request) -> JSONResponse:
        operation_id = str(request.path_params["operation_id"])
        client = await require_sidecar()
        try:
            return JSONResponse(await client.main_model_operation(operation_id))
        except RuntimeControllerRequestError as exc:
            # 404(없는 operation)는 여기서 NOT_FOUND / retryable=false로 나간다.
            return runtime_controller_request_error_response(exc)
        except RuntimeControllerUnavailableError as exc:
            return runtime_controller_unavailable_response(exc)

    # 메인 정지/시작은 별도 엔드포인트가 아니다: 메인 모델도 예산 참여자이며
    # 통일된 fleet verb인 PATCH /admin/runtimes/main {desired_state}로
    # 정지/시작된다. main 전용 프로필 변경(switch)만 /admin/main-model
    # 아래에 있다.

    return router
