from __future__ import annotations

from types import SimpleNamespace

from scripts.models.check_hf_model_config import (
    classify_exception,
    interpretation_for_shape,
    shape_from_config,
    tokenizer_canary_from_tokenizer,
)


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


class _TinyUnkTokenizer:
    unk_token = "<unk>"
    unk_token_id = 3
    vocab_size = 5

    def __len__(self) -> int:
        return 24

    def __call__(self, text: str, *, add_special_tokens: bool = False):
        return SimpleNamespace(input_ids=[3])

    def convert_ids_to_tokens(self, ids: list[int]) -> list[str]:
        return ["<unk>" for _ in ids]

    def decode(self, ids: list[int], *, skip_special_tokens: bool = False) -> str:
        return "<unk>"


class _NormalTokenizer:
    unk_token = "<unk>"
    unk_token_id = 3
    vocab_size = 258000

    def __len__(self) -> int:
        return 258024

    def __call__(self, text: str, *, add_special_tokens: bool = False):
        return SimpleNamespace(input_ids=[721, 4300, 576, 6081, 603])

    def convert_ids_to_tokens(self, ids: list[int]) -> list[str]:
        return ["The", "capital", "of", "France", "is"]

    def decode(self, ids: list[int], *, skip_special_tokens: bool = False) -> str:
        return "The capital of France is"


def test_tokenizer_canary_flags_incomplete_artifacts() -> None:
    canary = tokenizer_canary_from_tokenizer(
        model="example/bad",
        tokenizer=_TinyUnkTokenizer(),
        text="The capital of France is",
        min_vocab_size=1000,
        min_token_count=2,
    )

    assert canary.ok is False
    assert canary.classification == "TOKENIZER_VOCAB_TOO_SMALL"
    assert canary.tokens == ["<unk>"]


def test_tokenizer_canary_accepts_normal_tokenizer() -> None:
    canary = tokenizer_canary_from_tokenizer(
        model="example/good",
        tokenizer=_NormalTokenizer(),
        text="The capital of France is",
        min_vocab_size=1000,
        min_token_count=2,
    )

    assert canary.ok is True
    assert canary.classification is None
