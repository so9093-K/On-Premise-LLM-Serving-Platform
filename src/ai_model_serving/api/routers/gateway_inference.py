from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError, error_payload, error_response_headers
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
    # 비어있거나 형식이 잘못된 리스트는 "no modalities"로 축소되지 않고(그렇게
    # 되면 plain text조차 거부되어 잘못 설정된 프로필에서 전체 chat이 깨진다)
    # 정적 기본값으로 폴백한다.
    if not isinstance(deployed, list) or not deployed or not all(isinstance(item, str) for item in deployed):
        return None
    return tuple(deployed)


async def _resolve_active_main_modalities(sidecar: "SidecarClient | None", settings: Any) -> tuple[str, ...]:
    """Modalities the main model accepts right now, resolved from the same source the
    chat validator uses: the active profile (sidecar snapshot) with the static catalog
    default as fallback. Best-effort -- a sidecar outage falls back rather than failing
    the listing, so /v1/models never depends on the control plane being up."""
    static_default = tuple(settings.main_llm.allowed_input_modalities)
    if sidecar is None:
        return static_default
    try:
        main_model = await sidecar.main_model()
    except SidecarUnavailableError:
        return static_default
    return _active_input_modalities(main_model) or static_default


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
        items = [dict(item) for item in settings.public_models]
        # active main-model 프로필의 입력 modality를 덮어써서, listing이 정적
        # catalog 기본값이 아니라 chat validator와 동일하게 지금 실제로 서빙
        # 가능한 것(예: 멀티모달 프로필의 audio/video)을 알리도록 한다.
        active_modalities = await _resolve_active_main_modalities(sidecar, settings)
        main_model_id = settings.main_llm.model
        for item in items:
            if item.get("id") == main_model_id:
                item["input_modalities"] = list(active_modalities)
        return {"object": "list", "data": items}

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
                "description": "메인 모델 전환 중, control plane 불가, 또는 upstream 동시성/circuit breaker로 인한 일시적 admission 거부(error.code: MAIN_MODEL_SWITCH_IN_PROGRESS, MAIN_MODEL_CONTROL_UNAVAILABLE, QUEUE_TIMEOUT, CIRCUIT_OPEN)",
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
                    payload = error_payload("MAIN_MODEL_CONTROL_UNAVAILABLE", str(exc), True)
                    return JSONResponse(
                        payload,
                        status_code=503,
                        headers=error_response_headers(
                            "MAIN_MODEL_CONTROL_UNAVAILABLE", payload, retry_after_seconds=5
                        ),
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
                        headers=error_response_headers(
                            "MAIN_MODEL_SWITCH_IN_PROGRESS", body, retry_after_seconds=5
                        ),
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
