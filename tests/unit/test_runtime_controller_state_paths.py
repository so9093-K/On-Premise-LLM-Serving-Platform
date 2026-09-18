from __future__ import annotations

from pathlib import Path

from ai_model_serving.apps.runtime_controller import load_runtime_controller_config


def test_runtime_controller_state_children_follow_platform_state_root(tmp_path: Path) -> None:
    state_root = tmp_path / "platform-state"
    config = load_runtime_controller_config(
        {
            "APP_CONFIG_ROOT": str(Path(".").resolve()),
            "PLATFORM_STATE_DIR": str(state_root),
            "INTERNAL_SERVICE_AUTH_REQUIRED": "false",
        }
    )

    assert config.state_path == state_root / "main-model-state.json"
    assert config.log_target_manifest_path == (
        state_root / "log-targets" / "docker-containers.json"
    )
