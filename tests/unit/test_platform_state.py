from __future__ import annotations

from pathlib import Path

from ai_model_serving.platform_state import (
    DEFAULT_PLATFORM_STATE_DIR,
    configured_platform_state_root,
    gateway_runtime_state_path,
    operator_configuration_state_path,
    platform_state_path,
)


def test_local_run_has_no_persistent_root_without_opt_in() -> None:
    environment: dict[str, str] = {}

    assert configured_platform_state_root(environment) is None
    assert platform_state_path("runtime-state.json", environment=environment) is None
    assert operator_configuration_state_path(environment) is None


def test_platform_state_children_derive_from_one_configured_root(tmp_path: Path) -> None:
    environment = {"PLATFORM_STATE_DIR": str(tmp_path)}

    assert configured_platform_state_root(environment) == tmp_path
    assert gateway_runtime_state_path(environment) == tmp_path / "runtime-state.json"
    assert operator_configuration_state_path(environment) == (
        tmp_path / "config" / "operator-overrides.yaml"
    )


def test_gateway_runtime_state_path_keeps_explicit_legacy_override(tmp_path: Path) -> None:
    explicit = tmp_path / "legacy-runtime-state.json"
    environment = {
        "PLATFORM_STATE_DIR": str(tmp_path / "platform"),
        "GATEWAY_RUNTIME_STATE_PATH": str(explicit),
    }

    assert gateway_runtime_state_path(environment) == explicit


def test_container_default_root_is_explicit_boundary_fallback() -> None:
    environment: dict[str, str] = {}

    assert platform_state_path(
        "main-model-state.json",
        environment=environment,
        default_root=DEFAULT_PLATFORM_STATE_DIR,
    ) == DEFAULT_PLATFORM_STATE_DIR / "main-model-state.json"
