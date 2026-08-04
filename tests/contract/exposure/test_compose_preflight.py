"""bootstrap.sh가 compose 기동 전에 auth mode를 올바르게 적용하는지 검증한다."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_compose_preflight_rejects_local_open_without_full_stack_private_lan(
    monkeypatch,
) -> None:
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("preflight_compose", ROOT / "scripts/compose/preflight_compose.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local_open")
    monkeypatch.setenv("EXPOSURE_MODE", "private_network")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "")
    with pytest.raises(SystemExit) as exc:
        module._phase1({"profiles": {"private_network": {"diagnostics": {}}}})

    assert "auth profile evidence" in str(exc.value)


def test_compose_preflight_allows_non_local_local_open_on_trusted_lan(
    monkeypatch,
) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "preflight_compose_trusted_lan",
        ROOT / "scripts/compose/preflight_compose.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local_open")
    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "private_lan")

    module._check_auth_profile_preflight()


def test_compose_preflight_reads_auth_mode_from_env_file(monkeypatch, tmp_path) -> None:
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("preflight_compose_env_file", ROOT / "scripts/compose/preflight_compose.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=production\nAUTH_MODE=local_open\n", encoding="utf-8")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("ENV_FILE", str(env_file))
    with pytest.raises(SystemExit):
        module._phase1({"profiles": {"private_network": {"diagnostics": {}}}})


def test_compose_preflight_reads_exposure_from_env_file(monkeypatch, tmp_path) -> None:
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("preflight_compose_exposure_env_file", ROOT / "scripts/compose/preflight_compose.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\nAUTH_MODE=local_open\nEXPOSURE_MODE=master_open\nEXPOSURE_AUDIENCE=banana\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EXPOSURE_AUDIENCE", raising=False)
    monkeypatch.setenv("ENV_FILE", str(env_file))

    with pytest.raises(SystemExit):
        module._phase1(_load_exposure())
