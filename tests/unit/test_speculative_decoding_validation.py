"""Validation failure tests for speculative decoding configuration.

Tests validate_speculative_decoding_config() and validate_rendered_speculative_command()
against intentionally malformed configs. All cases should return non-empty error lists.
"""
from __future__ import annotations

import json

import pytest

from ai_model_serving.runtime_validation.speculative_validation import (
    validate_rendered_speculative_command,
    validate_speculative_decoding_config,
)

_VALID_FEATURES: dict = {
    "speculative_decoding": {
        "enabled": True,
        "method": "mtp",
        "mtp_drafter_model": "google/gemma-4-26B-A4B-it-assistant",
        "num_speculative_tokens": 2,
    }
}

_VALID_COMMAND = [
    "python", "-m", "vllm.entrypoints.openai.api_server",
    "--model", "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic",
    "--speculative-config",
    '{"method":"mtp","model":"google/gemma-4-26B-A4B-it-assistant","num_speculative_tokens":2}',
]


# ---------------------------------------------------------------------------
# validate_speculative_decoding_config — valid baseline passes
# ---------------------------------------------------------------------------

def test_valid_speculative_config_produces_no_errors() -> None:
    errors = validate_speculative_decoding_config("main_llm", _VALID_FEATURES, is_main_llm=True)
    assert errors == []


def test_disabled_speculative_config_always_valid() -> None:
    features = {"speculative_decoding": {"enabled": False}}
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert errors == []


def test_absent_speculative_config_valid() -> None:
    errors = validate_speculative_decoding_config("main_llm", {}, is_main_llm=True)
    assert errors == []


# ---------------------------------------------------------------------------
# validate_speculative_decoding_config — method failures
# ---------------------------------------------------------------------------

def test_fails_when_method_is_missing() -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "mtp_drafter_model": "google/gemma-4-26B-A4B-it-assistant",
            "num_speculative_tokens": 2,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("method" in e for e in errors)


def test_fails_when_method_is_not_mtp() -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "eagle",
            "mtp_drafter_model": "some/model",
            "num_speculative_tokens": 2,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("method" in e and "mtp" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_speculative_decoding_config — drafter model failures
# ---------------------------------------------------------------------------

def test_fails_when_drafter_model_is_missing() -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "mtp",
            "num_speculative_tokens": 2,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("mtp_drafter_model" in e for e in errors)


def test_fails_when_drafter_model_is_empty_string() -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "mtp",
            "mtp_drafter_model": "",
            "num_speculative_tokens": 2,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("mtp_drafter_model" in e for e in errors)


def test_fails_when_gemma4_assistant_used_with_non_mtp_method() -> None:
    """google/gemma-4-26B-A4B-it-assistant is MTP-only; non-mtp method must be rejected."""
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "eagle",
            "mtp_drafter_model": "google/gemma-4-26B-A4B-it-assistant",
            "num_speculative_tokens": 2,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    # Should have both: method != mtp AND gemma4-assistant + non-mtp
    assert len(errors) >= 2
    assert any("gemma-4-26B-A4B-it-assistant" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_speculative_decoding_config — num_speculative_tokens failures
# ---------------------------------------------------------------------------

def test_fails_when_num_speculative_tokens_is_missing() -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "mtp",
            "mtp_drafter_model": "google/gemma-4-26B-A4B-it-assistant",
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("num_speculative_tokens" in e for e in errors)


@pytest.mark.parametrize("value", [0, -1, -5])
def test_fails_when_num_speculative_tokens_is_not_positive(value: int) -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "mtp",
            "mtp_drafter_model": "google/gemma-4-26B-A4B-it-assistant",
            "num_speculative_tokens": value,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("num_speculative_tokens" in e for e in errors)


@pytest.mark.parametrize("value", [2.0, "2", None])
def test_fails_when_num_speculative_tokens_is_not_integer(value: object) -> None:
    features = {
        "speculative_decoding": {
            "enabled": True,
            "method": "mtp",
            "mtp_drafter_model": "google/gemma-4-26B-A4B-it-assistant",
            "num_speculative_tokens": value,
        }
    }
    errors = validate_speculative_decoding_config("main_llm", features, is_main_llm=True)
    assert any("num_speculative_tokens" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_speculative_decoding_config — non-main_llm guard
# ---------------------------------------------------------------------------

def test_fails_when_speculative_enabled_on_embedding() -> None:
    errors = validate_speculative_decoding_config("embedding", _VALID_FEATURES, is_main_llm=False)
    assert any("non-main_llm" in e for e in errors)


def test_fails_when_speculative_enabled_on_embedding_ko() -> None:
    errors = validate_speculative_decoding_config("embedding_ko", _VALID_FEATURES, is_main_llm=False)
    assert any("non-main_llm" in e for e in errors)


def test_fails_when_speculative_enabled_on_risk_prompt() -> None:
    errors = validate_speculative_decoding_config("risk_prompt", _VALID_FEATURES, is_main_llm=False)
    assert any("non-main_llm" in e for e in errors)


def test_non_main_llm_without_speculative_is_valid() -> None:
    errors = validate_speculative_decoding_config("embedding", {}, is_main_llm=False)
    assert errors == []


# ---------------------------------------------------------------------------
# validate_rendered_speculative_command — valid baseline
# ---------------------------------------------------------------------------

def test_valid_command_produces_no_errors() -> None:
    errors = validate_rendered_speculative_command("main_llm", _VALID_COMMAND)
    assert errors == []


# ---------------------------------------------------------------------------
# validate_rendered_speculative_command — missing flag
# ---------------------------------------------------------------------------

def test_fails_when_speculative_config_flag_missing_from_command() -> None:
    cmd = [t for t in _VALID_COMMAND if t != "--speculative-config" and "mtp" not in t]
    errors = validate_rendered_speculative_command("main_llm", cmd)
    assert any("--speculative-config" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_rendered_speculative_command — JSON key failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_key", ["method", "model", "num_speculative_tokens"])
def test_fails_when_speculative_config_json_missing_required_key(missing_key: str) -> None:
    spec = {
        "method": "mtp",
        "model": "google/gemma-4-26B-A4B-it-assistant",
        "num_speculative_tokens": 2,
    }
    del spec[missing_key]
    cmd = [*_VALID_COMMAND[:-1], json.dumps(spec)]
    # Replace the last speculative-config value
    cmd_list = list(_VALID_COMMAND)
    idx = cmd_list.index("--speculative-config")
    cmd_list[idx + 1] = json.dumps(spec)
    errors = validate_rendered_speculative_command("main_llm", cmd_list)
    assert any(missing_key in e for e in errors)


def test_fails_when_speculative_config_json_contains_disallowed_target_runtime_key() -> None:
    spec = {
        "method": "mtp",
        "model": "google/gemma-4-26B-A4B-it-assistant",
        "num_speculative_tokens": 2,
        "tensor_parallel_size": 1,  # disallowed: target runtime key
    }
    cmd_list = list(_VALID_COMMAND)
    idx = cmd_list.index("--speculative-config")
    cmd_list[idx + 1] = json.dumps(spec)
    errors = validate_rendered_speculative_command("main_llm", cmd_list)
    assert any("tensor_parallel_size" in e or "disallowed" in e for e in errors)


@pytest.mark.parametrize("disallowed_key", ["max_model_len", "gpu_memory_utilization", "dtype", "quantization"])
def test_fails_when_speculative_config_json_contains_various_disallowed_keys(disallowed_key: str) -> None:
    spec = {
        "method": "mtp",
        "model": "google/gemma-4-26B-A4B-it-assistant",
        "num_speculative_tokens": 2,
        disallowed_key: "some_value",
    }
    cmd_list = list(_VALID_COMMAND)
    idx = cmd_list.index("--speculative-config")
    cmd_list[idx + 1] = json.dumps(spec)
    errors = validate_rendered_speculative_command("main_llm", cmd_list)
    assert errors, f"Expected errors for disallowed key {disallowed_key!r}"


def test_fails_when_speculative_config_value_is_not_valid_json() -> None:
    cmd_list = list(_VALID_COMMAND)
    idx = cmd_list.index("--speculative-config")
    cmd_list[idx + 1] = "{not valid json"
    errors = validate_rendered_speculative_command("main_llm", cmd_list)
    assert any("not valid JSON" in e for e in errors)
