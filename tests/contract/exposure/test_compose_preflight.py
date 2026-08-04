"""bootstrap.sh가 compose 기동 전에 auth mode를 올바르게 적용하는지 검증한다."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_bootstrap_does_not_skip_any_named_auth_mode() -> None:
    """bootstrap.sh는 특정 이름을 콕 집어 건너뛰지 않고 모든 named profile을 적용해야 한다."""
    text = (ROOT / "scripts/build/bootstrap.sh").read_text(encoding="utf-8")
    # 어떤 profile 이름도 skip 조건으로 하드코딩되면 안 된다
    # 유일하게 허용되는 skip은 AUTH_MODE=custom(운영자가 직접 관리)뿐이다
    auth_yaml = _load_auth()
    for mode in auth_yaml.get("profiles", {}):
        if mode == "custom":
            continue
        assert f'!= "{mode}"' not in text, (
            f"bootstrap.sh must not skip AUTH_MODE={mode}; "
            "all named profiles (except custom) should be applied explicitly"
        )


def test_bootstrap_applies_named_auth_modes_skips_only_custom() -> None:
    text = (ROOT / "scripts/build/bootstrap.sh").read_text(encoding="utf-8")
    assert '!= "custom"' in text, (
        "bootstrap.sh must skip only AUTH_MODE=custom; all named profiles should be applied"
    )


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


def test_preflight_compose_wrapper_does_not_preload_root_dotenv() -> None:
    text = (ROOT / "scripts/compose/preflight_compose.sh").read_text(encoding="utf-8")
    assert "load_local_env .env" not in text
    assert 'ENV_FILE="${ENV_FILE:-.env}"' in text
    assert 'env ENV_FILE="$ENV_FILE"' in text


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
