from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import error_payload
from ...services.runtime_state import RuntimeState, RuntimeStateStore
from ...services.sidecar_client import SidecarClient, SidecarUnavailableError
from ...services.main_model_inflight import MainModelInFlight

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def build_router(
    api_dependencies: list,
    service: Any,
    settings: Any,
    state_store: RuntimeStateStore | None = None,
    sidecar: SidecarClient | None = None,
    main_model_inflight: MainModelInFlight | None = None,
) -> APIRouter:
    router = APIRouter()

    _s = _GW[("GET", "/v1/models")]

    @router.get(
        "/v1/models",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
    )
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [dict(item) for item in settings.public_models]}

    _s = _GW[("POST", "/v1/chat/completions")]

    @router.post(
        "/v1/chat/completions",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={
            401: {"description": "API Bearer token 필요"},
            503: {
                "description": "메인 모델 전환 중 또는 control plane 불가",
                "headers": {
                    "Retry-After": {
                        "description": "재시도 전 대기 시간(초)",
                        "schema": {"type": "integer", "example": 5},
                    }
                },
            },
        },
    )
    async def chat_completions(
        payload: dict[str, Any] = Body(...),
    ) -> Any:
        tracking = main_model_inflight.track() if main_model_inflight is not None else None
        if tracking is not None:
            await tracking.__aenter__()
        try:
            if sidecar is not None:
                try:
                    main_model = await sidecar.main_model()
                except SidecarUnavailableError as exc:
                    if tracking is not None:
                        await tracking.__aexit__(None, None, None)
                    return JSONResponse(
                        error_payload("MAIN_MODEL_CONTROL_UNAVAILABLE", str(exc), True),
                        status_code=503,
                        headers={"Retry-After": "5"},
                    )
                if main_model.get("gate") != "open":
                    operation = main_model.get("last_operation") or {}
                    body = error_payload(
                        "MAIN_MODEL_SWITCH_IN_PROGRESS",
                        "Main model requests are temporarily unavailable.",
                        True,
                    )
                    body["error"]["operation_id"] = operation.get("id")
                    body["error"]["operation_status"] = operation.get("status")
                    if tracking is not None:
                        await tracking.__aexit__(None, None, None)
                    return JSONResponse(
                        body,
                        status_code=503,
                        headers={"Retry-After": "5"},
                    )
        except Exception:
            if tracking is not None:
                await tracking.__aexit__(None, None, None)
            raise
        if payload.get("stream") is True:
            try:
                upstream_stream = service.stream_chat_completion(payload)
            except Exception:
                if tracking is not None:
                    await tracking.__aexit__(None, None, None)
                raise

            async def tracked_stream() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream_stream:
                        yield chunk
                finally:
                    if tracking is not None:
                        await tracking.__aexit__(None, None, None)
            return StreamingResponse(
                tracked_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        if tracking is None:
            return await service.create_chat_completion(payload)
        try:
            return await service.create_chat_completion(payload)
        finally:
            await tracking.__aexit__(None, None, None)

    _s = _GW[("POST", "/v1/embeddings")]

    @router.post(
        "/v1/embeddings",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def embeddings(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if state_store is not None:
            model = str(payload.get("model", settings.default_embedding_model))
            service_key = settings.embedding_model_routes.get(model, "embedding")
            state = await state_store.get(service_key)
            if state in (RuntimeState.stopped, RuntimeState.starting):
                raise HTTPException(
                    503,
                    detail={
                        "error": "runtime_unavailable",
                        "service_key": service_key,
                        "state": state.value,
                    },
                )
        return await service.create_embedding(payload)

    return router
