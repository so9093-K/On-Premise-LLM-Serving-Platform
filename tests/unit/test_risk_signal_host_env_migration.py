from __future__ import annotations

from scripts.config import setup_env


def test_sync_env_migrates_risk_signal_host_exposure_keys_without_value_loss(tmp_path):
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "RISK_ADAPTER_HOST=127.0.0.2\n"
        "RISK_ADAPTER_PORT=9505\n"
        "RISK_ADAPTER_BIND_ADDR=192.168.10.20\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert values["RISK_SIGNAL_SERVICE_HOST"] == "127.0.0.2"
    assert values["RISK_SIGNAL_SERVICE_PORT"] == "9505"
    assert values["RISK_SIGNAL_SERVICE_BIND_ADDR"] == "192.168.10.20"
    assert "RISK_ADAPTER_HOST" not in values
    assert "RISK_ADAPTER_PORT" not in values
    assert "RISK_ADAPTER_BIND_ADDR" not in values


def test_sync_env_rejects_conflicting_risk_signal_host_exposure_keys(tmp_path, capsys):
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "RISK_SIGNAL_SERVICE_PORT=9405\n"
        "RISK_ADAPTER_PORT=9505\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 2
    assert (
        "conflicting env keys RISK_ADAPTER_PORT and RISK_SIGNAL_SERVICE_PORT"
        in capsys.readouterr().err
    )
