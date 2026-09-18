from __future__ import annotations

import pytest

from ai_model_serving.env_compat import renamed_env_value


def test_renamed_env_value_prefers_canonical_and_reads_legacy() -> None:
    assert renamed_env_value({"MAIN_MODEL_ALIAS": "local-main"}, "MAIN_MODEL_ALIAS", "MAIN_LLM_MODEL") == "local-main"
    assert renamed_env_value({"MAIN_LLM_MODEL": "local-main"}, "MAIN_MODEL_ALIAS", "MAIN_LLM_MODEL") == "local-main"


def test_renamed_env_value_accepts_equal_dual_values() -> None:
    assert renamed_env_value(
        {"MAIN_MODEL_ALIAS": "local-main", "MAIN_LLM_MODEL": "local-main"},
        "MAIN_MODEL_ALIAS",
        "MAIN_LLM_MODEL",
    ) == "local-main"


def test_renamed_env_value_treats_empty_side_as_unset() -> None:
    assert renamed_env_value(
        {"MAIN_MODEL_ALIAS": "", "MAIN_LLM_MODEL": "local-main"},
        "MAIN_MODEL_ALIAS",
        "MAIN_LLM_MODEL",
    ) == "local-main"


def test_renamed_env_value_rejects_ambiguous_values() -> None:
    with pytest.raises(RuntimeError, match="conflicting env keys MAIN_LLM_MODEL and MAIN_MODEL_ALIAS"):
        renamed_env_value(
            {"MAIN_MODEL_ALIAS": "canonical", "MAIN_LLM_MODEL": "legacy"},
            "MAIN_MODEL_ALIAS",
            "MAIN_LLM_MODEL",
        )
