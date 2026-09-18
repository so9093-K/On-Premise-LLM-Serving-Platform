from __future__ import annotations

import pytest

from scripts.lib.service_endpoint import service_base_url


SERVICE = {
    "host_env_bind": "MAIN_MODEL_VLLM_BIND_ADDR",
    "legacy_host_env_bind": "MAIN_LLM_VLLM_BIND_ADDR",
    "host_env_port": "MAIN_MODEL_VLLM_PORT",
    "legacy_host_env_port": "MAIN_LLM_VLLM_PORT",
    "default_bind": "0.0.0.0",
    "default_host_port": 9401,
}


def test_service_endpoint_reads_legacy_main_model_exposure_aliases(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_LLM_VLLM_BIND_ADDR", "127.0.0.2")
    monkeypatch.setenv("MAIN_LLM_VLLM_PORT", "9501")

    assert service_base_url(SERVICE) == "http://127.0.0.2:9501"


def test_service_endpoint_rejects_conflicting_exposure_aliases(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_MODEL_VLLM_PORT", "9401")
    monkeypatch.setenv("MAIN_LLM_VLLM_PORT", "9501")

    with pytest.raises(RuntimeError, match="conflicting env keys MAIN_LLM_VLLM_PORT and MAIN_MODEL_VLLM_PORT"):
        service_base_url(SERVICE)
