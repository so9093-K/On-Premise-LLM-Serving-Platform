#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.domain import ModelRegistry  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="설정된 모델 인벤토리, API surface, 리소스 제어값을 출력합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--format", choices=["table", "yaml"], default="table")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    catalog = load_yaml(root / "configs/model_catalog.yaml")
    serving = load_yaml(root / "configs/model_serving.yaml")
    gpu_budgets = load_yaml(root / "configs/gpu_budgets.yaml")

    registry = ModelRegistry(catalog, serving)
    rows = [row.as_dict() for row in registry.inventory_rows()]
    total_gpu_utilization = round(sum(float(row["gpu_memory_utilization"] or 0) for row in rows), 6)
    gpu_policy = gpu_budgets["gpu"]["total_gpu_memory_utilization"]
    summary = {
        "profile": gpu_budgets["gpu"].get("default_profile"),
        "total_gpu_memory_utilization": total_gpu_utilization,
        "recommended_start": gpu_policy.get("recommended_start"),
        "avoid_above": gpu_policy.get("avoid_above"),
    }
    if args.format == "yaml":
        print(yaml.safe_dump({"summary": summary, "models": rows}, sort_keys=False, allow_unicode=True), end="")
        return 0
    headers = ["id", "role", "lifecycle_state", "exposure", "port", "endpoint", "input_modalities", "max_images", "max_image_bytes", "max_image_pixels", "max_model_len", "max_output_tokens", "max_concurrency", "gpu_memory_utilization"]
    widths = {h: max(len(h), *(len(str(row[h])) for row in rows)) for h in headers}
    print(" | ".join(h.ljust(widths[h]) for h in headers))
    print("-+-".join("-" * widths[h] for h in headers))
    for row in rows:
        print(" | ".join(str(row[h]).ljust(widths[h]) for h in headers))
    print()
    print(f"profile: {summary['profile']}")
    print(f"total_gpu_memory_utilization: {summary['total_gpu_memory_utilization']} / avoid_above {summary['avoid_above']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
