from __future__ import annotations

from pathlib import Path

import yaml

from ai_model_serving.platform_state import DEFAULT_PLATFORM_STATE_DIR


_ROOT = Path(__file__).resolve().parents[2]
_STATE_ROOT = str(DEFAULT_PLATFORM_STATE_DIR)
_STATE_BIND = f"../../.runtime/gateway:{_STATE_ROOT}"


def _compose(path: str) -> dict:
    return yaml.safe_load((_ROOT / path).read_text(encoding="utf-8"))


def test_platform_image_declares_canonical_state_root() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert f"PLATFORM_STATE_DIR={_STATE_ROOT}" in dockerfile
    assert f"install -d -o appuser -g appuser {_STATE_ROOT}" in dockerfile


def test_static_and_dynamic_gateway_share_platform_state_contract() -> None:
    static_gateway = _compose("ops/compose/static-main.external-runtime.yaml")["services"]["gateway"]
    dynamic_gateway = _compose("ops/compose/full-stack.private-network.yaml")["services"]["gateway"]

    assert static_gateway["environment"]["PLATFORM_STATE_DIR"] == _STATE_ROOT
    assert dynamic_gateway["environment"]["PLATFORM_STATE_DIR"] == _STATE_ROOT
    assert "GATEWAY_RUNTIME_STATE_PATH" not in static_gateway["environment"]
    assert "GATEWAY_RUNTIME_STATE_PATH" not in dynamic_gateway["environment"]
    assert _STATE_BIND in static_gateway["volumes"]
    assert _STATE_BIND in dynamic_gateway["volumes"]


def test_static_compose_prepares_gateway_state_directory_before_up() -> None:
    wrapper = (_ROOT / "scripts/compose/static_main_compose.sh").read_text(encoding="utf-8")

    assert (
        'ensure_gateway_runtime_dir "$GATEWAY_RUNTIME_DIR_RELPATH" "$PLATFORM_IMAGE_EFFECTIVE"'
        in wrapper
    )
    assert "gateway runtime state directory is not usable" in wrapper
