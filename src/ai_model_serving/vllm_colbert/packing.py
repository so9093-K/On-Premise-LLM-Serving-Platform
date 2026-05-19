from __future__ import annotations

import torch


def _pack_flattened_inputs(
    input_ids: torch.Tensor,
    positions: torch.Tensor | None,
    pad_token_id: int,
) -> tuple[torch.Tensor, torch.Tensor, list[int]]:
    """Restore vLLM flattened 1D input into a padded 2D batch.

    This is a vLLM executor compatibility adapter. It is not part of the
    ColBERT-ko model semantics; it converts vLLM's runtime representation into
    the 2D ``input_ids`` plus ``attention_mask`` contract expected by the core.
    """
    if positions is None:
        raise RuntimeError(
            "ColBERT-ko requires positions for vLLM flattened 1D input_ids. "
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

    # vLLM positions restart at zero for each packed sequence.
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
