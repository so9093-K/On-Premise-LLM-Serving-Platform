from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn


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


def _pack_flattened_inputs(
    input_ids: torch.Tensor,
    positions: torch.Tensor | None,
    pad_token_id: int,
) -> tuple[torch.Tensor, torch.Tensor, list[int]]:
    """positions 기반으로 flattened 1D input_ids를 시퀀스별로 분리해 패딩된 2D 배치를 만든다.

    vLLM pooling 경로는 여러 시퀀스를 하나의 연속된 1D 텐서로 전달할 수 있다.
    positions 값이 0으로 다시 시작되는 지점이 새 시퀀스의 시작점이다.
    이 함수 없이 unsqueeze(0)만 적용하면 여러 시퀀스가 encoder self-attention을
    공유해 batch size / 스케줄링 상태에 따라 score가 달라지는 재현성 문제가 발생한다.

    Returns:
        padded_ids: [num_seqs, max_len] int tensor
        attention_mask: [num_seqs, max_len] int tensor
        lengths: [num_seqs] 각 시퀀스의 실제 토큰 수
    """
    if positions is None:
        raise RuntimeError(
            "ColBERT-ko requires positions for flattened 1D input_ids. "
            "vLLM must provide positions to enable sequence boundary restoration."
        )
    if input_ids.ndim != 1 or positions.ndim != 1:
        raise RuntimeError(
            f"_pack_flattened_inputs: input_ids and positions must both be 1D, "
            f"got input_ids.ndim={input_ids.ndim}, positions.ndim={positions.ndim}."
        )
    if input_ids.numel() != positions.numel():
        raise RuntimeError(
            f"input_ids and positions length mismatch: "
            f"{input_ids.numel()} vs {positions.numel()}."
        )
    if input_ids.numel() == 0:
        raise RuntimeError("input_ids is empty; cannot pack zero tokens.")

    # positions가 0으로 재시작되는 지점을 새 시퀀스 시작으로 본다.
    starts = torch.where(positions == 0)[0].tolist()
    if not starts or starts[0] != 0:
        starts = [0] + [s for s in starts if s != 0]

    ends = starts[1:] + [input_ids.numel()]
    sequences = [input_ids[s:e] for s, e in zip(starts, ends) if e > s]
    if not sequences:
        raise RuntimeError("No valid sequences found in flattened input_ids.")
    lengths = [seq.numel() for seq in sequences]

    max_len = max(lengths)
    padded = input_ids.new_full((len(sequences), max_len), pad_token_id)
    attention_mask = input_ids.new_zeros((len(sequences), max_len))

    for i, seq in enumerate(sequences):
        padded[i, : seq.numel()] = seq
        attention_mask[i, : seq.numel()] = 1

    return padded, attention_mask, lengths


class ColbertKoEmbeddingGemmaForTextEncoding(nn.Module):
    """vLLM pooling model wrapper that applies ColBERT-ko ``proj.pt``.

    The prepared artifact keeps the original upstream layout under
    ``encoder/``, ``tokenizer/`` and ``proj.pt``.  vLLM runs this module in
    pooling mode with ``token_embed`` so `/score` computes late-interaction
    MaxSim over projected, L2-normalized token vectors.

    1D flattened input handling:
        vLLM may pack multiple sequences into a single flattened 1D tensor.
        ``forward`` uses ``positions`` to restore sequence boundaries so each
        sequence is encoded independently, preventing cross-sequence
        self-attention leakage that would make scores non-deterministic across
        batch sizes and scheduler packing states.
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
        return {name for name, _ in self.named_parameters()}

    def _encode_2d(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        outputs = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        hidden_states = outputs.last_hidden_state.to(dtype=self.projection.weight.dtype)
        projected = self.projection(hidden_states)
        if self.normalize_embeddings:
            projected = F.normalize(projected, p=2, dim=-1)
        return projected

    def forward(self, input_ids: torch.Tensor, positions: torch.Tensor | None = None, **kwargs: Any) -> torch.Tensor:
        if input_ids.ndim == 1:
            # vLLM may deliver multiple sequences as a single flattened tensor.
            # Restore boundaries via positions to prevent cross-sequence attention.
            pad_token_id = int(getattr(getattr(self.encoder, "config", None), "pad_token_id", None) or 0)
            padded_ids, attention_mask, lengths = _pack_flattened_inputs(input_ids, positions, pad_token_id)
            projected = self._encode_2d(padded_ids, attention_mask)
            # Slice each sequence to its actual length, then re-flatten.
            slices = [projected[i, : lengths[i]] for i in range(len(lengths))]
            return torch.cat(slices, dim=0)

        # 2D path: standard [batch, seq] input from non-packing vLLM schedulers.
        attention_mask = kwargs.get("attention_mask")
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)
        return self._encode_2d(input_ids, attention_mask)
