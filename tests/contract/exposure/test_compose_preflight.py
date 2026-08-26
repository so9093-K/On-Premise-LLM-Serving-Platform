"""compose 기동 전 preflight 게이트가 위험한 auth/exposure 조합을 막는지 검증한다.

preflight_compose.py는 `make compose-up`이 실제 컨테이너를 띄우기 전에 통과해야
하는 fail-closed 게이트다. 여기서 통과시키면 인증 없이 노출된 스택이 그대로 뜬다.
"""

from __future__ import annotations

import importlib.util
from types import ModuleType

import pytest

from .helpers import ROOT, load_exposure

PREFLIGHT_PATH = ROOT / "scripts/compose/preflight_compose.py"


def load_preflight() -> ModuleType:
    """preflight 스크립트를 매 테스트마다 새 모듈로 적재한다.

    이 스크립트는 import 시점에 환경을 읽으므로, 모듈을 공유하면 앞선 테스트의
    monkeypatch 결과가 뒤 테스트로 새어 들어간다.
    """
    spec = importlib.util.spec_from_file_location("preflight_compose_under_test", PREFLIGHT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# private_network profile만 담은 최소 입력 -- _phase1은 이 프로필의 존재 여부만 본다.
MINIMAL_PRIVATE_NETWORK = {"profiles": {"private_network": {"diagnostics": {}}}}


def test_compose_preflight_rejects_local_open_without_full_stack_private_lan(monkeypatch) -> None:
    module = load_preflight()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local_open")
    monkeypatch.setenv("EXPOSURE_MODE", "private_network")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "")

    with pytest.raises(SystemExit) as exc:
        module._phase1(MINIMAL_PRIVATE_NETWORK)

    assert "auth profile evidence" in str(exc.value)


def test_compose_preflight_allows_non_local_local_open_on_trusted_lan(monkeypatch) -> None:
    module = load_preflight()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local_open")
    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "private_lan")

    module._check_auth_profile_preflight()


def test_compose_preflight_reads_auth_mode_from_env_file(monkeypatch, tmp_path) -> None:
    module = load_preflight()

    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=production\nAUTH_MODE=local_open\n", encoding="utf-8")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("ENV_FILE", str(env_file))

    with pytest.raises(SystemExit):
        module._phase1(MINIMAL_PRIVATE_NETWORK)


def test_compose_preflight_reads_exposure_from_env_file(monkeypatch, tmp_path) -> None:
    module = load_preflight()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\nAUTH_MODE=local_open\nEXPOSURE_MODE=master_open\nEXPOSURE_AUDIENCE=banana\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EXPOSURE_AUDIENCE", raising=False)
    monkeypatch.setenv("ENV_FILE", str(env_file))

    with pytest.raises(SystemExit):
        module._phase1(load_exposure())
