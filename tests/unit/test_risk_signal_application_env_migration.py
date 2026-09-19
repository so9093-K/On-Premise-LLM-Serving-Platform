from __future__ import annotations

from scripts.config import setup_env


def test_sync_env_migrates_risk_signal_application_keys_without_value_loss(tmp_path):
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "RISK_ADAPTER_BASE_URL=http://legacy-risk:9405\n"
        "RISK_ADAPTER_TIMEOUT_SECONDS=21\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert values["RISK_SIGNAL_SERVICE_BASE_URL"] == "http://legacy-risk:9405"
    assert values["RISK_SIGNAL_SERVICE_TIMEOUT_SECONDS"] == "21"
    assert "RISK_ADAPTER_BASE_URL" not in values
    assert "RISK_ADAPTER_TIMEOUT_SECONDS" not in values


def test_sync_env_rejects_conflicting_risk_signal_application_keys(tmp_path, capsys):
    pairs = (
        (
            "RISK_ADAPTER_BASE_URL",
            "RISK_SIGNAL_SERVICE_BASE_URL",
            "http://legacy-risk:9405",
            "http://canonical-risk:9405",
        ),
        (
            "RISK_ADAPTER_TIMEOUT_SECONDS",
            "RISK_SIGNAL_SERVICE_TIMEOUT_SECONDS",
            "15",
            "21",
        ),
    )

    for index, (legacy_key, canonical_key, legacy_value, canonical_value) in enumerate(pairs):
        out = tmp_path / f".env-risk-application-{index}"
        out.write_text(
            "BUILD_PROFILE=compose\n"
            f"{legacy_key}={legacy_value}\n"
            f"{canonical_key}={canonical_value}\n",
            encoding="utf-8",
        )

        rc = setup_env.main(["--sync-env", "--env-file", str(out)])

        assert rc == 2
        assert (
            f"conflicting env keys {legacy_key} and {canonical_key}"
            in capsys.readouterr().err
        )
