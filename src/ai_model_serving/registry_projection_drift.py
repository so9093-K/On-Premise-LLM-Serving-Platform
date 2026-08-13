from __future__ import annotations

from typing import Any

from ai_model_serving.domain import ModelRegistry

def gpu_budget_status(registry: ModelRegistry, gpu_budgets: dict[str, Any]) -> dict[str, Any]:
    """설정된 GPU 총 사용률을 gpu_budgets.yaml의 avoid_above 정책과 비교한다."""
    total = round(
        sum(float(service.config.get("gpu_memory_utilization", 0)) for service in registry.iter_runtime_services()),
        6,
    )
    policy = gpu_budgets["gpu"]["total_gpu_memory_utilization"]
    avoid_above = float(policy.get("avoid_above", 1.0))
    return {
        "total_gpu_memory_utilization": total,
        "avoid_above": avoid_above,
        "over_avoid_threshold": total >= avoid_above,
    }
