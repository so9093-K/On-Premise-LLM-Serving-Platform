from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .dotenv_parser import load_strict_env_file

DOTENV_VALUES: dict[str, str] = {}
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


def load_dotenv(project_root: Path, env_file: Path | None = None) -> None:
    """Load simple KEY=VALUE pairs as fallback values only."""
    DOTENV_VALUES.clear()
    path = env_file or (project_root / ".env")
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
    if exported_app_env is None or exported_app_env.lower() in {"local", "test", "development"}:
        load_dotenv(project_root)


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
