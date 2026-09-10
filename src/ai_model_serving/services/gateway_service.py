from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import replace
from typing import Any, Protocol

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
from ..contracts.chat_response import RetryableStructuredOutputError
from ..errors import ServiceError
from ..logging_policy import record_stream_completion
from ..metrics import Metrics, sanitized_stream_status
from ..runtime_clients.ports import JsonRuntimeClient, StreamingRuntimeClient
from ..serving_profile import NAMED_TOOL_CHOICE_UPSTREAM_REQUIRED_SINGLE
from ..settings import AppSettings
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
    """Gateway 제어 값을 활성 runtime 요청과 응답 검증 규칙으로 변환한다."""
    response_format = payload.get("response_format")
    response_format_type = response_format.get("type") if isinstance(response_format, dict) else None
    json_schema_wrapper = response_format.get("json_schema") if isinstance(response_format, dict) else None
    json_schema = json_schema_wrapper.get("schema") if isinstance(json_schema_wrapper, dict) else None
    tools = payload.get("tools")
    allowed_tool_names = frozenset(
        tool["function"]["name"]
        for tool in tools or []
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    )
    requested_tool_choice = payload.get("tool_choice")
    tool_choice_name = None
    if isinstance(requested_tool_choice, dict):
        function = requested_tool_choice.get("function")
        tool_choice_name = function.get("name") if isinstance(function, dict) else None
        tool_choice = "named"
    elif isinstance(requested_tool_choice, str):
        tool_choice = requested_tool_choice
    else:
        tool_choice = "auto" if allowed_tool_names else None
    tool_policy = (policy or {}).get("tool_calling", {})
    profile_allows_parallel = (
        isinstance(tool_policy, dict)
        and tool_policy.get("allow_parallel_tool_calls") is True
    )
    parallel_tool_calls = payload.get("parallel_tool_calls", profile_allows_parallel) is True
    upstream = dict(payload)
    # Gateway 계약에서는 받지만 런타임에 넘길 이유가 없는 필드(예: OpenAI 표준의
    # user 식별자)를 제거한다. embedding 경로와 같은 정책 키를 쓴다.
    for name in (policy or {}).get("drop_upstream_parameters", []):
        upstream.pop(name, None)
    requested_reasoning = upstream.pop("reasoning", None)
    reasoning_policy = ((policy or {}).get("reasoning") or {})
    reasoning_enabled = (
        requested_reasoning
        if isinstance(requested_reasoning, bool)
        else reasoning_policy.get("default", False) is True
    )
    upstream_parameter = reasoning_policy.get("upstream_parameter")
    if isinstance(upstream_parameter, str) and upstream_parameter:
        upstream[upstream_parameter] = reasoning_enabled
    # runtime의 reasoning parser는 chat_template_kwargs만 보고 thinking 여부를 판단한다.
    # 끈 요청에서 이 값을 생략하면 parser는 기본값(thinking on)으로 읽어 "아직 사고 중"이라
    # 판단하고, 그동안 structured output grammar를 적용하지 않아 json_schema 응답이
    # 자유 텍스트로 나온다. 그래서 켤 때와 끌 때를 모두 명시한다.
    declared_kwargs = reasoning_policy.get("upstream_chat_template_kwargs") or {}
    if declared_kwargs:
        template_kwargs = dict(upstream.get("chat_template_kwargs", {}))
        for name, enabled_value in declared_kwargs.items():
            if reasoning_enabled:
                template_kwargs[name] = enabled_value
            elif isinstance(enabled_value, bool):
                template_kwargs[name] = not enabled_value
        upstream["chat_template_kwargs"] = template_kwargs
    # OpenAI-compatible runtimes may default parallel_tool_calls to true. A profile
    # that does not advertise parallel calls must therefore send false explicitly;
    # request validation alone cannot constrain an omitted upstream default.
    if allowed_tool_names and not profile_allows_parallel:
        upstream.setdefault("parallel_tool_calls", False)
    # vLLM은 named tool_choice 경로에서 tool_calls를 반환하면서 finish_reason=stop을
    # 쓴다(auto/required만 tool_calls). OpenAI 계약과 validate_chat_response는 도구
    # 호출 시 tool_calls를 요구하므로, 프로필이 선언하면 의미가 같은 "required +
    # 선택된 tool 하나"로 upstream 요청을 바꾼다. 응답을 사후 수정하지 않고 요청에서
    # 해결하므로 streaming도 같은 형태를 얻는다 -- streaming relay는 chunk를 파싱하지
    # 않고 그대로 전달하기 때문에 응답 교정으로는 그 경로를 덮을 수 없다.
    #
    # expectations는 아래에서 원본 요청 값으로 만든다. 따라서 함수명 일치, named
    # 준수, 병렬 금지 검증은 정규화와 무관하게 클라이언트가 요청한 계약을 그대로
    # 확인한다.
    if (
        tool_choice == "named"
        and tool_choice_name is not None
        and isinstance(tool_policy, dict)
        and tool_policy.get("named_tool_choice_upstream") == NAMED_TOOL_CHOICE_UPSTREAM_REQUIRED_SINGLE
    ):
        selected_tools = [
            tool
            for tool in tools or []
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool["function"].get("name") == tool_choice_name
        ]
        # 요청 검증이 이미 이름 일치를 보장하지만, 비어 있으면 정규화하지 않고
        # 원래 요청을 그대로 보내 실패 원인을 바꾸지 않는다.
        if selected_tools:
            upstream["tools"] = selected_tools
            upstream["tool_choice"] = "required"
            upstream["parallel_tool_calls"] = False
    expectations = ChatResponseExpectations(
        response_format_type=response_format_type,
        json_schema=dict(json_schema) if isinstance(json_schema, dict) else None,
        expect_logprobs=payload.get("logprobs") is True,
        stream=payload.get("stream") is True,
        allowed_tool_names=allowed_tool_names,
        tool_choice=tool_choice,
        tool_choice_name=tool_choice_name,
        parallel_tool_calls=parallel_tool_calls,
    )
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


class StreamingResponseObserver:
    """본문을 버퍼링하지 않고 전달 중인 SSE 바이트에서 진단 정보를 관찰한다.

    The Gateway relays the original bytes unchanged.  This observer only reads
    what the non-streaming path already records for the request log, so a single
    request_id answers the same questions on both paths.  Prompt text and token
    deltas are never read, and none of these values become metric labels.

    ``last_usage``
        마지막으로 본 OpenAI ``usage`` 객체. 토큰 개수는 민감정보가 아니고
        non-stream 경로가 이미 로그에 남기는 값이라 숫자를 버리지 않는다.
    ``response_id``
        처음 본 chunk의 ``id``. 한 생성의 모든 chunk가 같은 값을 나르므로
        먼저 본 것을 쓴다. vLLM은 이 id를 자기 컨테이너 로그에도 남기므로,
        Gateway request_id와 runtime 로그를 잇는 열쇠가 된다.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self.last_usage: dict[str, Any] | None = None
        self.response_id: str | None = None

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
            if not isinstance(event, dict):
                continue
            if self.response_id is None and isinstance(event.get("id"), str) and event["id"]:
                self.response_id = event["id"]
            if isinstance(event.get("usage"), dict):
                usage_events += 1
                self.last_usage = event["usage"]
        return usage_events

class GatewayClientSet(Protocol):
    main_llm: StreamingRuntimeClient
    embedding_clients: dict[str, JsonRuntimeClient]
    risk_adapter: JsonRuntimeClient | None


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
        # 중간에 잘리면 validate_chat_response가 STRUCTURED_OUTPUT_INVALID로 잡아낸다.
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
                    if isinstance(exc, RetryableStructuredOutputError) and attempt < attempts_allowed:
                        self.metrics.record_upstream_error(self.settings.runtime("main_llm").logical_id, "STRUCTURED_OUTPUT_RETRIED")
                        continue
                    self.metrics.record_upstream_error(self.settings.runtime("main_llm").logical_id, exc.operational_code)
                    raise
        except TimeoutError as exc:
            self.metrics.record_upstream_error(self.settings.runtime("main_llm").logical_id, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the chat runtime completed.", diagnostic_code="GATEWAY_TIMEOUT") from exc
        finally:
            self.metrics.record_upstream_request(
                self.settings.runtime("main_llm").logical_id,
                "chat/completions",
                time.monotonic() - start,
            )


    async def stream_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        active_modalities: tuple[str, ...] | None = None,
        gateway_policy: dict[str, Any] | None = None,
    ) -> AsyncIterator[bytes]:
        """Admit the streaming request, then return the SSE relay iterator.

        Admission runs here rather than inside the relay so that CIRCUIT_OPEN and
        QUEUE_TIMEOUT reach the caller as ordinary ``ServiceError``s, before any
        response header is committed.  Only failures that genuinely happen after
        the SSE transport is chosen end up as in-band error events.
        """
        payload, _expectations = self._chat_upstream_payload(
            self._validate_chat_payload(
                payload, active_modalities=active_modalities, gateway_policy=gateway_policy
            ),
            gateway_policy,
        )
        start = time.monotonic()
        target = self.settings.runtime("main_llm").logical_id
        try:
            upstream = await self.clients.main_llm.open_stream("chat/completions", payload)
        except ServiceError as exc:
            self.metrics.record_upstream_error(target, exc.operational_code)
            self.metrics.record_streaming_error(target, exc.operational_code, "admission")
            raise
        return self._relay_chat_stream(upstream, target=target, start=start)

    async def _relay_chat_stream(self, upstream: Any, *, target: str, start: float) -> AsyncIterator[bytes]:
        emitted_chunk = False
        first_chunk_recorded = False
        chunk_count = 0
        byte_count = 0
        terminal_status = "completed"
        observer = StreamingResponseObserver()
        self.metrics.record_streaming_request_started(target)
        try:
            # aclosing으로 감싸야 client가 끊었을 때 upstream generator가 GC가
            # 아니라 그 자리에서 닫히고, admission slot이 즉시 반납된다.
            async with asyncio.timeout(self.settings.streaming_max_duration_seconds), aclosing(upstream) as chunks:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    emitted_chunk = True
                    chunk_count += 1
                    byte_count += len(chunk)
                    if chunk_count > self.settings.streaming_max_chunks:
                        raise ServiceError(
                            "STREAM_LIMIT_EXCEEDED", f"stream emitted {chunk_count} chunks; limit is {self.settings.streaming_max_chunks}. Reduce max_tokens or retry without stream=true.",
                            diagnostic_code="STREAM_CHUNK_LIMIT_EXCEEDED",
                        )
                    if byte_count > self.settings.streaming_max_bytes:
                        raise ServiceError(
                            "STREAM_LIMIT_EXCEEDED", f"stream emitted {byte_count} bytes; limit is {self.settings.streaming_max_bytes}. Reduce max_tokens or retry without stream=true.",
                            diagnostic_code="STREAM_BYTE_LIMIT_EXCEEDED",
                        )
                    if not first_chunk_recorded:
                        first_chunk_recorded = True
                        self.metrics.record_streaming_first_chunk(target, time.monotonic() - start)
                    self.metrics.record_streaming_chunk(target, len(chunk))
                    for _ in range(observer.observe(chunk)):
                        self.metrics.record_streaming_usage_event(target)
                    yield chunk
        # client가 응답을 버리면 Starlette은 이 generator를 닫는다. 그 경로는
        # CancelledError가 아니라 GeneratorExit이라, 예전에는 어느 분기에도
        # 걸리지 않고 finally의 기본값 "completed"로 기록됐다 -- 중단된 stream이
        # 정상 완료와 구분되지 않았고 streaming_client_disconnects_total은 0에
        # 머물렀다. 둘 다 여기서 받는다.
        except (asyncio.CancelledError, GeneratorExit):
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
            error = ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the chat stream completed.", diagnostic_code="GATEWAY_TIMEOUT")
            yield _stream_error_event(error)
        except ServiceError as exc:
            terminal_status = exc.code
            phase = "mid_stream" if emitted_chunk else "before_first_chunk"
            self.metrics.record_upstream_error(target, exc.operational_code)
            self.metrics.record_streaming_error(target, exc.operational_code, phase)
            yield _stream_error_event(exc)
        finally:
            elapsed = time.monotonic() - start
            self.metrics.record_streaming_completed(target, terminal_status, elapsed, chunk_count)
            self.metrics.record_upstream_request(
                target,
                "chat/completions:stream",
                elapsed,
            )
            # non-stream 경로의 record_upstream_response와 같은 필드를 채워,
            # request_id 하나로 두 경로를 같은 방식으로 조회할 수 있게 한다.
            record_stream_completion(
                status=sanitized_stream_status(terminal_status),
                usage=observer.last_usage,
                response_id=observer.response_id,
            )

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
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the embedding runtime completed.", diagnostic_code="GATEWAY_TIMEOUT") from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error(profile.model, exc.operational_code)
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
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the risk adapter completed.", diagnostic_code="GATEWAY_TIMEOUT") from exc
        except ServiceError as exc:
            self.metrics.record_upstream_error("risk-adapter", exc.operational_code)
            raise
        finally:
            self.metrics.record_upstream_request("risk-adapter", path, time.monotonic() - start)

    async def rerank_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.retrieval.rerank_documents(payload)

    async def score_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.retrieval.score_documents(payload)
