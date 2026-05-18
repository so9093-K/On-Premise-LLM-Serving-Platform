from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from ..endpoint_spec import GATEWAY_ENDPOINTS

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def build_router(api_dependencies: list, admin_dependencies: list, service: Any, settings: Any) -> APIRouter:
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
        return await service.score_documents(payload)

    _s = _GW[("POST", "/v1/retrieval/token-embeddings")]

    @router.post(
        "/v1/retrieval/token-embeddings",
        dependencies=admin_dependencies,
        tags=[_s.tag],
        summary=_s.summary,
        operation_id=_s.operation_id,
        description=_s.description,
        responses={401: {"description": "Admin Bearer token 필요"}},
    )
    async def retrieval_token_embeddings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return await service.get_token_embeddings(payload)

    return router
