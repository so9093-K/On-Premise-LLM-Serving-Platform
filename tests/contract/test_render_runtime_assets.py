"""모델 runtime port가 생성된 운영 산출물까지 전파되는지 검증한다."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ai_model_serving.domain import ModelRegistry  # noqa: E402
from scripts.render_runtime_assets import render_prometheus_yml, render_runtime_targets_block  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── 포트 변경 시 산출물 전파 ──────────────────────────────────────────────────

def test_port_change_propagates_to_prometheus() -> None:
    catalog = _load_yaml(ROOT / "configs/model_catalog.yaml")
    serving = _load_yaml(ROOT / "configs/model_serving.yaml")
    monitoring = _load_yaml(ROOT / "configs/monitoring.yaml")
    services = _load_yaml(ROOT / "configs/services.yaml")["services"]
    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    serving2["models"]["main_llm"]["endpoint"] = "http://main-llm-vllm:9999/v1"
    registry2 = ModelRegistry(catalog, serving2)
    rendered = render_prometheus_yml(registry2, monitoring, services)
    assert "main-llm-vllm:9999" in rendered
    assert "main-llm-vllm:9401" not in rendered


def test_port_change_propagates_to_runtime_targets_block() -> None:
    catalog = _load_yaml(ROOT / "configs/model_catalog.yaml")
    serving = _load_yaml(ROOT / "configs/model_serving.yaml")
    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    serving2["models"]["main_llm"]["endpoint"] = "http://main-llm-vllm:9999/v1"
    registry2 = ModelRegistry(catalog, serving2)
    block = render_runtime_targets_block(registry2)
    assert "9999" in block
    assert "9401" not in block
