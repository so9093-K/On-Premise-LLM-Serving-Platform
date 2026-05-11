#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.project_inventory import find_project_root, write_inventory_reports  # noqa: E402


def main() -> None:
    root = find_project_root()
    outputs = write_inventory_reports(root)
    for path in outputs.values():
        print(f'wrote {path}')


if __name__ == '__main__':
    main()
