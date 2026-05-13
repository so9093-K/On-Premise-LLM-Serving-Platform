from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

from ..errors import ServiceError
from ..metrics import Metrics
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
    main_llm: Any
    embedding: Any
    risk_adapter: Any


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

    def _validate_chat_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return validate_chat_request(
            payload,
            expected_model=self.settings.main_llm.model,
            max_output_tokens=self.settings.main_llm.max_output_tokens,
            allowed_input_modalities=self.settings.main_llm.allowed_input_modalities,
            max_image_inputs=self.settings.main_llm.max_image_inputs,
            allowed_image_url_schemes=self.settings.main_llm.allowed_image_url_schemes,
            max_image_bytes=self.settings.main_llm.max_image_bytes,
            max_image_pixels=self.settings.main_llm.max_image_pixels,
            allowed_image_mime_types=self.settings.main_llm.allowed_image_mime_types,
            request_parameter_policy=self.settings.main_llm.request_parameter_policy,
        )

    def _chat_upstream_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], ChatResponseExpectations]:
        return normalize_chat_request_for_runtime(
            payload,
            self.settings.main_llm.runtime_features,
            self.settings.main_llm.request_parameter_policy,
        )

    async def create_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload, expectations = self._chat_upstream_payload(self._validate_chat_payload(payload))
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


    def stream_chat_completion(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        payload, _expectations = self._chat_upstream_payload(self._validate_chat_payload(payload))
        start = time.monotonic()
        target = self.settings.main_llm.logical_id

        async def relay() -> AsyncIterator[bytes]:
            emitted_chunk = False
            first_chunk_recorded = False
            chunk_count = 0
            terminal_status = "completed"
            observer = StreamingUsageObserver()
            self.metrics.record_streaming_request_started(target)
            try:
                async for chunk in self.clients.main_llm.stream_bytes("chat/completions", payload):
                    emitted_chunk = True
                    chunk_count += 1
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
        payload = validate_embedding_request(
            payload,
            expected_model=self.settings.embedding.model,
            request_parameter_policy=self.settings.embedding.request_parameter_policy,
        )
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self.clients.embedding.post_json("embeddings", payload),
                timeout=self.settings.gateway_timeout_seconds,
            )
            return validate_embedding_response(
                response,
                expected_model=self.settings.embedding.model,
                expected_count=expected_embedding_count(payload),
                expected_dimensions=requested_embedding_dimensions(payload),
            )
        except TimeoutError as exc:
            self.metrics.record_upstream_error(self.settings.embedding.logical_id, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the embedding runtime completed.", True, 504) from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error(self.settings.embedding.logical_id, exc.code)
            raise
        finally:
            self.metrics.record_upstream_request(
                self.settings.embedding.logical_id,
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
