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
    validate_retrieval_token_embeddings_request,
    validate_risk_response,
)

RETRIEVAL_MODE_BY_MODEL = {
    "local-embed": ("dense_cosine", "dense_embedding"),
    "local-colbert-ko": ("late_interaction_maxsim", "vllm_native_late_interaction"),
}
MAX_RETRIEVAL_DOCUMENTS = 32
MAX_TOKEN_EMBEDDING_TEXTS = 8
MAX_TOKEN_EMBEDDING_TOKENS = 192


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
    colbert_ko: Any | None


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

    # ------------------------------------------------------------------
    # Retrieval: rerank, score, token-embeddings
    # ------------------------------------------------------------------

    def _resolve_retrieval_mode(self, payload: dict[str, Any]) -> tuple[str, str]:
        embed_model = self.settings.embedding.model if self.settings.embedding else "local-embed"
        colbert_model = "local-colbert-ko"
        model = payload.get("model")
        score_mode = payload.get("score_mode")

        if model is None:
            model = embed_model if score_mode == "dense_cosine" else "local-colbert-ko"

        if model not in {embed_model, colbert_model}:
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"Unsupported retrieval model: {model}", False, 422)

        if score_mode is None:
            score_mode = "dense_cosine" if model == embed_model else "late_interaction_maxsim"

        if model == embed_model and score_mode != "dense_cosine":
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"{model} only supports dense_cosine score_mode.", False, 422)
        if model != embed_model and score_mode != "late_interaction_maxsim":
            raise ServiceError("MODEL_CAPABILITY_MISMATCH", f"{model} only supports late_interaction_maxsim score_mode.", False, 422)

        return model, score_mode

    def _retrieval_backend(self, model: str) -> str:
        return RETRIEVAL_MODE_BY_MODEL.get(model, ("unknown", "unknown"))[1]

    def _validate_query_documents_payload(self, payload: dict[str, Any], *, operation: str) -> tuple[str, list[str], str, str]:
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

    def _validate_token_embeddings_payload(self, payload: dict[str, Any]) -> tuple[list[str], int | None]:
        if isinstance(payload, dict) and payload.get("model") not in (None, "local-colbert-ko"):
            raise ServiceError(
                "MODEL_CAPABILITY_MISMATCH",
                "token-embeddings only supports model local-colbert-ko.",
                False,
                422,
            )
        payload = validate_retrieval_token_embeddings_request(payload)
        if payload.get("model") != "local-colbert-ko":
            raise ServiceError(
                "MODEL_CAPABILITY_MISMATCH",
                "token-embeddings only supports model local-colbert-ko.",
                False,
                422,
            )
        texts = payload.get("texts")
        if not isinstance(texts, list) or not texts:
            raise ServiceError("VALIDATION_ERROR", "token-embeddings texts must be a non-empty array.", False, 422)
        if len(texts) > MAX_TOKEN_EMBEDDING_TEXTS:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"token-embeddings texts cannot exceed {MAX_TOKEN_EMBEDDING_TEXTS} items.",
                False,
                422,
            )
        if any(not isinstance(item, str) or not item.strip() for item in texts):
            raise ServiceError("VALIDATION_ERROR", "token-embeddings texts must contain non-empty strings.", False, 422)

        # truncate_prompt_tokens (new standard) and truncate_to_tokens (deprecated alias) handling.
        # If both are present and conflict, return 422. If both equal, allow.
        # Internal vLLM call always uses truncate_prompt_tokens.
        truncate_new = payload.get("truncate_prompt_tokens")
        truncate_legacy = payload.get("truncate_to_tokens")
        if truncate_new is not None and truncate_legacy is not None:
            if truncate_new != truncate_legacy:
                raise ServiceError(
                    "VALIDATION_ERROR",
                    "truncate_prompt_tokens and truncate_to_tokens conflict. Provide only one, or ensure both have the same value.",
                    False,
                    422,
                )
            truncate = truncate_new
        elif truncate_new is not None:
            truncate = truncate_new
        elif truncate_legacy is not None:
            truncate = truncate_legacy
        else:
            return texts, None

        # -1 means use model max length; positive values are validated by schema.
        if not isinstance(truncate, int) or (truncate != -1 and truncate < 1):
            raise ServiceError("VALIDATION_ERROR", "truncate value must be -1 or a positive integer.", False, 422)
        return texts, truncate

    @staticmethod
    def _cosine(v1: list[float], v2: list[float]) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        return 0.0 if n1 == 0.0 or n2 == 0.0 else dot / (n1 * n2)

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        embed_model = self.settings.embedding.model if self.settings.embedding else "local-embed"
        response = await asyncio.wait_for(
            self.clients.embedding.post_json("embeddings", {"model": embed_model, "input": texts}),
            timeout=self.settings.gateway_timeout_seconds,
        )
        response = validate_embedding_response(
            response,
            expected_model=embed_model,
            expected_count=len(texts),
            expected_dimensions=None,
        )
        data = sorted(response.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in data]

    async def _score_dense_cosine(self, query: str, documents: list[str]) -> list[float]:
        vectors = await self._embed_texts([query] + documents)
        query_vec = vectors[0]
        return [self._cosine(query_vec, doc_vec) for doc_vec in vectors[1:]]

    async def _score_late_interaction(
        self,
        query: str,
        documents: list[str],
        *,
        max_tokens_per_query: int = 128,
        max_tokens_per_doc: int = 192,
        truncate_prompt_tokens: int | None = None,
        truncation_side: str | None = None,
    ) -> list[float]:
        colbert_ko = getattr(self.clients, "colbert_ko", None)
        if colbert_ko is None:
            raise ServiceError(
                "MODEL_UNAVAILABLE",
                "local-colbert-ko vLLM native runtime is unavailable.",
                True,
                503,
            )
        body: dict[str, Any] = {
            "model": "local-colbert-ko",
            "encoding_format": "float",
            "text_1": query,
            "text_2": documents,
            "max_tokens_per_query": max_tokens_per_query,
            "max_tokens_per_doc": max_tokens_per_doc,
        }
        if truncate_prompt_tokens is not None:
            body["truncate_prompt_tokens"] = truncate_prompt_tokens
        if truncation_side is not None:
            body["truncation_side"] = truncation_side
        response = await asyncio.wait_for(
            colbert_ko.post_json("score", body),
            timeout=self.settings.gateway_timeout_seconds,
        )
        data = response.get("data", [])
        if response.get("object") != "list" or not isinstance(data, list) or len(data) != len(documents):
            raise ServiceError(
                "UPSTREAM_SCHEMA_ERROR",
                "ColBERT score upstream response data must match requested documents.",
                True,
                502,
            )
        scores: list[float] = []
        for expected_index, item in enumerate(data):
            if not isinstance(item, dict) or item.get("index") != expected_index:
                raise ServiceError(
                    "UPSTREAM_SCHEMA_ERROR",
                    f"ColBERT score upstream response data[{expected_index}].index must be {expected_index}.",
                    True,
                    502,
                )
            score = item.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                raise ServiceError(
                    "UPSTREAM_SCHEMA_ERROR",
                    f"ColBERT score upstream response data[{expected_index}].score must be numeric.",
                    True,
                    502,
                )
            scores.append(float(score))
        return scores

    async def rerank_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        query, documents, model, score_mode = self._validate_query_documents_payload(payload, operation="rerank")
        backend = self._retrieval_backend(model)
        target = "local-colbert-ko" if score_mode == "late_interaction_maxsim" else (
            self.settings.embedding.logical_id if self.settings.embedding else "local-embed"
        )
        top_n = payload.get("top_n")
        max_tokens_per_query = int(payload.get("max_tokens_per_query") or 128)
        max_tokens_per_doc = int(payload.get("max_tokens_per_doc") or 192)
        truncate_prompt_tokens = payload.get("truncate_prompt_tokens")
        truncation_side = payload.get("truncation_side") or None
        start = time.monotonic()
        status_code = 200
        try:
            if score_mode == "late_interaction_maxsim":
                raw_scores = await self._score_late_interaction(
                    query,
                    documents,
                    max_tokens_per_query=max_tokens_per_query,
                    max_tokens_per_doc=max_tokens_per_doc,
                    truncate_prompt_tokens=truncate_prompt_tokens,
                    truncation_side=truncation_side,
                )
            else:
                raw_scores = await self._score_dense_cosine(query, documents)
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
        target = "local-colbert-ko" if score_mode == "late_interaction_maxsim" else (
            self.settings.embedding.logical_id if self.settings.embedding else "local-embed"
        )
        max_tokens_per_query = int(payload.get("max_tokens_per_query") or 128)
        max_tokens_per_doc = int(payload.get("max_tokens_per_doc") or 192)
        truncate_prompt_tokens = payload.get("truncate_prompt_tokens")
        truncation_side = payload.get("truncation_side") or None
        start = time.monotonic()
        status_code = 200
        try:
            if score_mode == "late_interaction_maxsim":
                raw_scores = await self._score_late_interaction(
                    query,
                    documents,
                    max_tokens_per_query=max_tokens_per_query,
                    max_tokens_per_doc=max_tokens_per_doc,
                    truncate_prompt_tokens=truncate_prompt_tokens,
                    truncation_side=truncation_side,
                )
            else:
                raw_scores = await self._score_dense_cosine(query, documents)
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

    async def get_token_embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        texts, truncate = self._validate_token_embeddings_payload(payload)
        colbert_ko = getattr(self.clients, "colbert_ko", None)
        if colbert_ko is None:
            raise ServiceError(
                "MODEL_UNAVAILABLE",
                "local-colbert-ko vLLM native runtime is unavailable.",
                True,
                503,
            )
        request_body: dict[str, Any] = {
            "model": "local-colbert-ko",
            "input": texts,
            "task": "token_embed",
        }
        if truncate is not None:
            request_body["truncate_prompt_tokens"] = truncate
        start = time.monotonic()
        status_code = 200
        response: dict[str, Any] | None = None
        try:
            response = await asyncio.wait_for(
                colbert_ko.post_json("pooling", request_body),
                timeout=self.settings.gateway_timeout_seconds,
            )
        except TimeoutError as exc:
            status_code = 504
            self.metrics.record_upstream_error("local-colbert-ko", "GATEWAY_TIMEOUT")
            raise ServiceError("UPSTREAM_TIMEOUT", "Gateway request timed out before the ColBERT runtime completed.", True, 504) from exc
        except ServiceError as exc:
            status_code = exc.status_code or 500
            self.metrics.record_upstream_error("local-colbert-ko", exc.code)
            raise
        finally:
            elapsed = time.monotonic() - start
            self.metrics.record_upstream_request("local-colbert-ko", "token-embeddings", elapsed)
            self.metrics.record_retrieval_request(
                route="/v1/retrieval/token-embeddings",
                model="local-colbert-ko",
                backend="vllm_native_late_interaction",
                score_mode="token_embed",
                status_code=status_code,
                elapsed_seconds=elapsed,
                item_count=len(texts),
                response_bytes=len(json.dumps(response, ensure_ascii=False).encode("utf-8")) if response is not None else 0,
            )

        # Upstream schema validation: /v1/pooling token-embed response contract.
        if response.get("object") != "list":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", "token-embeddings upstream response must have object=list.", True, 502)
        raw_data = response.get("data")
        if not isinstance(raw_data, list):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", "token-embeddings upstream response data must be a list.", True, 502)
        if len(raw_data) != len(texts):
            raise ServiceError(
                "UPSTREAM_SCHEMA_ERROR",
                f"token-embeddings upstream returned {len(raw_data)} items for {len(texts)} texts.",
                True,
                502,
            )

        embeddings = []
        expected_dim: int | None = None
        for expected_index, item in enumerate(sorted(raw_data, key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0)):
            if not isinstance(item, dict):
                raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"token-embeddings upstream data[{expected_index}] must be an object.", True, 502)
            if item.get("index") != expected_index:
                raise ServiceError(
                    "UPSTREAM_SCHEMA_ERROR",
                    f"token-embeddings upstream data[{expected_index}].index must be {expected_index}.",
                    True,
                    502,
                )
            emb = item.get("data", item.get("embedding", []))
            if not isinstance(emb, list):
                raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"token-embeddings upstream data[{expected_index}].embedding must be a list.", True, 502)
            # Normalize to list[list[float]] (token-level embeddings matrix)
            if emb and isinstance(emb[0], list):
                token_vectors = emb
            else:
                token_vectors = [emb] if emb else []
            # Validate all token vector dimensions are equal
            if token_vectors:
                dims = {len(v) for v in token_vectors if isinstance(v, list)}
                if len(dims) != 1:
                    raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"token-embeddings upstream data[{expected_index}] has inconsistent token vector dimensions.", True, 502)
                dim = dims.pop()
                if expected_dim is None:
                    expected_dim = dim
                elif dim != expected_dim:
                    raise ServiceError("UPSTREAM_SCHEMA_ERROR", "token-embeddings upstream returned inconsistent dimensions across texts.", True, 502)
            embeddings.append({"index": expected_index, "token_count": len(token_vectors), "embedding": token_vectors})
        return {"model": "local-colbert-ko", "embeddings": embeddings}
