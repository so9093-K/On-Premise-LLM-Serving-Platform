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
from ai_model_serving.operator_status import operator_status_bundle_document, write_operator_status_bundle  # noqa: E402
from ai_model_serving.storage_paths import StorageRegistry  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="ModelRegistry 기반 운영 상태 bundle을 생성합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output-dir", default="reports/runtime")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    registry = ModelRegistry(
        load_yaml(root / "configs/model_catalog.yaml"),
        load_yaml(root / "configs/model_serving.yaml"),
    )
    document = operator_status_bundle_document(
        registry=registry,
        monitoring=load_yaml(root / "configs/monitoring.yaml"),
        gpu_budgets=load_yaml(root / "configs/gpu_budgets.yaml"),
        version=(root / "VERSION").read_text(encoding="utf-8").strip(),
        storage_paths=StorageRegistry.from_yaml(root / "configs/storage_paths.yaml").as_report_document(),
    )
    json_path, md_path = write_operator_status_bundle(document, root / args.output_dir)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
