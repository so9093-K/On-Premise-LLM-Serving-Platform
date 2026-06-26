from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
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
    validate_embedding_request,
    validate_embedding_response,
    validate_risk_response,
)
from .retrieval_service import RetrievalService


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
    runtime_features: dict[str, Any] | None,
    policy: dict[str, Any] | None,
) -> tuple[dict[str, Any], ChatResponseExpectations]:
    """Map Gateway-facing controls to vLLM request extensions and response checks."""
    del runtime_features, policy
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
    reasoning_enabled = upstream.pop("reasoning", None)
    if reasoning_enabled is True:
        template_kwargs = dict(upstream.get("chat_template_kwargs", {}))
        template_kwargs["enable_thinking"] = True
        upstream["chat_template_kwargs"] = template_kwargs
    return upstream, expectations



def _stream_error_event(exc: ServiceError) -> bytes:
    """Return a bounded SSE error event for streaming transport failures.

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
    """Inspect relayed SSE bytes for usage objects without buffering content.

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
    """Use-case layer for public Gateway operations.

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

    def _validate_chat_payload(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        # The set of accepted input modalities tracks the ACTIVE main-model profile
        # (deployed_input from the sidecar snapshot) when available, falling back to
        # the static registry value. This keeps the current model's behavior
        # unchanged (its profile resolves to the same modalities) while letting a
        # switched-in profile (e.g. audio-capable) widen what is accepted -- without
        # ever advertising a modality the running model cannot serve. The per-modality
        # safety limits remain static policy from the registry.
        allowed_input_modalities = (
            active_modalities
            if active_modalities is not None
            else self.settings.main_llm.allowed_input_modalities
        )
        return validate_chat_request(
            payload,
            expected_model=self.settings.main_llm.model,
            max_output_tokens=self.settings.main_llm.max_output_tokens,
            allowed_input_modalities=allowed_input_modalities,
            max_image_inputs=self.settings.main_llm.max_image_inputs,
            allowed_image_url_schemes=self.settings.main_llm.allowed_image_url_schemes,
            max_image_bytes=self.settings.main_llm.max_image_bytes,
            max_image_pixels=self.settings.main_llm.max_image_pixels,
            allowed_image_mime_types=self.settings.main_llm.allowed_image_mime_types,
            max_audio_inputs=self.settings.main_llm.max_audio_inputs,
            allowed_audio_formats=self.settings.main_llm.allowed_audio_formats,
            max_audio_bytes=self.settings.main_llm.max_audio_bytes,
            max_video_inputs=self.settings.main_llm.max_video_inputs,
            allowed_video_url_schemes=self.settings.main_llm.allowed_video_url_schemes,
            allowed_video_mime_types=self.settings.main_llm.allowed_video_mime_types,
            max_video_bytes=self.settings.main_llm.max_video_bytes,
            max_video_frames=self.settings.main_llm.max_video_frames,
            max_video_frame_pixels=self.settings.main_llm.max_video_frame_pixels,
            request_parameter_policy=self.settings.main_llm.request_parameter_policy,
        )

    def _chat_upstream_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], ChatResponseExpectations]:
        return normalize_chat_request_for_runtime(
            payload,
            self.settings.main_llm.runtime_features,
            self.settings.main_llm.request_parameter_policy,
        )

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        payload, expectations = self._chat_upstream_payload(
            self._validate_chat_payload(payload, active_modalities=active_modalities)
        )
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self.clients.main_llm.post_json("chat/completions", payload),
                timeout=self.settings.gateway_timeout_seconds,
            )
            return validate_chat_response(response, expected_model=self.settings.main_llm.model, expectations=expectations)
        except TimeoutError as exc:
            self.metrics.record_upstream_error(self.settings.main_llm.logical_id, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the chat runtime completed.", True, 504) from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error(self.settings.main_llm.logical_id, exc.code)
            raise
        finally:
            self.metrics.record_upstream_request(
                self.settings.main_llm.logical_id,
                "chat/completions",
                time.monotonic() - start,
            )


    def stream_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
    ) -> AsyncIterator[bytes]:
        payload, _expectations = self._chat_upstream_payload(
            self._validate_chat_payload(payload, active_modalities=active_modalities)
        )
        start = time.monotonic()
        target = self.settings.main_llm.logical_id

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
                            raise ServiceError("STREAM_LIMIT_EXCEEDED", "stream chunk limit exceeded.", True, 504)
                        if byte_count > self.settings.streaming_max_bytes:
                            raise ServiceError("STREAM_LIMIT_EXCEEDED", "stream byte limit exceeded.", True, 504)
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
                error = ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the chat stream completed.", True, 504)
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
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"Unsupported embedding model: {model}", False, 422)
        client = self.clients.embedding_clients.get(model)
        if client is None:
            raise ServiceError("MODEL_UNAVAILABLE", f"{model} embedding runtime is unavailable.", True, 503)
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
            )
        except TimeoutError as exc:
            self.metrics.record_upstream_error(profile.model, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the embedding runtime completed.", True, 504) from exc
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
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the risk adapter completed.", True, 504) from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error("risk-adapter", exc.code)
            raise
        finally:
            self.metrics.record_upstream_request("risk-adapter", path, time.monotonic() - start)

    async def rerank_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.retrieval.rerank_documents(payload)

    async def score_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.retrieval.score_documents(payload)
