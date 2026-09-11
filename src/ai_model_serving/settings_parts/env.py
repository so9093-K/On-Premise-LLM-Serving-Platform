from __future__ import annotations

import os
from pathlib import Path

from .dotenv_parser import load_strict_env_file

DOTENV_VALUES: dict[str, str] = {}
# "이 APP_ENV는 개발자 source tree 실행인가"의 단일 기준.
# 저장소 .env를 fallback으로 읽을지, 그리고 운영 secret 검증을 강제할지가
# 모두 이 집합으로 갈린다. 같은 리터럴을 여러 모듈이 복제하면 한쪽만 바뀌어
# "로컬로는 취급하지만 secret 검증은 하는" 모순 상태가 조용히 생긴다.
LOCAL_ENVIRONMENTS = {"local", "test", "development"}
DEFAULT_SECRET_VALUES = {
    "",
    "change-me",
    "change-me-internal",
    "replace-me",
    "todo",
    "example",
    "__generate__",
    "generate-with-make-init-env",
}


def is_default_secret(value: str) -> bool:
    return value.strip().lower() in DEFAULT_SECRET_VALUES


def as_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env(name: str, default: str) -> str:
    if name in os.environ:
        return os.environ[name]
    return DOTENV_VALUES.get(name, default)


# 이 프로젝트의 기본 env 파일. 형식 파싱은 dotenv_parser가, "어느 파일인가"는
# 여기가 소유한다. 예전에는 settings, auth_control, compose preflight가 각자
# ``project_root / ".env"``를 계산해서, 그 사실을 한 번 바꾸려면 세 곳을 찾아야 했다.
# 실제로 테스트가 저장소 .env를 흡수하는 문제를 고칠 때 한 곳을 놓쳐 한 번 더 고쳤다.
DEFAULT_ENV_FILENAME = ".env"


def default_env_path(project_root: Path) -> Path:
    """명시된 파일이 없을 때 사용할 env 파일 경로."""
    return project_root / DEFAULT_ENV_FILENAME


def resolve_env_file(value: str | Path | None, project_root: Path) -> Path:
    """지정된 env 파일을 절대 경로로 바꾼다. 비어 있으면 기본 파일.

    ``ENV_FILE``처럼 상대 경로가 들어올 수 있는 호출부가 이 해석을 각자
    적지 않게 한다.
    """
    if not value:
        return default_env_path(project_root)
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def load_dotenv(project_root: Path, env_file: Path | None = None) -> None:
    """Load simple KEY=VALUE pairs as fallback values only."""
    DOTENV_VALUES.clear()
    path = env_file or default_env_path(project_root)
    if not path.exists():
        return
    DOTENV_VALUES.update(load_strict_env_file(path))


def load_local_dotenv_when_allowed(project_root: Path, env_file: Path | str | None) -> None:
    """Apply explicit env_file or local .env policy for load_settings()."""
    DOTENV_VALUES.clear()
    if env_file is not None:
        env_path = Path(env_file)
        if not env_path.is_absolute():
            env_path = project_root / env_path
        load_dotenv(project_root, env_path)
        return

    exported_app_env = os.getenv("APP_ENV")
    if exported_app_env is None or exported_app_env.lower() in LOCAL_ENVIRONMENTS:
        load_dotenv(project_root)


def as_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(env(name, str(default)))
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    return value


def as_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    value = float(env(name, str(default)))
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    return value
