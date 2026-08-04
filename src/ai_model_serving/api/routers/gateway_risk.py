from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Request

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError
from ...logging_policy import record_request_response_preview, record_token_usage
from ...services.runtime_state import RuntimeState, RuntimeStateStore
from ...settings import AppSettings

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def build_router(
    api_dependencies: list,
    service: Any,
    settings: AppSettings,
    state_store: RuntimeStateStore | None = None,
) -> APIRouter:
    router = APIRouter()

    def _record_if_enabled(request: Request, *, payload: dict[str, Any], response: dict[str, Any]) -> None:
        record_token_usage(request, response.get("usage"))
        if settings.log_request_response_body:
            record_request_response_preview(
                request,
                request_text=str(payload.get("prompt", "")),
                response_text=json.dumps(response, ensure_ascii=False, sort_keys=True),
            )

    _s = _GW[("POST", "/v1/risk/detectors/prompt/assessments")]

    @router.post(
        "/v1/risk/detectors/prompt/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def risk_prompt_assessment(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if state_store is not None:
            state = await state_store.get("risk_prompt")
            if state in (RuntimeState.stopped, RuntimeState.starting):
                raise ServiceError(
                    "MODEL_UNAVAILABLE",
                    "risk_prompt runtime is "
                    f"{state.value}. Start it with PATCH /admin/runtimes/risk_prompt.",
                    True,
                    503,
                )
        result = await service.forward_risk_assessment("/v1/risk/detectors/prompt/assessments", payload)
        _record_if_enabled(request, payload=payload, response=result)
        return result

    _s = _GW[("POST", "/v1/risk/detectors/pii/assessments")]

    @router.post(
        "/v1/risk/detectors/pii/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def risk_pii_assessment(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        result = await service.forward_risk_assessment("/v1/risk/detectors/pii/assessments", payload)
        _record_if_enabled(request, payload=payload, response=result)
        return result

    _s = _GW[("POST", "/v1/risk/detectors/secret/assessments")]

    @router.post(
        "/v1/risk/detectors/secret/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def risk_secret_assessment(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        result = await service.forward_risk_assessment("/v1/risk/detectors/secret/assessments", payload)
        _record_if_enabled(request, payload=payload, response=result)
        return result

    _s = _GW[("POST", "/v1/risk/assessments")]

    @router.post(
        "/v1/risk/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def risk_assessment(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if state_store is not None:
            state = await state_store.get("risk_prompt")
            if state in (RuntimeState.stopped, RuntimeState.starting):
                raise ServiceError(
                    "MODEL_UNAVAILABLE",
                    "risk_prompt runtime is "
                    f"{state.value}. Start it with PATCH /admin/runtimes/risk_prompt.",
                    True,
                    503,
                )
        result = await service.forward_risk_assessment("/v1/risk/assessments", payload)
        _record_if_enabled(request, payload=payload, response=result)
        return result

    return router
