from __future__ import annotations

from ai_model_serving.access_profile import access_profile_env_values


def test_operator_bind_policy_preserves_legacy_main_model_bind_alias() -> None:
    values = access_profile_env_values(
        "edge",
        current={"MAIN_LLM_VLLM_BIND_ADDR": "192.168.10.25"},
    )

    assert values["MAIN_MODEL_VLLM_BIND_ADDR"] == "192.168.10.25"
    assert "MAIN_LLM_VLLM_BIND_ADDR" not in values
