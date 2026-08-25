from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError
from ...services.runtime_state import RuntimeState, RuntimeStateStore

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


async def _ensure_retrieval_runtime_available(
    payload: dict[str, Any],
    settings: Any,
    state_store: RuntimeStateStore | None,
) -> None:
    if state_store is None:
        return
    model = str(payload.get("model") or settings.default_retrieval_model)
    profile = settings.embedding_profiles.get(model)
    if profile is None:
        return
    service_key = profile.service_key
    state = await state_store.get(service_key)
    if state in (RuntimeState.stopped, RuntimeState.starting):
        raise ServiceError(
            "MODEL_UNAVAILABLE", f"{service_key} runtime is {state.value}. Start it with PATCH /admin/runtimes/{service_key}.",
        )


def build_router(
    api_dependencies: list,
    admin_dependencies: list,
    service: Any,
    settings: Any,
    state_store: RuntimeStateStore | None = None,
) -> APIRouter:
    router = APIRouter()

    _s = _GW[("POST", "/v1/retrieval/rerank")]

    @router.post(
        "/v1/retrieval/rerank",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def retrieval_rerank(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        await _ensure_retrieval_runtime_available(payload, settings, state_store)
        return await service.rerank_documents(payload)

    _s = _GW[("POST", "/v1/retrieval/score")]

    @router.post(
        "/v1/retrieval/score",
        dependencies=api_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "API Bearer token 필요"}},
    )
    async def retrieval_score(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        await _ensure_retrieval_runtime_available(payload, settings, state_store)
        return await service.score_documents(payload)

    return router
