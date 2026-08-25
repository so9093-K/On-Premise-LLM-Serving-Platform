from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Protocol

from ..errors import ServiceError
from ..metrics import Metrics
from ..runtime_clients.ports import JsonRuntimeClient, StreamingRuntimeClient
from ..settings import AppSettings
from ..contracts import (
    ChatResponseExpectations,
    read_risk_prompt,
    validate_chat_request,
    validate_chat_response,
    expected_embedding_count,
    requested_embedding_dimensions,
    requested_encoding_format,
    validate_embedding_request,
    validate_embedding_response,
    validate_risk_response,
)
from .retrieval_service import RetrievalService


_STRUCTURED_OUTPUT_RETRYABLE_FORMATS = {"json_schema", "json_object"}


def normalize_embedding_request_for_runtime(
    payload: dict[str, Any],
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    """Gateway에서 허용하지만 upstream으로 전달하지 않을 필드를 제거한다."""
    upstream = dict(payload)
    for name in (policy or {}).get("drop_upstream_parameters", []):
        upstream.pop(name, None)
    return upstream


def normalize_chat_request_for_runtime(
    payload: dict[str, Any],
    policy: dict[str, Any] | None,
) -> tuple[dict[str, Any], ChatResponseExpectations]:
    """Gateway 제어 값을 vLLM 요청 확장과 응답 검증 규칙으로 변환한다."""
    response_format = payload.get("response_format")
    response_format_type = response_format.get("type") if isinstance(response_format, dict) else None
    json_schema_wrapper = response_format.get("json_schema") if isinstance(response_format, dict) else None
    json_schema = json_schema_wrapper.get("schema") if isinstance(json_schema_wrapper, dict) else None
    expectations = ChatResponseExpectations(
        response_format_type=response_format_type,
        json_schema=dict(json_schema) if isinstance(json_schema, dict) else None,
        expect_logprobs=payload.get("logprobs") is True,
        stream=payload.get("stream") is True,
    )
    upstream = dict(payload)
    # Gateway 계약에서는 받지만 런타임에 넘길 이유가 없는 필드(예: OpenAI 표준의
    # user 식별자)를 제거한다. embedding 경로와 같은 정책 키를 쓴다.
    for name in (policy or {}).get("drop_upstream_parameters", []):
        upstream.pop(name, None)
    reasoning_enabled = upstream.pop("reasoning", None)
    # runtime의 reasoning parser는 chat_template_kwargs만 보고 thinking 여부를 판단한다.
    # 끈 요청에서 이 값을 생략하면 parser는 기본값(thinking on)으로 읽어 "아직 사고 중"이라
    # 판단하고, 그동안 structured output grammar를 적용하지 않아 json_schema 응답이
    # 자유 텍스트로 나온다. 그래서 켤 때와 끌 때를 모두 명시한다.
    declared_kwargs = ((policy or {}).get("reasoning") or {}).get("upstream_chat_template_kwargs") or {}
    if declared_kwargs:
        template_kwargs = dict(upstream.get("chat_template_kwargs", {}))
        for name, enabled_value in declared_kwargs.items():
            if reasoning_enabled is True:
                template_kwargs[name] = enabled_value
            elif isinstance(enabled_value, bool):
                template_kwargs[name] = not enabled_value
        upstream["chat_template_kwargs"] = template_kwargs
    return upstream, expectations



def _stream_error_event(exc: ServiceError) -> bytes:
    """streaming 전송 실패에 대해 크기를 제한한 SSE 오류 event를 반환한다.

    Once the Gateway has selected the SSE transport, some failures may happen
    after response headers are committed.  In that phase the service cannot
    safely switch back to the normal JSON error envelope, so it emits an SSE
    `error` event followed by `[DONE]`.  The event intentionally contains only
    the structured error payload and never includes prompt or generated text.
    """
    return (
        "event: error\n"
        f"data: {json.dumps(exc.to_payload(), ensure_ascii=False, separators=(',', ':'))}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")


class StreamingUsageObserver:
    """본문을 버퍼링하지 않고 전달 중인 SSE 바이트에서 usage 객체를 확인한다.

    vLLM/OpenAI-compatible streaming may include `usage` on a final or near-final
    chunk.  The Gateway relays the original bytes unchanged and only counts that
    such an accounting event was present; prompt text, token deltas, and the
    numeric usage values are intentionally not exported as metric labels.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def observe(self, chunk: bytes) -> int:
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            text = chunk.decode("utf-8", errors="ignore")
        self._buffer += text
        usage_events = 0
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("usage"), dict):
                usage_events += 1
        return usage_events

class GatewayClientSet(Protocol):
    main_llm: StreamingRuntimeClient
    embedding_clients: dict[str, JsonRuntimeClient]
    risk_adapter: JsonRuntimeClient


class GatewayService:
    """공개 Gateway 작업을 담당하는 use-case 계층이다.

    FastAPI handlers should remain responsible for transport concerns only:
    routing, auth dependencies, examples, and response metadata.  This service
    owns request validation, upstream orchestration, timeout mapping, response
    validation, and metrics for the Gateway's primary use cases.
    """

    def __init__(self, settings: AppSettings, clients: GatewayClientSet, metrics: Metrics) -> None:
        self.settings = settings
        self.clients = clients
        self.metrics = metrics
        self.retrieval = RetrievalService(settings, clients, metrics)

    def _main_llm_endpoint(
        self,
        gateway_policy: dict[str, Any] | None,
        active_modalities: tuple[str, ...] | None,
    ):
        """공통 연결 설정에 활성 Profile의 API 정책만 합성한다."""
        policy = gateway_policy or self.settings.default_main_model_gateway_policy
        if not policy:
            return replace(
                self.settings.runtime("main_llm"),
                allowed_input_modalities=active_modalities or self.settings.runtime("main_llm").allowed_input_modalities,
            )
        limits = policy.get("request_limits", {}) if isinstance(policy, dict) else {}
        modalities = active_modalities or tuple(str(item) for item in limits.get("input_modalities", ()))
        if not modalities:
            modalities = self.settings.runtime("main_llm").allowed_input_modalities
        return replace(
            self.settings.runtime("main_llm"),
            max_output_tokens=int(policy.get("max_output_tokens", self.settings.runtime("main_llm").max_output_tokens or 0)),
            max_model_len=(int(limits["max_model_len"]) if "max_model_len" in limits else self.settings.runtime("main_llm").max_model_len),
            allowed_input_modalities=modalities,
            max_image_inputs=int(limits.get("max_image_inputs", 0)),
            allowed_image_url_schemes=tuple(str(item) for item in limits.get("allowed_image_url_schemes", ())),
            max_image_bytes=int(limits.get("max_image_bytes", 0)),
            max_image_pixels=int(limits.get("max_image_pixels", 0)),
            allowed_image_mime_types=tuple(str(item) for item in limits.get("allowed_image_mime_types", ())),
            max_audio_inputs=int(limits.get("max_audio_inputs", 0)),
            allowed_audio_formats=tuple(str(item) for item in limits.get("allowed_audio_formats", ())),
            max_audio_bytes=int(limits.get("max_audio_bytes", 0)),
            max_video_inputs=int(limits.get("max_video_inputs", 0)),
            allowed_video_url_schemes=tuple(str(item) for item in limits.get("allowed_video_url_schemes", ())),
            allowed_video_mime_types=tuple(str(item) for item in limits.get("allowed_video_mime_types", ())),
            max_video_bytes=int(limits.get("max_video_bytes", 0)),
            max_video_frames=int(limits.get("max_video_frames", 0)),
            max_video_frame_pixels=int(limits.get("max_video_frame_pixels", 0)),
            max_video_duration_seconds=float(limits.get("max_video_duration_seconds", 0)),
            request_parameter_policy=dict(policy.get("request_parameter_policy", {})),
        )

    def _validate_chat_payload(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
        gateway_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        endpoint = self._main_llm_endpoint(gateway_policy, active_modalities)
        return validate_chat_request(
            payload,
            expected_model=endpoint.model,
            max_output_tokens=endpoint.max_output_tokens,
            allowed_input_modalities=endpoint.allowed_input_modalities,
            max_image_inputs=endpoint.max_image_inputs,
            allowed_image_url_schemes=endpoint.allowed_image_url_schemes,
            max_image_bytes=endpoint.max_image_bytes,
            max_image_pixels=endpoint.max_image_pixels,
            allowed_image_mime_types=endpoint.allowed_image_mime_types,
            max_audio_inputs=endpoint.max_audio_inputs,
            allowed_audio_formats=endpoint.allowed_audio_formats,
            max_audio_bytes=endpoint.max_audio_bytes,
            max_video_inputs=endpoint.max_video_inputs,
            allowed_video_url_schemes=endpoint.allowed_video_url_schemes,
            allowed_video_mime_types=endpoint.allowed_video_mime_types,
            max_video_bytes=endpoint.max_video_bytes,
            max_video_frames=endpoint.max_video_frames,
            max_video_frame_pixels=endpoint.max_video_frame_pixels,
            max_video_duration_seconds=endpoint.max_video_duration_seconds,
            request_parameter_policy=endpoint.request_parameter_policy,
        )

    def _chat_upstream_payload(
        self,
        payload: dict[str, Any],
        gateway_policy: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], ChatResponseExpectations]:
        endpoint = self._main_llm_endpoint(gateway_policy, None)
        return normalize_chat_request_for_runtime(
            payload,
            endpoint.request_parameter_policy,
        )

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
        gateway_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload, expectations = self._chat_upstream_payload(
            self._validate_chat_payload(
                payload, active_modalities=active_modalities, gateway_policy=gateway_policy
            ),
            gateway_policy,
        )
        # 구조화 출력(json_schema/json_object) 응답이 콜드스타트 Triton JIT 지연 등으로
        # 중간에 잘리면 validate_chat_response가 UPSTREAM_SCHEMA_ERROR로 잡아낸다.
        # 이 스키마는 요청마다 임의로 달라질 수 있어 미리 예열해둘 수 없으므로,
        # 스키마 내용과 무관하게 통하는 방어선은 "잘림 감지 후 즉시 1회 재시도"뿐이다.
        attempts_allowed = 2 if expectations.response_format_type in _STRUCTURED_OUTPUT_RETRYABLE_FORMATS else 1
        start = time.monotonic()
        try:
            for attempt in range(1, attempts_allowed + 1):
                try:
                    response = await asyncio.wait_for(
                        self.clients.main_llm.post_json("chat/completions", payload),
                        timeout=self.settings.gateway_timeout_seconds,
                    )
                    return validate_chat_response(
                        response, expected_model=self.settings.runtime("main_llm").model, expectations=expectations
                    )
                except ServiceError as exc:
                    if exc.code == "UPSTREAM_SCHEMA_ERROR" and attempt < attempts_allowed:
                        self.metrics.record_upstream_error(self.settings.runtime("main_llm").logical_id, "UPSTREAM_SCHEMA_ERROR_RETRIED")
                        continue
                    self.metrics.record_upstream_error(self.settings.runtime("main_llm").logical_id, exc.code)
                    raise
        except TimeoutError as exc:
            self.metrics.record_upstream_error(self.settings.runtime("main_llm").logical_id, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the chat runtime completed.") from exc
        finally:
            self.metrics.record_upstream_request(
                self.settings.runtime("main_llm").logical_id,
                "chat/completions",
                time.monotonic() - start,
            )


    def stream_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
        gateway_policy: dict[str, Any] | None = None,
    ) -> AsyncIterator[bytes]:
        payload, _expectations = self._chat_upstream_payload(
            self._validate_chat_payload(
                payload, active_modalities=active_modalities, gateway_policy=gateway_policy
            ),
            gateway_policy,
        )
        start = time.monotonic()
        target = self.settings.runtime("main_llm").logical_id

        async def relay() -> AsyncIterator[bytes]:
            emitted_chunk = False
            first_chunk_recorded = False
            chunk_count = 0
            byte_count = 0
            terminal_status = "completed"
            observer = StreamingUsageObserver()
            self.metrics.record_streaming_request_started(target)
            try:
                async with asyncio.timeout(self.settings.streaming_max_duration_seconds):
                    async for chunk in self.clients.main_llm.stream_bytes("chat/completions", payload):
                        if not chunk:
                            continue
                        emitted_chunk = True
                        chunk_count += 1
                        byte_count += len(chunk)
                        if chunk_count > self.settings.streaming_max_chunks:
                            raise ServiceError(
                                "STREAM_LIMIT_EXCEEDED", f"stream emitted {chunk_count} chunks; limit is {self.settings.streaming_max_chunks}. Reduce max_tokens or retry without stream=true.",
                            )
                        if byte_count > self.settings.streaming_max_bytes:
                            raise ServiceError(
                                "STREAM_LIMIT_EXCEEDED", f"stream emitted {byte_count} bytes; limit is {self.settings.streaming_max_bytes}. Reduce max_tokens or retry without stream=true.",
                            )
                        if not first_chunk_recorded:
                            first_chunk_recorded = True
                            self.metrics.record_streaming_first_chunk(target, time.monotonic() - start)
                        self.metrics.record_streaming_chunk(target, len(chunk))
                        for _ in range(observer.observe(chunk)):
                            self.metrics.record_streaming_usage_event(target)
                        yield chunk
            except asyncio.CancelledError:
                terminal_status = "client_disconnect"
                phase = "mid_stream" if emitted_chunk else "before_first_chunk"
                self.metrics.record_streaming_client_disconnect(target, phase)
                self.metrics.record_streaming_error(target, "CLIENT_DISCONNECT", phase)
                raise
            except TimeoutError as exc:
                terminal_status = "gateway_timeout"
                phase = "mid_stream" if emitted_chunk else "before_first_chunk"
                self.metrics.record_upstream_error(target, "GATEWAY_TIMEOUT")
                self.metrics.record_streaming_error(target, "GATEWAY_TIMEOUT", phase)
                error = ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the chat stream completed.")
                yield _stream_error_event(error)
            except ServiceError as exc:
                terminal_status = exc.code
                phase = "mid_stream" if emitted_chunk else "before_first_chunk"
                self.metrics.record_upstream_error(target, exc.code)
                self.metrics.record_streaming_error(target, exc.code, phase)
                yield _stream_error_event(exc)
            finally:
                elapsed = time.monotonic() - start
                self.metrics.record_streaming_completed(target, terminal_status, elapsed, chunk_count)
                self.metrics.record_upstream_request(
                    target,
                    "chat/completions:stream",
                    elapsed,
                )

        return relay()

    async def create_embedding(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = str(payload.get("model", self.settings.default_embedding_model))
        profile = self.settings.embedding_profiles.get(model)
        if profile is None:
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"Unsupported embedding model: {model}")
        client = self.clients.embedding_clients.get(model)
        if client is None:
            raise ServiceError("MODEL_UNAVAILABLE", f"{model} embedding runtime is unavailable.")
        payload = validate_embedding_request(
            payload,
            expected_model=profile.model,
            request_parameter_policy=profile.request_parameter_policy,
        )
        upstream_payload = normalize_embedding_request_for_runtime(
            payload,
            profile.request_parameter_policy,
        )
        expected_dimensions = requested_embedding_dimensions(payload) or profile.default_dimensions
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.post_json("embeddings", upstream_payload),
                timeout=self.settings.gateway_timeout_seconds,
            )
            return validate_embedding_response(
                response,
                expected_model=profile.model,
                expected_count=expected_embedding_count(payload),
                expected_dimensions=expected_dimensions,
                encoding_format=requested_encoding_format(payload),
            )
        except TimeoutError as exc:
            self.metrics.record_upstream_error(profile.model, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the embedding runtime completed.") from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error(profile.model, exc.code)
            raise
        finally:
            self.metrics.record_upstream_request(
                profile.model,
                "embeddings",
                time.monotonic() - start,
            )

    async def forward_risk_assessment(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        read_risk_prompt(payload)
        start = time.monotonic()
        headers = {"authorization": f"Bearer {self.settings.security.internal_service_token}"}
        try:
            response = await asyncio.wait_for(
                self.clients.risk_adapter.post_json(path, payload, headers=headers),
                timeout=self.settings.gateway_timeout_seconds,
            )
            return validate_risk_response(response)
        except TimeoutError as exc:
            self.metrics.record_upstream_error("risk-adapter", "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the risk adapter completed.") from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error("risk-adapter", exc.code)
            raise
        finally:
            self.metrics.record_upstream_request("risk-adapter", path, time.monotonic() - start)

    async def rerank_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.retrieval.rerank_documents(payload)

    async def score_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.retrieval.score_documents(payload)
