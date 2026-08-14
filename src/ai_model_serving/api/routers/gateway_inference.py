from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..endpoint_spec import GATEWAY_ENDPOINTS
from ...errors import ServiceError, error_payload, error_response_headers
from ...domain.request_surfaces import chat_request_parameter_surface
from ...logging_policy import record_request_response_preview, record_token_usage
from ...services.runtime_state import RuntimeState, RuntimeStateStore
from ...services.sidecar_client import SidecarClient, SidecarUnavailableError
from ...services.main_model_inflight import MainModelInFlight

_GW = {(s.method, s.path): s for s in GATEWAY_ENDPOINTS}


def _chat_text_preview(payload: dict[str, Any]) -> str:
    """messages[].content에서 텍스트만 뽑아 사람이 읽을 수 있는 프리뷰로 합친다.

    image_url/input_audio/video_url 같은 non-text content part는 제외한다(용량이
    크고 마스킹 대상도 아님).
    """
    lines: list[str] = []
    for message in payload.get("messages", []):
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = ""
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _chat_response_preview(response: dict[str, Any]) -> str:
    parts = []
    for choice in response.get("choices", []):
        if not isinstance(choice, dict):
            continue
        content = choice.get("message", {}).get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _embedding_input_preview(payload: dict[str, Any]) -> str:
    """embeddings 요청의 input을 사람이 읽을 프리뷰로 합친다(문자열 또는 문자열 리스트)."""
    raw = payload.get("input")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return " | ".join(str(item) for item in raw)
    return ""


def _embedding_response_summary(response: dict[str, Any]) -> str:
    """임베딩 응답은 float 벡터라 원문을 그대로 남기면 못 읽고 크기만 커진다 --
    개수/차원/모델 요약만 남긴다."""
    data = response.get("data")
    count = len(data) if isinstance(data, list) else 0
    dim = 0
    if isinstance(data, list) and data and isinstance(data[0], dict):
        embedding = data[0].get("embedding")
        if isinstance(embedding, list):
            dim = len(embedding)
    model = response.get("model", "")
    return f"{count} embeddings returned, dim={dim}, model={model}"


def _active_input_modalities(main_model: dict[str, Any]) -> tuple[str, ...] | None:
    """현재 활성 main-model 프로필이 실제로 제공하는 입력 modality를 반환한다.

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


def _active_gateway_policy(main_model: dict[str, Any]) -> dict[str, Any] | None:
    """활성 Profile이 함께 공개한 Gateway 정책을 반환한다."""
    profile = main_model.get("active_profile")
    if not isinstance(profile, dict):
        return None
    policy = profile.get("gateway_policy")
    return dict(policy) if isinstance(policy, dict) else None


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
        active_policy = settings.default_main_model_gateway_policy
        active_modalities = tuple(
            str(item)
            for item in active_policy.get("request_limits", {}).get(
                "input_modalities", settings.runtime("main_llm").allowed_input_modalities
            )
        )
        if sidecar is not None:
            try:
                active_snapshot = await sidecar.main_model()
            except SidecarUnavailableError:
                active_snapshot = None
            if isinstance(active_snapshot, dict):
                active_modalities = _active_input_modalities(active_snapshot) or active_modalities
                active_policy = _active_gateway_policy(active_snapshot) or active_policy
        main_model_id = settings.runtime("main_llm").model
        for item in items:
            if item.get("id") == main_model_id:
                item["input_modalities"] = list(active_modalities)
                if active_policy:
                    item["request_parameters"] = chat_request_parameter_surface(
                        active_policy.get("request_parameter_policy", {}),
                        max_output_tokens=int(active_policy.get("max_output_tokens", 0)) or None,
                    )
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
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> Any:
        tracking = main_model_inflight.track() if main_model_inflight is not None else None
        if tracking is not None:
            await tracking.__aenter__()
        active_modalities: tuple[str, ...] | None = None
        gateway_policy: dict[str, Any] | None = None
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
                gateway_policy = _active_gateway_policy(main_model)
        except Exception:
            if tracking is not None:
                await tracking.__aexit__(None, None, None)
            raise
        if payload.get("stream") is True:
            try:
                upstream_stream = service.stream_chat_completion(
                    payload, active_modalities=active_modalities, gateway_policy=gateway_policy
                )
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
            result = await service.create_chat_completion(
                payload, active_modalities=active_modalities, gateway_policy=gateway_policy
            )
        else:
            try:
                result = await service.create_chat_completion(
                    payload, active_modalities=active_modalities, gateway_policy=gateway_policy
                )
            finally:
                await tracking.__aexit__(None, None, None)
        record_token_usage(request, result.get("usage"))
        if settings.log_request_response_body:
            record_request_response_preview(
                request,
                request_text=_chat_text_preview(payload),
                response_text=_chat_response_preview(result),
            )
        return result

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
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        if state_store is not None:
            model = str(payload.get("model", settings.default_embedding_model))
            profile = settings.embedding_profiles.get(model)
            service_key = profile.service_key if profile is not None else "embedding"
            state = await state_store.get(service_key)
            if state in (RuntimeState.stopped, RuntimeState.starting):
                raise ServiceError(
                    "MODEL_UNAVAILABLE",
                    f"{service_key} runtime is {state.value}. Start it with PATCH /admin/runtimes/{service_key}.",
                    True,
                    503,
                )
        result = await service.create_embedding(payload)
        record_token_usage(request, result.get("usage"))
        if settings.log_request_response_body:
            record_request_response_preview(
                request,
                request_text=_embedding_input_preview(payload),
                response_text=_embedding_response_summary(result),
            )
        return result

    return router
