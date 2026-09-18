from __future__ import annotations

from ai_model_serving.access_profile import access_profile_env_values


def test_operator_bind_policy_preserves_canonical_main_model_bind() -> None:
    values = access_profile_env_values(
        "private",
        current={"MAIN_MODEL_VLLM_BIND_ADDR": "192.168.10.25"},
    )

    assert values["MAIN_MODEL_VLLM_BIND_ADDR"] == "192.168.10.25"
