from __future__ import annotations

import csv
import subprocess
from typing import Any

from .config import RuntimeValidationConfig
from .results import CheckResult


def _reserve_hard_minimum(gpu_budgets: dict[str, Any]) -> float:
    reserve_policy = gpu_budgets["gpu"]["reserve_gib"]
    value = reserve_policy.get("hard_minimum", reserve_policy.get("minimum"))
    if value is None:
        raise KeyError("hard_minimum")
    return float(value)


def sample_gpu(config: RuntimeValidationConfig, gpu_budgets: dict[str, Any], name: str) -> CheckResult:
    cmd = [
        "nvidia-smi",
        "--query-gpu=memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    output = subprocess.check_output(cmd, text=True, timeout=config.timeout_seconds).strip()
    row = next(csv.reader([output.splitlines()[0].replace(" W", "")], skipinitialspace=True))
    total_mib = float(row[0])
    used_mib = float(row[1])
    reserve_gib = (total_mib - used_mib) / 1024
    minimum = _reserve_hard_minimum(gpu_budgets)
    ok = reserve_gib >= minimum
    return CheckResult("gpu-capacity", name, "pass" if ok else "fail", details={"total_mib": total_mib, "used_mib": used_mib, "reserve_gib": round(reserve_gib, 2), "minimum_reserve_gib": minimum})
