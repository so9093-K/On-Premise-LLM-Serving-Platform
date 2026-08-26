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
from scripts.render_runtime_assets import render_prometheus_yml  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── 포트 변경 시 산출물 전파 ──────────────────────────────────────────────────

def test_port_change_propagates_to_prometheus() -> None:
    catalog = _load_yaml(ROOT / "configs/model_catalog.yaml")
    serving = _load_yaml(ROOT / "configs/model_serving.yaml")
    monitoring = _load_yaml(ROOT / "configs/monitoring.yaml")
    services = _load_yaml(ROOT / "configs/services.yaml")["services"]
    # 원래 포트는 config에서 읽는다. 예전엔 9401을 그대로 적어뒀는데, 배포 포트가
    # 바뀌면 "옛 포트가 사라졌다"는 단언이 조용히 무의미해진다(항상 통과).
    original_port = serving["models"]["main_llm"]["port"]
    assert original_port != 9999, "테스트가 쓰는 대체 포트가 실제 포트와 겹친다"

    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    serving2["models"]["main_llm"]["endpoint"] = "http://main-llm-vllm:9999/v1"
    registry2 = ModelRegistry(catalog, serving2)
    rendered = render_prometheus_yml(registry2, monitoring, services)
    assert "main-llm-vllm:9999" in rendered
    assert f"main-llm-vllm:{original_port}" not in rendered
