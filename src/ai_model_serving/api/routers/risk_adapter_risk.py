from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Request

from ..endpoint_spec import RISK_ADAPTER_ENDPOINTS
from ...contracts import read_risk_prompt
from ...logging_policy import record_request_response_preview, record_token_usage
from ...settings import AppSettings

_RA = {(s.method, s.path): s for s in RISK_ADAPTER_ENDPOINTS}


def _risk_response_preview(response: dict[str, Any]) -> str:
    return json.dumps(response, ensure_ascii=False, sort_keys=True)


def build_router(api_dependencies: list, service: Any, settings: AppSettings) -> APIRouter:
    router = APIRouter()

    def _record_if_enabled(request: Request, *, prompt: str, response: dict[str, Any]) -> None:
        record_token_usage(request, response.get("usage"))
        if settings.log_request_response_body:
            record_request_response_preview(
                request,
                request_text=prompt,
                response_text=_risk_response_preview(response),
            )

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
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        prompt = read_risk_prompt(payload)
        result = await service.assess_detector_key("prompt", prompt)
        _record_if_enabled(request, prompt=prompt, response=result)
        return result

    _s = _RA[("POST", "/v1/risk/detectors/pii/assessments")]

    @router.post(
        "/v1/risk/detectors/pii/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "Internal Bearer token 필요"}},
    )
    async def pii_assessment(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        prompt = read_risk_prompt(payload)
        result = await service.assess_detector_key("pii", prompt)
        _record_if_enabled(request, prompt=prompt, response=result)
        return result

    _s = _RA[("POST", "/v1/risk/detectors/secret/assessments")]

    @router.post(
        "/v1/risk/detectors/secret/assessments",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "Internal Bearer token 필요"}},
    )
    async def secret_assessment(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        prompt = read_risk_prompt(payload)
        result = await service.assess_detector_key("secret", prompt)
        _record_if_enabled(request, prompt=prompt, response=result)
        return result

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
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        prompt = read_risk_prompt(payload)
        result = await service.assess_aggregate(prompt)
        _record_if_enabled(request, prompt=prompt, response=result)
        return result

    return router
