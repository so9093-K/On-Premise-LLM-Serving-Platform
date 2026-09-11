"""테스트 수집 전에 필요한 최소 환경을 한 곳에서 설정한다.

``make test``뿐 아니라 IDE와 직접 ``python -m pytest`` 실행도 같은 테스트
환경을 사용해야 한다. 실제 배포 이미지를 가리키지 않는 digest 형식의 값만 두며,
개별 테스트가 명시한 환경은 덮어쓰지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path


os.environ.setdefault("APP_ENV", "test")
# 스크립트 계층(preflight, auth, compose)은 settings_parts와 별개로 자기
# _env_value가 ENV_FILE(기본 ".env")을 읽는다. 테스트가 저장소 .env를 흡수하지
# 않도록 존재하지 않는 경로를 기본값으로 세운다. ENV_FILE을 직접 지정하는
# 테스트는 그 값이 이긴다.
os.environ.setdefault(
    "ENV_FILE", str(Path(__file__).resolve().parents[1] / ".env.not-a-test-input")
)
os.environ.setdefault(
    "VLLM_IMAGE",
    "registry.example.com/vllm-unified@sha256:"
    "0000000000000000000000000000000000000000000000000000000000000000",
)
os.environ.setdefault(
    "AUDIO_VLLM_IMAGE",
    "registry.example.com/vllm-unified@sha256:"
    "1111111111111111111111111111111111111111111111111111111111111111",
)


# 저장소 .env는 테스트 입력이 아니다.
#
# load_settings()는 APP_ENV가 local/test/development일 때 저장소 .env를 fallback으로
# 읽는다. 이는 source tree 실행의 정상 동작이지만, 위에서 APP_ENV=test를 세우는
# 순간 테스트 스위트까지 그 경로에 들어간다. 그 결과 `make setup`을 한 개발자의
# 머신에서만 테스트가 깨진다 -- 실제로 macOS target의 .env가 DEPLOYMENT_TARGET을
# 덮어써서 risk_prompt 런타임이 사라지고 4개가 실패했다.
#
# 테스트 입력은 테스트가 명시한 것만이어야 한다. 그래서 "저장소 루트의 .env"
# 하나만 보이지 않게 한다. tmp_path 루트를 넘기거나 env_file을 지정한 테스트는
# 그 경로를 그대로 읽으므로 영향받지 않는다.
import pytest

from ai_model_serving.settings_parts import env as _env_module

_REPO_DOTENV = Path(__file__).resolve().parents[1] / ".env"
_real_load_dotenv = _env_module.load_dotenv


def _load_dotenv_without_repo_env(project_root, env_file=None):
    path = Path(env_file) if env_file is not None else Path(project_root) / ".env"
    if path == _REPO_DOTENV:
        _env_module.DOTENV_VALUES.clear()
        return
    _real_load_dotenv(project_root, env_file)


@pytest.fixture(autouse=True)
def _repo_dotenv_is_not_a_test_input(monkeypatch):
    monkeypatch.setattr(_env_module, "load_dotenv", _load_dotenv_without_repo_env)
