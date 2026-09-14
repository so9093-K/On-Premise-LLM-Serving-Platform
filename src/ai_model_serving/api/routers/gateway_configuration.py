from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response

from ...configuration_mutation import (
    ConfigurationApplyFailure,
    ConfigurationMutationEngine,
    ConfigurationPreconditionRequired,
    ConfigurationRevisionConflict,
    ConfigurationValidationError,
    ConfigurationWriteUnavailable,
    configuration_etag,
    parse_apply_request,
    parse_configuration_if_match,
    parse_plan_request,
    parse_rollback_apply_request,
    parse_rollback_plan_request,
)
from ...configuration_plane import configuration_schema, effective_configuration
from ...errors import ServiceError, request_id_for
from ...operator_configuration import ConfigurationValueResolver
from ...security import AdminAuthContext
from ..endpoint_spec import GATEWAY_ENDPOINTS

_GW = {(spec.method, spec.path): spec for spec in GATEWAY_ENDPOINTS}


async def _request_json(request: Request) -> Any:
    """직접 Request를 받는 mutation route도 표준 422 JSON 경계를 유지한다."""
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigurationValidationError(
            "Request body must be valid JSON",
            param="body",
        ) from exc


def _mutation_error(exc: Exception, request: Request) -> ServiceError:
    request_id = request_id_for(request)
    if isinstance(exc, ConfigurationValidationError):
        return ServiceError(
            "VALIDATION_ERROR",
            str(exc),
            request_id=request_id,
            param=exc.param,
        )
    if isinstance(exc, ConfigurationPreconditionRequired):
        return ServiceError(
            "PRECONDITION_REQUIRED",
            str(exc),
            request_id=request_id,
        )
    if isinstance(exc, ConfigurationRevisionConflict):
        details: dict[str, Any] = {"reason": exc.reason}
        if exc.current_revision is not None:
            details["current_revision"] = exc.current_revision
        return ServiceError(
            "CONFIG_REVISION_CONFLICT",
            str(exc),
            request_id=request_id,
            details=details,
        )
    if isinstance(exc, ConfigurationWriteUnavailable):
        # History/operator state 오류에는 host 경로 같은 내부 진단이 들어갈 수 있다.
        # 원인은 exception chain을 통해 요청 로그에 남기되 공개 응답은 안정된 문구와
        # machine-readable reason만 제공한다.
        return ServiceError(
            "CONFIGURATION_WRITE_UNAVAILABLE",
            "Configuration write plane is temporarily unavailable.",
            request_id=request_id,
            details={"reason": exc.reason},
        )
    if isinstance(exc, ConfigurationApplyFailure):
        return ServiceError(
            "CONFIGURATION_APPLY_FAILED",
            str(exc),
            request_id=request_id,
            details={
                "operation_id": exc.operation_id,
                "phase": exc.phase,
                "desired_revision": exc.desired_revision,
                "verification": exc.verification,
            },
        )
    raise exc


def _admin_actor(request: Request) -> dict[str, str]:
    context = getattr(request.state, "admin_auth_context", None)
    if not isinstance(context, AdminAuthContext):
        # Admin auth dependency가 비활성인 local profile은 dependency 자체가 등록되지
        # 않으므로 handler가 같은 actor abstraction을 직접 사용한다.
        context = AdminAuthContext(auth_method="local", actor_id="local_operator")
    return {
        "auth_method": context.auth_method,
        "actor_id": context.actor_id,
    }


def _if_match_openapi() -> dict[str, Any]:
    return {
        "parameters": [
            {
                "name": "If-Match",
                "in": "header",
                "required": True,
                "description": (
                    "`GET /admin/config/effective`가 반환한 Configuration ETag입니다. "
                    "예: `\"config-7\"`. 누락되거나 형식이 잘못되면 428을 반환합니다."
                ),
                "schema": {
                    "type": "string",
                    "pattern": '^"config-[0-9]+"$',
                },
            }
        ]
    }


def _history_openapi() -> dict[str, Any]:
    return {
        "parameters": [
            {
                "name": "limit",
                "in": "query",
                "required": False,
                "description": "한 페이지에 반환할 operation 수입니다. 기본 50, 최대 200입니다.",
                "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            {
                "name": "cursor",
                "in": "query",
                "required": False,
                "description": "직전 History 응답의 `next_cursor`입니다.",
                "schema": {"type": "string", "minLength": 1},
            },
        ]
    }


def _history_query(request: Request) -> tuple[int, str | None]:
    params = request.query_params
    allowed = {"limit", "cursor"}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ConfigurationValidationError(
            f"unsupported history query parameter(s): {', '.join(unknown)}",
            param="query",
        )
    for name in allowed:
        if len(params.getlist(name)) > 1:
            raise ConfigurationValidationError(
                f"history query parameter must appear at most once: {name}",
                param=name,
            )

    raw_limit = params.get("limit")
    if raw_limit is None:
        limit = 50
    else:
        if not raw_limit.isascii() or not raw_limit.isdecimal():
            raise ConfigurationValidationError(
                "limit must be an integer between 1 and 200",
                param="limit",
            )
        limit = int(raw_limit)

    cursor = params.get("cursor")
    if cursor == "":
        raise ConfigurationValidationError("history cursor is invalid", param="cursor")
    return limit, cursor


def build_router(
    admin_dependencies: list,
    settings: Any,
    resolver: ConfigurationValueResolver,
    mutation: ConfigurationMutationEngine,
) -> APIRouter:
    router = APIRouter()
    schema_spec = _GW[("GET", "/admin/config/schema")]
    effective_spec = _GW[("GET", "/admin/config/effective")]
    plan_spec = _GW[("POST", "/admin/config/plans")]
    apply_spec = _GW[("PATCH", "/admin/config")]
    history_spec = _GW[("GET", "/admin/config/history")]
    rollback_plan_spec = _GW[("POST", "/admin/config/rollbacks/plans")]
    rollback_apply_spec = _GW[("POST", "/admin/config/rollbacks")]

    @router.get(
        "/admin/config/schema", dependencies=admin_dependencies,
        tags=[schema_spec.tag], summary=schema_spec.summary,
        description=schema_spec.description, operation_id=schema_spec.operation_id,
    )
    async def config_schema() -> dict[str, Any]:
        return configuration_schema(write_status=mutation.status())

    @router.get(
        "/admin/config/effective", dependencies=admin_dependencies,
        tags=[effective_spec.tag], summary=effective_spec.summary,
        description=effective_spec.description, operation_id=effective_spec.operation_id,
    )
    async def config_effective(response: Response) -> dict[str, Any]:
        body = effective_configuration(
            settings,
            resolver,
            write_status=mutation.status(),
        )
        response.headers["ETag"] = configuration_etag(body["revision"])
        return body

    @router.get(
        "/admin/config/history", dependencies=admin_dependencies,
        tags=[history_spec.tag], summary=history_spec.summary,
        description=history_spec.description, operation_id=history_spec.operation_id,
        openapi_extra=_history_openapi(),
    )
    async def config_history(request: Request) -> dict[str, Any]:
        try:
            limit, cursor = _history_query(request)
            return mutation.history_page(limit=limit, cursor=cursor)
        except (ConfigurationValidationError, ConfigurationWriteUnavailable) as exc:
            raise _mutation_error(exc, request) from exc

    @router.post(
        "/admin/config/plans", dependencies=admin_dependencies,
        tags=[plan_spec.tag], summary=plan_spec.summary,
        description=plan_spec.description, operation_id=plan_spec.operation_id,
    )
    async def plan_config_change(request: Request) -> dict[str, Any]:
        try:
            base_revision, changes = parse_plan_request(await _request_json(request))
            return mutation.plan(base_revision=base_revision, changes=changes)
        except (
            ConfigurationValidationError,
            ConfigurationRevisionConflict,
            ConfigurationWriteUnavailable,
        ) as exc:
            raise _mutation_error(exc, request) from exc

    @router.patch(
        "/admin/config", dependencies=admin_dependencies,
        tags=[apply_spec.tag], summary=apply_spec.summary,
        description=apply_spec.description, operation_id=apply_spec.operation_id,
        openapi_extra=_if_match_openapi(),
    )
    async def apply_config_change(request: Request, response: Response) -> dict[str, Any]:
        try:
            expected_revision = parse_configuration_if_match(request.headers.get("if-match"))
            plan_digest, changes = parse_apply_request(await _request_json(request))
            result = mutation.apply(
                expected_revision=expected_revision,
                changes=changes,
                plan_digest=plan_digest,
                actor=_admin_actor(request),
                request_id=request_id_for(request),
            )
            response.headers["ETag"] = configuration_etag(result["revision"])
            return result
        except (
            ConfigurationApplyFailure,
            ConfigurationPreconditionRequired,
            ConfigurationRevisionConflict,
            ConfigurationValidationError,
            ConfigurationWriteUnavailable,
        ) as exc:
            raise _mutation_error(exc, request) from exc

    @router.post(
        "/admin/config/rollbacks/plans", dependencies=admin_dependencies,
        tags=[rollback_plan_spec.tag], summary=rollback_plan_spec.summary,
        description=rollback_plan_spec.description, operation_id=rollback_plan_spec.operation_id,
    )
    async def plan_config_rollback(request: Request) -> dict[str, Any]:
        try:
            base_revision, target_revision = parse_rollback_plan_request(
                await _request_json(request)
            )
            return mutation.rollback_plan(
                base_revision=base_revision,
                target_revision=target_revision,
            )
        except (
            ConfigurationValidationError,
            ConfigurationRevisionConflict,
            ConfigurationWriteUnavailable,
        ) as exc:
            raise _mutation_error(exc, request) from exc

    @router.post(
        "/admin/config/rollbacks", dependencies=admin_dependencies,
        tags=[rollback_apply_spec.tag], summary=rollback_apply_spec.summary,
        description=rollback_apply_spec.description, operation_id=rollback_apply_spec.operation_id,
        openapi_extra=_if_match_openapi(),
    )
    async def apply_config_rollback(request: Request, response: Response) -> dict[str, Any]:
        try:
            expected_revision = parse_configuration_if_match(request.headers.get("if-match"))
            target_revision, plan_digest = parse_rollback_apply_request(
                await _request_json(request)
            )
            result = mutation.rollback(
                expected_revision=expected_revision,
                target_revision=target_revision,
                plan_digest=plan_digest,
                actor=_admin_actor(request),
                request_id=request_id_for(request),
            )
            response.headers["ETag"] = configuration_etag(result["revision"])
            return result
        except (
            ConfigurationApplyFailure,
            ConfigurationPreconditionRequired,
            ConfigurationRevisionConflict,
            ConfigurationValidationError,
            ConfigurationWriteUnavailable,
        ) as exc:
            raise _mutation_error(exc, request) from exc

    return router
