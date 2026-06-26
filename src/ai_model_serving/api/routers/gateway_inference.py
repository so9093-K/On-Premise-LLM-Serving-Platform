from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError, error_payload
from ...services.runtime_state import RuntimeState, RuntimeStateStore
from ...services.sidecar_client import SidecarClient, SidecarUnavailableError
from ...services.main_model_inflight import MainModelInFlight

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def _active_input_modalities(main_model: dict[str, Any]) -> tuple[str, ...] | None:
    """Input modalities the *currently active* main-model profile actually serves.

    Returned to the validation layer so accepted modalities track the running model
    (e.g. an audio-capable profile) instead of a static registry value. None means
    "use the static default" (no usable profile signal)."""
    profile = main_model.get("active_profile")
    if not isinstance(profile, dict):
        return None
    capabilities = profile.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    deployed = capabilities.get("deployed_input")
    # An empty/malformed list falls back to the static default rather than
    # collapsing to "no modalities" (which would reject even plain text and break
    # all chat on a misconfigured profile).
    if not isinstance(deployed, list) or not deployed or not all(isinstance(item, str) for item in deployed):
        return None
    return tuple(deployed)


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
        active_modalities: tuple[str, ...] | None = None
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
                active_modalities = _active_input_modalities(main_model)
        except Exception:
            if tracking is not None:
                await tracking.__aexit__(None, None, None)
            raise
        if payload.get("stream") is True:
            try:
                upstream_stream = service.stream_chat_completion(payload, active_modalities=active_modalities)
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
            return await service.create_chat_completion(payload, active_modalities=active_modalities)
        try:
            return await service.create_chat_completion(payload, active_modalities=active_modalities)
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
                raise ServiceError(
                    "MODEL_UNAVAILABLE",
                    f"{service_key} runtime is {state.value}. Start it with PATCH /admin/runtimes/{service_key}.",
                    True,
                    503,
                )
        return await service.create_embedding(payload)

    return router
