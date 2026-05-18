from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any


@dataclass(frozen=True)
class ColbertReferenceConfig:
    model_id: str = "sigridjineth/colbert-ko-embeddinggemma-300m"
    encoder_subfolder: str = "encoder"
    tokenizer_subfolder: str = "tokenizer"
    projection_filename: str = "proj.pt"
    projection_dim: int = 128
    query_max_tokens: int = 128
    doc_max_tokens: int = 192
    normalize_embeddings: bool = True
    device: str | None = None


def _optional_imports() -> tuple[Any, Any, Any, Any]:
    try:
        import torch
        import torch.nn.functional as functional
        from huggingface_hub import hf_hub_download
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "ColBERT reference adapter requires optional dependencies: torch, transformers, huggingface_hub."
        ) from exc
    return torch, functional, hf_hub_download, (AutoModel, AutoTokenizer)


def _projection_weight_and_bias(loaded: Any, projection_dim: int) -> tuple[Any, Any | None]:
    if not isinstance(loaded, dict):
        raise RuntimeError(
            f"Unsupported projection object: {type(loaded).__name__}. "
            "Expected a state_dict-compatible object."
        )

    state = loaded.get("state_dict", loaded)
    weight = None
    bias = None
    for key, value in state.items():
        if key.endswith("weight") and getattr(value, "ndim", None) == 2:
            weight = value
        elif key.endswith("bias") and getattr(value, "ndim", None) == 1:
            bias = value
    if weight is None:
        raise RuntimeError("Could not find a 2D projection weight in proj.pt.")
    if int(weight.shape[0]) != projection_dim:
        raise RuntimeError(
            f"Projection dimension mismatch: expected {projection_dim}, got {weight.shape[0]}."
        )
    return weight, bias


def maxsim_score(
    query_embeddings: Sequence[Sequence[float]],
    document_embeddings: Sequence[Sequence[float]],
    *,
    query_mask: Sequence[bool] | None = None,
    document_mask: Sequence[bool] | None = None,
) -> float:
    """Pure-Python ColBERT MaxSim score used for deterministic fixture tests."""
    if query_mask is None:
        query_mask = [True] * len(query_embeddings)
    if document_mask is None:
        document_mask = [True] * len(document_embeddings)
    active_docs = [embedding for embedding, keep in zip(document_embeddings, document_mask, strict=True) if keep]
    if not active_docs:
        return 0.0

    total = 0.0
    for query_embedding, keep_query in zip(query_embeddings, query_mask, strict=True):
        if not keep_query:
            continue
        best = max(
            sum(float(q) * float(d) for q, d in zip(query_embedding, document_embedding, strict=True))
            for document_embedding in active_docs
        )
        total += best
    return total


def maxsim_scores(
    query_embeddings: Sequence[Sequence[float]],
    documents_embeddings: Sequence[Sequence[Sequence[float]]],
    *,
    query_mask: Sequence[bool] | None = None,
    documents_mask: Sequence[Sequence[bool]] | None = None,
) -> list[float]:
    if documents_mask is None:
        documents_mask = [[True] * len(document_embeddings) for document_embeddings in documents_embeddings]
    return [
        maxsim_score(query_embeddings, document_embeddings, query_mask=query_mask, document_mask=document_mask)
        for document_embeddings, document_mask in zip(documents_embeddings, documents_mask, strict=True)
    ]


def rerank_from_scores(documents: Sequence[str], scores: Sequence[float]) -> list[dict[str, Any]]:
    return sorted(
        [
            {"index": idx, "document": document, "score": float(score)}
            for idx, (document, score) in enumerate(zip(documents, scores, strict=True))
        ],
        key=lambda item: item["score"],
        reverse=True,
    )


class ColbertReferenceAdapter:
    """Reference ColBERT-Ko scoring path used before vLLM native promotion.

    This adapter intentionally loads the repository as it exists upstream:
    encoder weights from ``encoder/``, tokenizer assets from ``tokenizer/``, and
    the late-interaction projection head from ``proj.pt``.  It is not imported by
    the normal Gateway hot path unless an operator explicitly wires it in.
    """

    def __init__(self, config: ColbertReferenceConfig | None = None) -> None:
        self.config = config or ColbertReferenceConfig()
        self._torch, self._functional, self._hf_hub_download, models = _optional_imports()
        auto_model, auto_tokenizer = models
        self.device = self.config.device or ("cuda" if self._torch.cuda.is_available() else "cpu")
        self.tokenizer = auto_tokenizer.from_pretrained(
            self.config.model_id,
            subfolder=self.config.tokenizer_subfolder,
        )
        self.encoder = auto_model.from_pretrained(
            self.config.model_id,
            subfolder=self.config.encoder_subfolder,
        ).to(self.device)
        self.encoder.eval()
        self.projection = self._load_projection().to(self.device)
        self.projection.eval()

    def _load_projection(self) -> Any:
        torch = self._torch
        path = self._hf_hub_download(self.config.model_id, filename=self.config.projection_filename)
        try:
            loaded = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError:
            loaded = torch.load(path, map_location="cpu")
        except Exception as exc:
            raise RuntimeError(
                f"Could not safely load {self.config.projection_filename} with weights_only=True. "
                "Repackage it as a state_dict before using it for parity checks."
            ) from exc

        weight, bias = _projection_weight_and_bias(loaded, self.config.projection_dim)
        layer = torch.nn.Linear(weight.shape[1], weight.shape[0], bias=bias is not None)
        layer.weight.data.copy_(weight)
        if bias is not None:
            layer.bias.data.copy_(bias)
        return layer

    def token_embeddings(self, texts: list[str], *, max_tokens: int) -> tuple[Any, Any]:
        if not texts:
            raise ValueError("texts must not be empty")
        torch = self._torch
        with torch.inference_mode():
            batch = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=max_tokens,
                return_tensors="pt",
            )
            batch = {key: value.to(self.device) for key, value in batch.items()}
            hidden = self.encoder(**batch).last_hidden_state
            projected = self.projection(hidden)
            if self.config.normalize_embeddings:
                projected = self._functional.normalize(projected, p=2, dim=-1)
            mask = batch["attention_mask"].bool()
            return projected, mask

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not query:
            raise ValueError("query must not be empty")
        if not documents:
            return []
        torch = self._torch
        query_embeddings, query_mask = self.token_embeddings([query], max_tokens=self.config.query_max_tokens)
        doc_embeddings, doc_mask = self.token_embeddings(documents, max_tokens=self.config.doc_max_tokens)
        query_embeddings = query_embeddings[0]
        query_mask = query_mask[0]
        scores: list[float] = []
        with torch.inference_mode():
            for embeddings, mask in zip(doc_embeddings, doc_mask, strict=True):
                sim = query_embeddings @ embeddings.transpose(0, 1)
                sim = sim.masked_fill(~mask.unsqueeze(0), float("-inf"))
                maxsim = sim.max(dim=1).values
                maxsim = maxsim.masked_select(query_mask)
                scores.append(float(maxsim.sum().detach().cpu()))
        return scores

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        return rerank_from_scores(documents, self.score(query, documents))
