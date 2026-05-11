#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REPORT_COMMANDS = [
    ["scripts/reports/runtime_targets_report.py"],
    ["scripts/reports/storage_paths_report.py"],
    ["scripts/reports/project_inventory_report.py"],
    ["scripts/reports/monitoring_projection_report.py"],
    ["scripts/reports/operator_status_bundle.py"],
    ["scripts/reports/live_evidence_bundle.py", "--static-placeholder"],
]


def main() -> int:
    python = sys.executable
    for command in REPORT_COMMANDS:
        print(f"[refresh-generated-reports] {' '.join(command)}")
        subprocess.run([python, *command], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
