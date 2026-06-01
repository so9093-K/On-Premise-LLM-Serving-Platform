#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.live_evidence import (  # noqa: E402
    latest_runtime_validation_report,
    live_evidence_bundle_document,
    load_json,
    write_live_evidence_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 상태와 runtime validation report를 묶어 live evidence bundle을 생성합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output-dir", default="reports/runtime")
    parser.add_argument("--operator-bundle", default="reports/runtime/operator_status_bundle.json")
    parser.add_argument("--runtime-report", default="", help="Runtime validation JSON report 경로입니다. 기본값은 존재하는 최신 non-config-only reports/runtime/runtime_validation_*.json입니다.")
    parser.add_argument("--latest-any", action="store_true", help="config-only report라도 최신 runtime validation report를 사용합니다.")
    parser.add_argument("--static-placeholder", action="store_true", help="timestamped runtime validation report를 연결하지 않는 패키징용 placeholder bundle을 생성합니다.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output_dir = root / args.output_dir
    operator_bundle_path = (root / args.operator_bundle).resolve()
    if not operator_bundle_path.exists():
        raise SystemExit(f"operator status bundle not found: {operator_bundle_path}. Run make operator-status first.")

    if args.static_placeholder:
        runtime_report_path = None
        runtime_report = None
    else:
        runtime_report_path = (root / args.runtime_report).resolve() if args.runtime_report else latest_runtime_validation_report(root, args.output_dir, prefer_live=not args.latest_any)
        runtime_report = load_json(runtime_report_path) if runtime_report_path and runtime_report_path.exists() else None
    document = live_evidence_bundle_document(
        operator_status=load_json(operator_bundle_path),
        runtime_report=runtime_report,
        runtime_report_path=str(runtime_report_path.relative_to(root)) if runtime_report_path else None,
        version=(root / "VERSION").read_text(encoding="utf-8").strip(),
        is_package_placeholder=args.static_placeholder,
    )
    json_path, md_path = write_live_evidence_bundle(document, output_dir)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
