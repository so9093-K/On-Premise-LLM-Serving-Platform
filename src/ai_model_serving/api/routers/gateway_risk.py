from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError
from ...services.runtime_state import RuntimeState, RuntimeStateStore

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def build_router(
    api_dependencies: list,
    service: Any,
    state_store: RuntimeStateStore | None = None,
) -> APIRouter:
    router = APIRouter()

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
        return await service.forward_risk_assessment("/v1/risk/detectors/prompt/assessments", payload)

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
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        return await service.forward_risk_assessment("/v1/risk/detectors/pii/assessments", payload)

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
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        return await service.forward_risk_assessment("/v1/risk/detectors/secret/assessments", payload)

    _s = _GW[("POST", "/v1/risk/detectors/siren/assessments")]

    @router.post(
        "/v1/risk/detectors/siren/assessments",
        status_code=_s.status_code,
        response_model=None,
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}, 410: {"description": "Detector 사용 중단"}},
    )
    async def risk_siren_assessment() -> None:
        raise ServiceError(
            "DETECTOR_RETIRED",
            "risk-siren detector는 retired 상태입니다. "
            "/v1/risk/detectors/prompt/assessments 또는 /v1/risk/assessments를 사용하세요.",
            False,
            410,
        )

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
        return await service.forward_risk_assessment("/v1/risk/assessments", payload)

    return router
