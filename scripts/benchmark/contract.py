"""성능 계약을 읽는 유일한 경로.

runner·collector·evaluator가 각자 YAML을 열면 계약이 이름을 단독 소유한다는
결정이 무너진다(ADR-0026 12절). 여기서만 읽고 나머지는 반환값을 쓴다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.configuration import load_yaml_mapping  # noqa: E402

PERFORMANCE_DIR = ROOT / "configs" / "performance"
RESULT_SCHEMA_PATH = ROOT / "specs" / "schemas" / "performance_run.schema.json"


@dataclass(frozen=True)
class PerformanceContract:
    version: int
    metrics: dict[str, Any]
    workloads: dict[str, Any]
    slo_classes: dict[str, Any]

    def workload(self, workload_id: str) -> dict[str, Any]:
        try:
            return self.workloads[workload_id]
        except KeyError as exc:
            known = ", ".join(sorted(self.workloads))
            raise KeyError(f"unknown workload {workload_id!r}; contract declares: {known}") from exc

    def slo_class_for(self, workload_id: str) -> tuple[str, dict[str, Any]] | None:
        for name, slo in self.slo_classes.items():
            if slo.get("workload") == workload_id:
                return name, slo
        return None


def load_contract() -> PerformanceContract:
    metrics = load_yaml_mapping(PERFORMANCE_DIR / "metrics.yaml")
    workloads = load_yaml_mapping(PERFORMANCE_DIR / "workloads.yaml")
    slo = load_yaml_mapping(PERFORMANCE_DIR / "slo.yaml")
    return PerformanceContract(
        version=int(metrics["version"]),
        metrics=metrics.get("metrics") or {},
        workloads=workloads.get("workloads") or {},
        slo_classes=slo.get("slo_classes") or {},
    )
