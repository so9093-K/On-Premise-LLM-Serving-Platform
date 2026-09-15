from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


DEFAULT_PLATFORM_STATE_DIR = Path("/var/lib/ai-model-serving")


def configured_platform_state_root(
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the explicitly configured persistent platform-state root.

    Local source-tree runs intentionally get ``None`` unless they opt in with
    ``PLATFORM_STATE_DIR``. Container/deployment callers that need the image default
    can pass ``DEFAULT_PLATFORM_STATE_DIR`` as a fallback at their boundary.
    """

    source = os.environ if environment is None else environment
    value = source.get("PLATFORM_STATE_DIR", "").strip()
    return Path(value) if value else None


def platform_state_path(
    *parts: str,
    environment: Mapping[str, str] | None = None,
    default_root: Path | None = None,
) -> Path | None:
    root = configured_platform_state_root(environment) or default_root
    return root.joinpath(*parts) if root is not None else None


def gateway_runtime_state_path(
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    source = os.environ if environment is None else environment
    explicit = source.get("GATEWAY_RUNTIME_STATE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    return platform_state_path("runtime-state.json", environment=source)


def runtime_transition_history_path(
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    source = os.environ if environment is None else environment
    runtime_state = gateway_runtime_state_path(source)
    if runtime_state is not None:
        return runtime_state.parent / "runtime-history"
    return platform_state_path("runtime-history", environment=source)


def operator_configuration_state_path(
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    return platform_state_path(
        "config",
        "operator-overrides.yaml",
        environment=environment,
    )
