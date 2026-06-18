from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .main_model_control import (
    MainModelStateError,
    load_main_model_catalog,
    resolve_boot_profile,
)
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


def read_persisted_active_profile(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MainModelStateError(f"main model state is corrupt: {path}") from exc
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise MainModelStateError("unsupported main model state schema")
    active = state.get("active_profile")
    if active is not None and not isinstance(active, str):
        raise MainModelStateError("active_profile must be a string or null")
    return active


def render_boot_override(
    *,
    catalog_path: Path,
    state_path: Path,
    env_path: Path,
) -> tuple[str, dict[str, Any]]:
    catalog = load_main_model_catalog(catalog_path)
    env = read_env_values(env_path)
    configured = env.get("MAIN_LLM_BOOT_PROFILE") or catalog.default_profile
    locked = _strict_bool(
        env.get("MAIN_LLM_PROFILE_LOCKED", "false"),
        key="MAIN_LLM_PROFILE_LOCKED",
    )
    profile_id = resolve_boot_profile(
        catalog,
        configured_profile=configured,
        locked=locked,
        persisted_profile=read_persisted_active_profile(state_path),
    )
    profile = catalog.profiles[profile_id]
    return profile_id, {
        "services": {
            str(catalog.runtime["compose_service"]): {
                "image": str(catalog.runtime["image"]),
                "command": list(profile.command),
            }
        }
    }
