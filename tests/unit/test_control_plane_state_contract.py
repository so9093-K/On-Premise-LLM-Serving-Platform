from __future__ import annotations

from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]
_STATE_ROOT = "/var/lib/ai-model-serving"
_STATE_BIND = "../../.runtime/gateway:/var/lib/ai-model-serving"


def _compose(path: str) -> dict:
    return yaml.safe_load((_ROOT / path).read_text(encoding="utf-8"))


def test_platform_image_declares_canonical_state_root() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert f"PLATFORM_STATE_DIR={_STATE_ROOT}" in dockerfile
    assert f"install -d -o appuser -g appuser {_STATE_ROOT}" in dockerfile


def test_static_gateway_persists_platform_state_like_dynamic_gateway() -> None:
    static_gateway = _compose("ops/compose/static-main.external-runtime.yaml")["services"]["gateway"]
    dynamic_gateway = _compose("ops/compose/full-stack.private-network.yaml")["services"]["gateway"]

    assert static_gateway["environment"]["PLATFORM_STATE_DIR"] == _STATE_ROOT
    assert static_gateway["environment"]["GATEWAY_RUNTIME_STATE_PATH"] == (
        f"{_STATE_ROOT}/runtime-state.json"
    )
    assert _STATE_BIND in static_gateway["volumes"]
    assert _STATE_BIND in dynamic_gateway["volumes"]


def test_static_compose_prepares_gateway_state_directory_before_up() -> None:
    wrapper = (_ROOT / "scripts/compose/static_main_compose.sh").read_text(encoding="utf-8")

    assert (
        'ensure_gateway_runtime_dir "$GATEWAY_RUNTIME_DIR_RELPATH" "$PLATFORM_IMAGE_EFFECTIVE"'
        in wrapper
    )
    assert "gateway runtime state directory is not usable" in wrapper
