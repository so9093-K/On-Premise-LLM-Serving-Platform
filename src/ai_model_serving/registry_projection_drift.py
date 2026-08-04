from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_model_serving.domain import ModelRegistry


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def registry_projected_artifacts(root: Path, registry: ModelRegistry) -> list[tuple[str, Any, Any]]:
    """(상대 경로, 파일의 실제 내용, registry가 기대하는 값) 목록을 반환한다.

    governance_validation, scripts/render_runtime_assets.py --check,
    scripts/models/modelctl.py가 각자 이 비교를 따로 구현하다가 서로 어긋난
    이력이 있어(2026-08-03 정리) 여기 하나로 모았다. prometheus.yml은
    monitoring/services 인자가 추가로 필요해 여기 포함하지 않는다 --
    필요하면 monitoring_projection.py를 직접 쓴다.
    """
    checks: list[tuple[str, Any, Any]] = [
        (
            "specs/schemas/model_list_response.schema.json",
            _read_json(root / "specs/schemas/model_list_response.schema.json"),
            registry.model_list_schema_document(),
        ),
    ]
    return checks


def registry_artifact_diffs(root: Path, registry: ModelRegistry) -> list[dict[str, str]]:
    """각 generated artifact에 대해 {"path": ..., "status": "ok"|"diff"}를 반환한다."""
    return [
        {"path": path, "status": "ok" if actual == expected else "diff"}
        for path, actual, expected in registry_projected_artifacts(root, registry)
    ]


def gpu_budget_status(registry: ModelRegistry, gpu_budgets: dict[str, Any]) -> dict[str, Any]:
    """설정된 GPU 총 사용률을 gpu_budgets.yaml의 avoid_above 정책과 비교한다."""
    total = round(
        sum(float(service.config.get("gpu_memory_utilization", 0)) for service in registry.iter_runtime_services()),
        6,
    )
    policy = gpu_budgets["gpu"]["total_gpu_memory_utilization"]
    avoid_above = float(policy.get("avoid_above", 1.0))
    return {
        "profile": gpu_budgets["gpu"].get("default_profile"),
        "total_gpu_memory_utilization": total,
        "recommended_start": policy.get("recommended_start"),
        "avoid_above": avoid_above,
        "over_avoid_threshold": total >= avoid_above,
    }
