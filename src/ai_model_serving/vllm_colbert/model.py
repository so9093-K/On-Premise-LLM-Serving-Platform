from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn
from transformers import AutoModel


def _model_dir_from_config(vllm_config: Any) -> Path:
    model_config = getattr(vllm_config, "model_config", None)
    hf_config = getattr(model_config, "hf_config", None)
    for value in (
        getattr(hf_config, "_name_or_path", None),
        getattr(model_config, "model", None),
        getattr(model_config, "model_path", None),
    ):
        if value:
            return Path(str(value))
    raise RuntimeError("Cannot resolve ColBERT-ko prepared artifact path from vLLM config.")


def _load_projection(path: Path) -> nn.Module:
    try:
        loaded = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        loaded = torch.load(path, map_location="cpu")
    except Exception as exc:
        raise RuntimeError(
            "Could not safely load ColBERT-ko proj.pt with weights_only=True. "
            "Prepare proj.pt as a state_dict-compatible artifact before running production vLLM native."
        ) from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(
            f"Unsupported ColBERT projection object: {type(loaded).__name__}. "
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
        raise RuntimeError("proj.pt does not contain a 2D projection weight.")

    projection = nn.Linear(weight.shape[1], weight.shape[0], bias=bias is not None)
    projection.weight.data.copy_(weight)
    if bias is not None:
        projection.bias.data.copy_(bias)
    if projection.out_features != 128:
        raise RuntimeError(f"ColBERT projection must output 128 dims, got {projection.out_features}.")
    return projection


class ColbertKoEmbeddingGemmaForTextEncoding(nn.Module):
    """vLLM pooling model wrapper that applies ColBERT-ko ``proj.pt``.

    The prepared artifact keeps the original upstream layout under
    ``encoder/``, ``tokenizer/`` and ``proj.pt``.  vLLM runs this module in
    pooling mode with ``token_embed`` so `/score` computes late-interaction
    MaxSim over projected, L2-normalized token vectors.
    """

    is_pooling_model = True
    attn_type = "encoder_only"
    default_seq_pooling_type = "LAST"
    default_tok_pooling_type = "ALL"
    score_type = "late-interaction"

    def __init__(self, *, vllm_config: Any, prefix: str = "") -> None:
        del prefix
        super().__init__()
        model_dir = _model_dir_from_config(vllm_config)
        self.encoder = AutoModel.from_pretrained(model_dir / "encoder", trust_remote_code=True)
        self.projection = _load_projection(model_dir / "proj.pt")
        self.normalize_embeddings = True

        from vllm.model_executor.layers.pooler import DispatchPooler, Pooler

        pooler_config = vllm_config.model_config.pooler_config
        if hasattr(DispatchPooler, "for_embedding"):
            self.pooler = DispatchPooler.for_embedding(pooler_config)
        else:
            self.pooler = DispatchPooler(
                {
                    "token_embed": Pooler.for_token_embed(pooler_config),
                    "embed": Pooler.for_embed(pooler_config),
                }
            )

    def load_weights(self, weights: Any) -> set[str]:
        # We load the original encoder/ and proj.pt directly from the prepared
        # artifact to preserve the upstream key layout.  vLLM still expects the
        # model to report initialized parameter names so its default loader can
        # verify that startup did not leave parameters at random init.
        del weights
        return set(self.state_dict())

    def forward(self, input_ids: torch.Tensor, positions: torch.Tensor | None = None, **kwargs: Any) -> torch.Tensor:
        del positions
        attention_mask = kwargs.get("attention_mask")
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)
        flatten_output = input_ids.ndim == 1
        if flatten_output:
            input_ids = input_ids.unsqueeze(0)
            attention_mask = attention_mask.unsqueeze(0)
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        hidden_states = outputs.last_hidden_state.to(dtype=self.projection.weight.dtype)
        projected = self.projection(hidden_states)
        if self.normalize_embeddings:
            projected = F.normalize(projected, p=2, dim=-1)
        if flatten_output:
            projected = projected.squeeze(0)
        return projected
