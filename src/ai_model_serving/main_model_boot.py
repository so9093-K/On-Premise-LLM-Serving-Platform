from __future__ import annotations

from pathlib import Path
from typing import Any

from .main_model_control import (
    gpu_util_override_from_mapping,
    load_main_model_catalog,
    resolve_boot_profile,
)
from .main_model_state import read_active_profile
from .settings_parts.dotenv_parser import load_strict_env_file


def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"environment file not found: {path}")
    return load_strict_env_file(path)


def resolve_compose_relative_path(raw: str, compose_file: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (compose_file.resolve().parent / raw.removeprefix("./")).resolve()


def _strict_bool(value: str, *, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{key} must be true or false, got: {value!r}")


def render_boot_override(
    *,
    catalog_path: Path,
    state_path: Path,
    env_path: Path,
) -> tuple[str, dict[str, Any]]:
    env = read_env_values(env_path)
    catalog = load_main_model_catalog(
        catalog_path,
        gpu_memory_utilization_override=gpu_util_override_from_mapping(env),
        env=env,
    )
    configured = env.get("MAIN_LLM_BOOT_PROFILE") or catalog.default_profile
    locked = _strict_bool(
        env.get("MAIN_LLM_PROFILE_LOCKED", "false"),
        key="MAIN_LLM_PROFILE_LOCKED",
    )
    profile_id = resolve_boot_profile(
        catalog,
        configured_profile=configured,
        locked=locked,
        persisted_profile=read_active_profile(state_path),
    )
    profile = catalog.profiles[profile_id]
    return profile_id, {
        "services": {
            str(catalog.runtime["compose_service"]): {
                # 이 프로필에 대해 resolve된 image로 boot한다 (프로필이 자체
                # runtime을 고정할 수 있다, 예: audio-capable 빌드). loader가
                # profile.image가 digest-pinned이며 비어있지 않음을 보장한다.
                "image": str(profile.image),
                "command": list(profile.command),
            }
        }
    }
