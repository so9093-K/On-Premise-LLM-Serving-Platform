from __future__ import annotations

from types import SimpleNamespace

from scripts.models.check_hf_model_config import classify_exception, interpretation_for_shape, shape_from_config


def test_classify_hidden_head_config_validation_failure() -> None:
    exc = ValueError("The hidden size (1792) is not a multiple of the number of attention heads (24).")
    assert classify_exception(exc) == "CONFIG_VALIDATION_HIDDEN_HEAD_MISMATCH"


def test_shape_from_config_reports_explicit_head_dim_mismatch() -> None:
    config = SimpleNamespace(
        model_type="llama",
        architectures=["LlamaForCausalLM"],
        hidden_size=1792,
        num_attention_heads=24,
        num_key_value_heads=8,
        head_dim=128,
    )

    shape = shape_from_config("kakaocorp/kanana-safeguard-prompt-2.1b", config)

    assert shape.hidden_size_divisible_by_attention_heads is False
    assert shape.attention_projection_width == 3072
    assert shape.attention_projection_matches_hidden_size is False
    assert shape.requires_runtime_head_dim_support is True
    assert "honor explicit head_dim" in interpretation_for_shape(shape)
