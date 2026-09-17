from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from ..contracts.responses import (
    normalize_responses_request_for_runtime,
    project_responses_response,
    project_responses_stream_event,
    validate_responses_request,
)
from ..errors import ServiceError
from ..logging_policy import record_stream_completion
from ..metrics import sanitized_stream_status
from ..runtime_configuration import RuntimeConfigurationProvider


def _stream_error_event(exc: ServiceError) -> bytes:
    return (
        "event: error\n"
        f"data: {json.dumps(exc.to_payload(), ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


class ResponsesService:
    """Responses API use-case layer over the same main-model policy and runtime boundary as Chat."""

    def __init__(self, gateway_service: Any) -> None:
        self.gateway = gateway_service
        self.settings = gateway_service.settings
        self.clients = gateway_service.clients
        self.metrics = gateway_service.metrics
        self.runtime_configuration = RuntimeConfigurationProvider.from_settings(self.settings)

    def _validated_payload(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None,
        gateway_policy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        endpoint = self.gateway.main_llm_endpoint(gateway_policy, active_modalities)
        validated = validate_responses_request(
            payload,
            expected_model=endpoint.model,
            max_output_tokens=endpoint.max_output_tokens,
            allowed_input_modalities=endpoint.allowed_input_modalities,
            max_image_inputs=endpoint.max_image_inputs,
            allowed_image_url_schemes=endpoint.allowed_image_url_schemes,
            max_image_bytes=endpoint.max_image_bytes,
            max_image_pixels=endpoint.max_image_pixels,
            allowed_image_mime_types=endpoint.allowed_image_mime_types,
            request_parameter_policy=endpoint.request_parameter_policy,
        )
        return normalize_responses_request_for_runtime(validated, endpoint.request_parameter_policy)

    async def create_response(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
        gateway_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        upstream = self._validated_payload(
            payload, active_modalities=active_modalities, gateway_policy=gateway_policy
        )
        target = self.settings.runtime("main_llm").logical_id
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self.clients.main_llm.post_json("responses", upstream),
                timeout=self.settings.gateway_timeout_seconds,
            )
            return project_responses_response(
                response, expected_model=self.settings.runtime("main_llm").model
            )
        except TimeoutError as exc:
            self.metrics.record_upstream_error(target, "GATEWAY_TIMEOUT")
            raise ServiceError(
                "UPSTREAM_TIMEOUT",
                "Gateway request timed out before the Responses runtime completed.",
                diagnostic_code="GATEWAY_TIMEOUT",
            ) from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error(target, exc.operational_code)
            raise
        finally:
            self.metrics.record_upstream_request(target, "responses", time.monotonic() - start)

    async def stream_response(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
        gateway_policy: dict[str, Any] | None = None,
    ) -> AsyncIterator[bytes]:
        upstream_payload = self._validated_payload(
            payload, active_modalities=active_modalities, gateway_policy=gateway_policy
        )
        start = time.monotonic()
        target = self.settings.runtime("main_llm").logical_id
        try:
            upstream = await self.clients.main_llm.open_stream("responses", upstream_payload)
        except ServiceError as exc:
            self.metrics.record_upstream_error(target, exc.operational_code)
            self.metrics.record_streaming_error(target, exc.operational_code, "admission")
            raise
        return self._relay(upstream, target=target, start=start)

    async def _relay(self, upstream: Any, *, target: str, start: float) -> AsyncIterator[bytes]:
        runtime_config = self.runtime_configuration.snapshot()
        chunk_count = 0
        byte_count = 0
        first_chunk_recorded = False
        terminal_status = "completed"
        buffer = ""
        response_id: str | None = None
        usage: dict[str, Any] | None = None
        self.metrics.record_streaming_request_started(target)
        try:
            async with asyncio.timeout(runtime_config.streaming_max_duration_seconds), aclosing(upstream) as chunks:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    chunk_count += 1
                    byte_count += len(chunk)
                    if chunk_count > runtime_config.streaming_max_chunks:
                        raise ServiceError(
                            "STREAM_LIMIT_EXCEEDED",
                            f"Responses stream emitted {chunk_count} chunks; limit is {runtime_config.streaming_max_chunks}.",
                            diagnostic_code="STREAM_CHUNK_LIMIT_EXCEEDED",
                        )
                    if byte_count > runtime_config.streaming_max_bytes:
                        raise ServiceError(
                            "STREAM_LIMIT_EXCEEDED",
                            f"Responses stream emitted {byte_count} bytes; limit is {runtime_config.streaming_max_bytes}.",
                            diagnostic_code="STREAM_BYTE_LIMIT_EXCEEDED",
                        )
                    if not first_chunk_recorded:
                        first_chunk_recorded = True
                        self.metrics.record_streaming_first_chunk(target, time.monotonic() - start)
                    self.metrics.record_streaming_chunk(target, len(chunk))
                    buffer += chunk.decode("utf-8", errors="ignore")
                    emitted: list[str] = []
                    while "\n" in buffer:
                        raw, buffer = buffer.split("\n", 1)
                        stripped = raw.strip()
                        if not stripped.startswith("data:"):
                            emitted.append(raw + "\n")
                            continue
                        data = stripped[5:].strip()
                        if not data or data == "[DONE]":
                            emitted.append(raw + "\n")
                            continue
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise ServiceError(
                                "UPSTREAM_RESPONSE_INVALID",
                                "Responses stream emitted invalid JSON.",
                            ) from exc
                        if not isinstance(event, dict):
                            raise ServiceError(
                                "UPSTREAM_RESPONSE_INVALID",
                                "Responses stream event must be an object.",
                            )
                        raw_response = event.get("response")
                        if isinstance(raw_response, dict):
                            if isinstance(raw_response.get("id"), str):
                                response_id = raw_response["id"]
                            if isinstance(raw_response.get("usage"), dict):
                                usage = raw_response["usage"]
                                self.metrics.record_streaming_usage_event(target)
                        projected = project_responses_stream_event(
                            event, expected_model=self.settings.runtime("main_llm").model
                        )
                        emitted.append(
                            "data: "
                            + json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
                            + "\n"
                        )
                    if emitted:
                        yield "".join(emitted).encode("utf-8")
                if buffer:
                    yield buffer.encode("utf-8")
        except (asyncio.CancelledError, GeneratorExit):
            terminal_status = "client_disconnect"
            phase = "mid_stream" if chunk_count else "before_first_chunk"
            self.metrics.record_streaming_client_disconnect(target, phase)
            self.metrics.record_streaming_error(target, "CLIENT_DISCONNECT", phase)
            raise
        except TimeoutError:
            terminal_status = "gateway_timeout"
            self.metrics.record_upstream_error(target, "GATEWAY_TIMEOUT")
            self.metrics.record_streaming_error(target, "GATEWAY_TIMEOUT", "mid_stream" if chunk_count else "before_first_chunk")
            yield _stream_error_event(
                ServiceError(
                    "UPSTREAM_TIMEOUT",
                    "Gateway request timed out before the Responses stream completed.",
                    diagnostic_code="GATEWAY_TIMEOUT",
                )
            )
        except ServiceError as exc:
            terminal_status = exc.code
            phase = "mid_stream" if chunk_count else "before_first_chunk"
            self.metrics.record_upstream_error(target, exc.operational_code)
            self.metrics.record_streaming_error(target, exc.operational_code, phase)
            yield _stream_error_event(exc)
        finally:
            elapsed = time.monotonic() - start
            self.metrics.record_streaming_completed(target, terminal_status, elapsed, chunk_count)
            self.metrics.record_upstream_request(target, "responses:stream", elapsed)
            record_stream_completion(
                status=sanitized_stream_status(terminal_status),
                usage=usage,
                response_id=response_id,
            )
