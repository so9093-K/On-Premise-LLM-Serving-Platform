from __future__ import annotations

from scripts.config import setup_env


LEGACY_TO_CANONICAL = {
    "RISK_PROMPT_BASE_URL": "PROMPT_INJECTION_DETECTOR_BASE_URL",
    "RISK_PROMPT_MODEL": "PROMPT_INJECTION_DETECTOR_MODEL",
    "RISK_PROMPT_TIMEOUT_SECONDS": "PROMPT_INJECTION_DETECTOR_TIMEOUT_SECONDS",
    "RISK_PROMPT_MAX_CONCURRENCY": "PROMPT_INJECTION_DETECTOR_MAX_CONCURRENCY",
    "RISK_PROMPT_QUEUE_TIMEOUT_SECONDS": "PROMPT_INJECTION_DETECTOR_QUEUE_TIMEOUT_SECONDS",
    "RISK_PROMPT_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "PROMPT_INJECTION_DETECTOR_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "RISK_PROMPT_CIRCUIT_BREAKER_RESET_SECONDS": "PROMPT_INJECTION_DETECTOR_CIRCUIT_BREAKER_RESET_SECONDS",
    "RISK_PROMPT_VLLM_BIND_ADDR": "PROMPT_INJECTION_DETECTOR_RUNTIME_BIND_ADDR",
    "RISK_PROMPT_VLLM_PORT": "PROMPT_INJECTION_DETECTOR_RUNTIME_PORT",
}


def test_sync_env_migrates_prompt_injection_detector_keys_without_value_loss(tmp_path):
    values = {
        "RISK_PROMPT_BASE_URL": "http://legacy-prompt:9503/v1",
        "RISK_PROMPT_MODEL": "custom-prompt-detector",
        "RISK_PROMPT_TIMEOUT_SECONDS": "7",
        "RISK_PROMPT_MAX_CONCURRENCY": "3",
        "RISK_PROMPT_QUEUE_TIMEOUT_SECONDS": "4",
        "RISK_PROMPT_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "6",
        "RISK_PROMPT_CIRCUIT_BREAKER_RESET_SECONDS": "23",
        "RISK_PROMPT_VLLM_BIND_ADDR": "192.168.10.30",
        "RISK_PROMPT_VLLM_PORT": "9503",
    }
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        + "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    migrated = setup_env.read_env_values(out)
    for legacy_key, canonical_key in LEGACY_TO_CANONICAL.items():
        assert migrated[canonical_key] == values[legacy_key]
        assert legacy_key not in migrated


def test_sync_env_rejects_conflicting_prompt_injection_detector_keys(tmp_path, capsys):
    for index, (legacy_key, canonical_key) in enumerate(LEGACY_TO_CANONICAL.items()):
        out = tmp_path / f".env-prompt-{index}"
        out.write_text(
            "BUILD_PROFILE=compose\n"
            f"{legacy_key}=legacy-value\n"
            f"{canonical_key}=canonical-value\n",
            encoding="utf-8",
        )

        rc = setup_env.main(["--sync-env", "--env-file", str(out)])

        assert rc == 2
        assert (
            f"conflicting env keys {legacy_key} and {canonical_key}"
            in capsys.readouterr().err
        )


def test_sync_env_migrates_old_prompt_detector_compose_default_url(tmp_path):
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "PROMPT_INJECTION_DETECTOR_BASE_URL=http://risk-prompt-vllm:9403/v1\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert (
        values["PROMPT_INJECTION_DETECTOR_BASE_URL"]
        == "http://prompt-injection-detector-runtime:9403/v1"
    )


def test_sync_env_applies_key_then_value_migration_for_legacy_prompt_url(tmp_path):
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "RISK_PROMPT_BASE_URL=http://risk-prompt-vllm:9403/v1\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert "RISK_PROMPT_BASE_URL" not in values
    assert (
        values["PROMPT_INJECTION_DETECTOR_BASE_URL"]
        == "http://prompt-injection-detector-runtime:9403/v1"
    )


def test_sync_env_preserves_custom_prompt_detector_base_url(tmp_path):
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "PROMPT_INJECTION_DETECTOR_BASE_URL=http://custom-detector.example:9503/v1\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert (
        values["PROMPT_INJECTION_DETECTOR_BASE_URL"]
        == "http://custom-detector.example:9503/v1"
    )
