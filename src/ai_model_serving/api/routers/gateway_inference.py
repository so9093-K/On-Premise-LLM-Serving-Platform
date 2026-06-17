from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...services.runtime_state import RuntimeState, RuntimeStateStore

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def build_router(
    api_dependencies: list,
    service: Any,
    settings: Any,
    state_store: RuntimeStateStore | None = None,
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
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def chat_completions(
        payload: dict[str, Any] = Body(...),
    ) -> Any:
        if payload.get("stream") is True:
            return StreamingResponse(
                service.stream_chat_completion(payload),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        return await service.create_chat_completion(payload)

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
