"""Pure validation logic for speculative decoding configuration.

Called from config_checks.py (ConfigOnlyChecks) and independently from unit tests.
Source of truth: configs/model_serving.yaml runtime_features.speculative_decoding.
"""
from __future__ import annotations

import json
from typing import Any

_GEMMA4_ASSISTANT = "google/gemma-4-26B-A4B-it-assistant"

# Keys that belong to the target runtime and must not appear inside
# --speculative-config JSON (which configures the drafter side only).
_DISALLOWED_SPEC_JSON_KEYS = frozenset({
    "tensor_parallel_size",
    "max_model_len",
    "gpu_memory_utilization",
    "max_num_seqs",
    "max_num_batched_tokens",
    "dtype",
    "quantization",
    "trust_remote_code",
})


def validate_speculative_decoding_config(
    model_key: str,
    features: dict[str, Any],
    *,
    is_main_llm: bool,
) -> list[str]:
    """Validate speculative_decoding config block. Returns list of errors (empty = valid)."""
    errors: list[str] = []
    spec = features.get("speculative_decoding") or {}

    if not is_main_llm:
        if spec.get("enabled") is True:
            errors.append(
                f"{model_key}: speculative_decoding.enabled must not be true on non-main_llm runtimes"
            )
        return errors

    if not spec.get("enabled"):
        return errors

    method = spec.get("method")
    drafter = (
        spec.get("mtp_drafter_model")
        or spec.get("speculative_drafter_model")
        or ""
    )
    num_tokens = spec.get("num_speculative_tokens")

    if method != "mtp":
        errors.append(
            f"{model_key}: speculative_decoding.method must be 'mtp', got {method!r}"
        )

    if not drafter:
        errors.append(
            f"{model_key}: speculative_decoding.mtp_drafter_model must not be empty"
        )

    if drafter == _GEMMA4_ASSISTANT and method != "mtp":
        errors.append(
            f"{model_key}: drafter {_GEMMA4_ASSISTANT!r} requires method='mtp', got {method!r}"
        )

    if num_tokens is None:
        errors.append(
            f"{model_key}: speculative_decoding.num_speculative_tokens is required"
        )
    elif not isinstance(num_tokens, int):
        errors.append(
            f"{model_key}: speculative_decoding.num_speculative_tokens must be an integer, "
            f"got {type(num_tokens).__name__}"
        )
    elif num_tokens <= 0:
        errors.append(
            f"{model_key}: speculative_decoding.num_speculative_tokens must be > 0, got {num_tokens}"
        )

    return errors


def validate_rendered_speculative_command(
    model_key: str,
    command: list[str],
) -> list[str]:
    """Validate --speculative-config flag in a rendered vLLM command list."""
    errors: list[str] = []

    if "--speculative-config" not in command:
        errors.append(f"{model_key}: rendered command is missing --speculative-config")
        return errors

    idx = command.index("--speculative-config")
    if idx + 1 >= len(command):
        errors.append(f"{model_key}: --speculative-config flag has no value in command")
        return errors

    raw = command[idx + 1]
    try:
        spec_json = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{model_key}: --speculative-config value is not valid JSON: {exc}")
        return errors

    for required_key in ("method", "model", "num_speculative_tokens"):
        if required_key not in spec_json:
            errors.append(
                f"{model_key}: --speculative-config JSON is missing required key {required_key!r}"
            )

    disallowed = _DISALLOWED_SPEC_JSON_KEYS & set(spec_json)
    if disallowed:
        errors.append(
            f"{model_key}: --speculative-config JSON contains disallowed target runtime keys: "
            + ", ".join(sorted(disallowed))
        )

    return errors
