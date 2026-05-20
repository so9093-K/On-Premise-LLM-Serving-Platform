from __future__ import annotations

import asyncio
import json
import math
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
    validate_retrieval_rerank_request,
    validate_retrieval_score_request,
    validate_risk_response,
)

MAX_RETRIEVAL_DOCUMENTS = 32


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
    main_llm: Any
    embedding: Any
    embedding_clients: dict[str, Any]
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

    # ------------------------------------------------------------------
    # Retrieval: rerank, score
    # ------------------------------------------------------------------

    def _resolve_retrieval_mode(self, payload: dict[str, Any]) -> tuple[str, str]:
        model = str(payload.get("model") or self.settings.default_retrieval_model)
        score_mode = str(payload.get("score_mode") or "dense_cosine")
        profile = self.settings.embedding_profiles.get(model)
        if profile is None or not profile.retrieval_enabled:
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"Unsupported retrieval model: {model}", False, 422)
        if score_mode != "dense_cosine" or score_mode not in profile.score_modes:
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"{model} only supports dense_cosine score_mode.", False, 422)
        return model, score_mode

    def _retrieval_backend(self, model: str) -> str:
        profile = self.settings.embedding_profiles.get(model)
        return "dense_embedding" if profile and profile.retrieval_enabled else "unknown"

    def _validate_query_documents_payload(self, payload: dict[str, Any], *, operation: str) -> tuple[str, list[str], str, str]:
        if isinstance(payload, dict):
            if payload.get("model") == "local-colbert-ko":
                raise ServiceError("MODEL_CAPABILITY_MISMATCH", "local-colbert-ko has been removed; use local-embed-ko or local-embed with dense_cosine.", False, 422)
            if payload.get("score_mode") == "late_interaction_maxsim":
                raise ServiceError("MODEL_CAPABILITY_MISMATCH", "late_interaction_maxsim has been removed; only dense_cosine is supported.", False, 422)
        payload = (
            validate_retrieval_rerank_request(payload)
            if operation == "rerank"
            else validate_retrieval_score_request(payload)
        )
        query = payload.get("query")
        documents = payload.get("documents")
        if not isinstance(query, str) or not query.strip():
            raise ServiceError("VALIDATION_ERROR", "retrieval query must be a non-empty string.", False, 422)
        if not isinstance(documents, list) or not documents:
            raise ServiceError("VALIDATION_ERROR", "retrieval documents must be a non-empty array.", False, 422)
        if len(documents) > MAX_RETRIEVAL_DOCUMENTS:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"retrieval documents cannot exceed {MAX_RETRIEVAL_DOCUMENTS} items.",
                False,
                422,
            )
        if any(not isinstance(item, str) or not item.strip() for item in documents):
            raise ServiceError("VALIDATION_ERROR", "retrieval documents must contain non-empty strings.", False, 422)
        model, score_mode = self._resolve_retrieval_mode(payload)
        return query, documents, model, score_mode

    @staticmethod
    def _cosine(v1: list[float], v2: list[float]) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        return 0.0 if n1 == 0.0 or n2 == 0.0 else dot / (n1 * n2)

    def _apply_prompt_policy(self, model: str, role: str, text: str) -> str:
        profile = self.settings.embedding_profiles[model]
        policy = profile.prompt_policy.get(role, {}) if isinstance(profile.prompt_policy.get(role, {}), dict) else {}
        mode = policy.get("mode", "none")
        if mode == "prefix":
            return f"{policy.get('prefix', '')}{text}"
        if mode == "sentence_transformers_prompt_name":
            # TODO: vLLM /v1/embeddings does not accept prompt_name; this branch uses
            # fallback_prefix only. fallback_prefix="" is a no-op. No active profile uses
            # this mode — prefer explicit prefix mode for new profiles.
            return f"{policy.get('fallback_prefix', '')}{text}"
        return text

    async def _embed_texts(self, model: str, texts: list[str], *, role: str) -> list[list[float]]:
        profile = self.settings.embedding_profiles[model]
        client = self.clients.embedding_clients.get(model)
        if client is None:
            raise ServiceError("MODEL_UNAVAILABLE", f"{model} embedding runtime is unavailable.", True, 503)
        input_texts = [self._apply_prompt_policy(model, role, text) for text in texts]
        response = await asyncio.wait_for(
            client.post_json("embeddings", {"model": model, "input": input_texts}),
            timeout=self.settings.gateway_timeout_seconds,
        )
        response = validate_embedding_response(
            response,
            expected_model=model,
            expected_count=len(texts),
            expected_dimensions=profile.default_dimensions,
        )
        data = sorted(response.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in data]

    async def _score_dense_cosine(self, model: str, query: str, documents: list[str]) -> list[float]:
        profile = self.settings.embedding_profiles[model]
        query_vectors = await self._embed_texts(model, [query], role="retrieval_query")
        query_vec = query_vectors[0]
        scores: list[float] = []
        batch_size = max(1, int(profile.request_parameter_policy.get("max_embedding_batch_size", 16)))
        for start in range(0, len(documents), batch_size):
            batch = documents[start:start + batch_size]
            doc_vectors = await self._embed_texts(model, batch, role="retrieval_document")
            scores.extend(self._cosine(query_vec, doc_vec) for doc_vec in doc_vectors)
        return scores

    async def rerank_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        query, documents, model, score_mode = self._validate_query_documents_payload(payload, operation="rerank")
        backend = self._retrieval_backend(model)
        target = model
        top_n = payload.get("top_n")
        start = time.monotonic()
        status_code = 200
        try:
            raw_scores = await self._score_dense_cosine(model, query, documents)
        except TimeoutError as exc:
            status_code = 504
            self.metrics.record_upstream_error(target, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the retrieval runtime completed.", True, 504) from exc
        except ServiceError as exc:
            status_code = exc.status_code or 500
            self.metrics.record_upstream_error(target, exc.code)
            raise
        finally:
            elapsed = time.monotonic() - start
            self.metrics.record_upstream_request(target, "retrieval/rerank", elapsed)
            self.metrics.record_retrieval_request(
                route="/v1/retrieval/rerank",
                model=model,
                backend=backend,
                score_mode=score_mode,
                status_code=status_code,
                elapsed_seconds=elapsed,
                item_count=len(documents),
            )

        results = sorted(
            [{"index": i, "document": doc, "score": s} for i, (doc, s) in enumerate(zip(documents, raw_scores))],
            key=lambda x: x["score"],
            reverse=True,
        )
        if top_n is not None:
            results = results[:top_n]
        return {"model": model, "score_mode": score_mode, "results": results}

    async def score_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("top_n") is not None:
            raise ServiceError(
                "VALIDATION_ERROR",
                "top_n is not supported on the score endpoint. Use the rerank endpoint for filtered ranking.",
                False,
                422,
            )
        query, documents, model, score_mode = self._validate_query_documents_payload(payload, operation="score")
        backend = self._retrieval_backend(model)
        target = model
        start = time.monotonic()
        status_code = 200
        try:
            raw_scores = await self._score_dense_cosine(model, query, documents)
        except TimeoutError as exc:
            status_code = 504
            self.metrics.record_upstream_error(target, "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the retrieval runtime completed.", True, 504) from exc
        except ServiceError as exc:
            status_code = exc.status_code or 500
            self.metrics.record_upstream_error(target, exc.code)
            raise
        finally:
            elapsed = time.monotonic() - start
            self.metrics.record_upstream_request(target, "retrieval/score", elapsed)
            self.metrics.record_retrieval_request(
                route="/v1/retrieval/score",
                model=model,
                backend=backend,
                score_mode=score_mode,
                status_code=status_code,
                elapsed_seconds=elapsed,
                item_count=len(documents),
            )

        return {
            "model": model,
            "score_mode": score_mode,
            "scores": [{"index": i, "score": s} for i, s in enumerate(raw_scores)],
        }
