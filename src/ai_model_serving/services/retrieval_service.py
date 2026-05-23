from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Protocol

from ..contracts import (
    validate_embedding_response,
    validate_retrieval_rerank_request,
    validate_retrieval_score_request,
)
from ..errors import ServiceError
from ..metrics import Metrics
from ..runtime_clients.ports import JsonRuntimeClient
from ..settings import AppSettings

MAX_RETRIEVAL_DOCUMENTS = 32


type RetrievalPayload = tuple[str, list[str], str, str, int | None]


class RetrievalClientSet(Protocol):
    embedding_clients: dict[str, JsonRuntimeClient]


class RetrievalService:
    """Use-case layer for dense retrieval scoring and reranking."""

    def __init__(self, settings: AppSettings, clients: RetrievalClientSet, metrics: Metrics) -> None:
        self.settings = settings
        self.clients = clients
        self.metrics = metrics

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

    def _validate_query_documents_payload(self, payload: dict[str, Any], *, operation: str) -> RetrievalPayload:
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
        truncate_prompt_tokens = payload.get("truncate_prompt_tokens")
        if truncate_prompt_tokens is not None:
            policy = self.settings.embedding_profiles[model].request_parameter_policy
            max_tokens = int(policy.get("max_truncate_prompt_tokens", 2048))
            if (
                not isinstance(truncate_prompt_tokens, int)
                or truncate_prompt_tokens == 0
                or truncate_prompt_tokens < -1
                or truncate_prompt_tokens > max_tokens
            ):
                raise ServiceError(
                    "VALIDATION_ERROR",
                    f"truncate_prompt_tokens must be -1 or an integer between 1 and {max_tokens}.",
                    False,
                    422,
                )
        return query, documents, model, score_mode, truncate_prompt_tokens

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

    async def _embed_texts(
        self,
        model: str,
        texts: list[str],
        *,
        role: str,
        truncate_prompt_tokens: int | None = None,
    ) -> list[list[float]]:
        profile = self.settings.embedding_profiles[model]
        client = self.clients.embedding_clients.get(model)
        if client is None:
            raise ServiceError("MODEL_UNAVAILABLE", f"{model} embedding runtime is unavailable.", True, 503)
        input_texts = [self._apply_prompt_policy(model, role, text) for text in texts]
        upstream_payload: dict[str, Any] = {"model": model, "input": input_texts}
        if truncate_prompt_tokens is not None:
            upstream_payload["truncate_prompt_tokens"] = truncate_prompt_tokens
        response = await asyncio.wait_for(
            client.post_json("embeddings", upstream_payload),
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

    async def _score_dense_cosine(
        self,
        model: str,
        query: str,
        documents: list[str],
        *,
        truncate_prompt_tokens: int | None = None,
    ) -> list[float]:
        profile = self.settings.embedding_profiles[model]
        query_vectors = await self._embed_texts(
            model,
            [query],
            role="retrieval_query",
            truncate_prompt_tokens=truncate_prompt_tokens,
        )
        query_vec = query_vectors[0]
        scores: list[float] = []
        batch_size = max(1, int(profile.request_parameter_policy.get("max_embedding_batch_size", 16)))
        for start in range(0, len(documents), batch_size):
            batch = documents[start:start + batch_size]
            doc_vectors = await self._embed_texts(
                model,
                batch,
                role="retrieval_document",
                truncate_prompt_tokens=truncate_prompt_tokens,
            )
            scores.extend(self._cosine(query_vec, doc_vec) for doc_vec in doc_vectors)
        return scores

    async def rerank_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        query, documents, model, score_mode, truncate_prompt_tokens = self._validate_query_documents_payload(payload, operation="rerank")
        backend = self._retrieval_backend(model)
        target = model
        top_n = payload.get("top_n")
        start = time.monotonic()
        status_code = 200
        try:
            raw_scores = await self._score_dense_cosine(
                model,
                query,
                documents,
                truncate_prompt_tokens=truncate_prompt_tokens,
            )
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
        query, documents, model, score_mode, truncate_prompt_tokens = self._validate_query_documents_payload(payload, operation="score")
        backend = self._retrieval_backend(model)
        target = model
        start = time.monotonic()
        status_code = 200
        try:
            raw_scores = await self._score_dense_cosine(
                model,
                query,
                documents,
                truncate_prompt_tokens=truncate_prompt_tokens,
            )
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
