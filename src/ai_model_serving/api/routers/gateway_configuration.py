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
        openapi_extra={
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
        },
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

    return router
