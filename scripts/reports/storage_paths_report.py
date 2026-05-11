#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.storage_paths import StorageRegistry, write_storage_paths_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 storage/cache/report 경로 인벤토리 report를 생성합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output-dir", default="reports/runtime")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    registry = StorageRegistry.from_yaml(root / "configs/storage_paths.yaml")
    json_path, md_path = write_storage_paths_report(registry, root / args.output_dir)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
