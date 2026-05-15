from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from ..endpoint_spec import RISK_ADAPTER_ENDPOINTS
from ...api_examples import AGGREGATE_EXAMPLES, PROMPT_EXAMPLES
from ...contracts import read_risk_prompt

_RA = {(s.method, s.path): s for s in RISK_ADAPTER_ENDPOINTS}


def build_router(api_dependencies: list, service: Any) -> APIRouter:
    router = APIRouter()

    _s = _RA[("POST", "/v1/risk/detectors/prompt/assessments")]

    @router.post(
        "/v1/risk/detectors/prompt/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "Internal Bearer token 필요"}},
    )
    async def prompt_assessment(
        payload: dict[str, Any] = Body(openapi_examples=PROMPT_EXAMPLES),
    ) -> dict[str, Any]:
        prompt = read_risk_prompt(payload)
        return await service.assess_detector_key("prompt", prompt)

    _s = _RA[("POST", "/v1/risk/assessments")]

    @router.post(
        "/v1/risk/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "Internal Bearer token 필요"}},
    )
    async def aggregate(
        payload: dict[str, Any] = Body(openapi_examples=AGGREGATE_EXAMPLES),
    ) -> dict[str, Any]:
        prompt = read_risk_prompt(payload)
        return await service.assess_aggregate(prompt)

    return router
