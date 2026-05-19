from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import torch
from torch import nn

from ai_model_serving.vllm_colbert.core import ColbertKoCore, load_projection
from ai_model_serving.vllm_colbert.packing import _pack_flattened_inputs


LOGGER = logging.getLogger(__name__)
TRACE_FORWARD_SHAPES_ENV = "COLBERT_KO_TRACE_FORWARD_SHAPES"


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


def _shape_of(value: Any) -> tuple[int, ...] | None:
    shape = getattr(value, "shape", None)
    if shape is None:
        return None
    return tuple(int(dim) for dim in shape)


def _trace_forward_shapes(
    *,
    input_ids: torch.Tensor,
    positions: torch.Tensor | None,
    attention_mask: torch.Tensor | None,
    selected_path: str,
    restored_lengths: list[int] | None = None,
) -> None:
    if os.getenv(TRACE_FORWARD_SHAPES_ENV) != "1":
        return
    payload: dict[str, Any] = {
        "event": "colbert_ko_forward_shape_trace",
        "input_ids_ndim": int(input_ids.ndim),
        "input_ids_shape": _shape_of(input_ids),
        "positions_present": positions is not None,
        "positions_shape": _shape_of(positions),
        "attention_mask_present": attention_mask is not None,
        "attention_mask_shape": _shape_of(attention_mask),
        "selected_path": selected_path,
    }
    if restored_lengths is not None:
        payload["restored_sequence_count"] = len(restored_lengths)
        payload["restored_lengths"] = [int(length) for length in restored_lengths]
        payload["restored_max_len"] = max(restored_lengths) if restored_lengths else 0
    LOGGER.info("ColBERT-ko forward shape trace: %s", payload)


class ColbertKoEmbeddingGemmaForTextEncoding(nn.Module):
    """vLLM pooling model wrapper that applies ColBERT-ko ``proj.pt``.

    The prepared artifact keeps the original upstream layout under
    ``encoder/``, ``tokenizer/`` and ``proj.pt``.  vLLM runs this module in
    pooling mode with ``token_embed`` so `/score` computes late-interaction
    MaxSim over projected, L2-normalized token vectors.

    vLLM flattened adapter path:
        vLLM may pack multiple sequences into a single flattened 1D tensor.
        ``forward`` uses ``positions`` to restore 2D ``input_ids`` and
        ``attention_mask`` before calling the ColBERT-ko core. This adapter is
        a vLLM executor compatibility boundary, not the model's core semantics.
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
        try:
            from transformers import AutoModel
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "ColBERT-ko vLLM runtime requires transformers. "
                "Use the dedicated ColBERT vLLM image or install the ColBERT runtime dependencies."
            ) from exc
        encoder = AutoModel.from_pretrained(model_dir / "encoder", trust_remote_code=True)
        projection = load_projection(model_dir / "proj.pt")
        self.core = ColbertKoCore(encoder, projection, normalize_embeddings=True)

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
        return {name for name, _ in self.named_parameters()}

    def forward(self, input_ids: torch.Tensor, positions: torch.Tensor | None = None, **kwargs: Any) -> torch.Tensor:
        if input_ids.ndim == 1:
            # vLLM executor compatibility adapter path. Restore the flattened
            # runtime representation to the 2D core contract.
            pad_token_id = int(getattr(getattr(self.core.encoder, "config", None), "pad_token_id", None) or 0)
            padded_ids, attention_mask, lengths = _pack_flattened_inputs(input_ids, positions, pad_token_id)
            _trace_forward_shapes(
                input_ids=input_ids,
                positions=positions,
                attention_mask=attention_mask,
                selected_path="vllm_flattened_adapter",
                restored_lengths=lengths,
            )
            projected = self.core(padded_ids, attention_mask)
            # Slice each sequence to its actual length, then re-flatten.
            slices = [projected[i, : lengths[i]] for i in range(len(lengths))]
            return torch.cat(slices, dim=0)

        # Direct 2D path: ColBERT-ko core semantics require tokenizer attention_mask.
        attention_mask = kwargs.get("attention_mask")
        if attention_mask is None:
            _trace_forward_shapes(
                input_ids=input_ids,
                positions=positions,
                attention_mask=None,
                selected_path="direct_2d_core",
            )
            raise RuntimeError("ColBERT-ko 2D input requires attention_mask.")
        _trace_forward_shapes(
            input_ids=input_ids,
            positions=positions,
            attention_mask=attention_mask,
            selected_path="direct_2d_core",
        )
        return self.core(input_ids, attention_mask)
