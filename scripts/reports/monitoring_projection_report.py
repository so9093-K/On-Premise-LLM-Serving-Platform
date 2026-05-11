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
from ai_model_serving.monitoring_projection import monitoring_projection_document, write_monitoring_projection_report  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="ModelRegistry 기반 monitoring projection report를 생성합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output-dir", default="reports/runtime")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    registry = ModelRegistry(
        load_yaml(root / "configs/model_catalog.yaml"),
        load_yaml(root / "configs/model_serving.yaml"),
    )
    document = monitoring_projection_document(
        registry=registry,
        monitoring=load_yaml(root / "configs/monitoring.yaml"),
    )
    json_path, md_path = write_monitoring_projection_report(document, root / args.output_dir)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
