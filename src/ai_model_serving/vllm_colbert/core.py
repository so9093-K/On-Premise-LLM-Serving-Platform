from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn


def load_projection(path: Path) -> nn.Module:
    try:
        loaded = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        loaded = torch.load(path, map_location="cpu")
    except Exception as exc:
        raise RuntimeError(
            "Could not safely load ColBERT-ko proj.pt with weights_only=True. "
            "Prepare proj.pt as a state_dict-compatible artifact before running the production vLLM runtime."
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


class ColbertKoCore(nn.Module):
    """ColBERT-ko core: 2D token ids plus tokenizer attention mask.

    This module preserves the upstream inference meaning:
    tokenizer output -> encoder -> proj.pt -> L2-normalized token embeddings.
    vLLM flattened runtime representations must be restored before calling it.
    """

    def __init__(self, encoder: nn.Module, projection: nn.Module, *, normalize_embeddings: bool = True) -> None:
        super().__init__()
        self.encoder = encoder
        self.projection = projection
        self.normalize_embeddings = normalize_embeddings

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None, **_: Any) -> torch.Tensor:
        if input_ids.ndim != 2:
            raise RuntimeError(f"ColBERT-ko core requires 2D input_ids, got input_ids.ndim={input_ids.ndim}.")
        if attention_mask is None:
            raise RuntimeError("ColBERT-ko 2D input requires attention_mask.")
        if attention_mask.ndim != 2:
            raise RuntimeError(f"ColBERT-ko 2D input requires 2D attention_mask, got ndim={attention_mask.ndim}.")
        if tuple(attention_mask.shape) != tuple(input_ids.shape):
            raise RuntimeError(
                "ColBERT-ko attention_mask shape must match input_ids shape, "
                f"got input_ids={tuple(input_ids.shape)} attention_mask={tuple(attention_mask.shape)}."
            )

        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        hidden_states = outputs.last_hidden_state.to(dtype=self.projection.weight.dtype)
        projected = self.projection(hidden_states)
        if self.normalize_embeddings:
            projected = F.normalize(projected, p=2, dim=-1)
        return projected
