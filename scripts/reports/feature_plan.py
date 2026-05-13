#!/usr/bin/env python3
"""feature-plan: 기능 변경 시 갱신해야 할 문서, 테스트, 설정, generated output을 출력한다.

make feature-plan ID=security_profiles
make feature-plan ID=monitoring_dashboards
make feature-plan --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc

ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = ROOT / "features"


def list_features() -> int:
    yamls = sorted(FEATURES_DIR.glob("*.yaml"))
    if not yamls:
        print("features/ 디렉터리에 manifest가 없습니다.")
        return 1
    print("## 등록된 feature manifest")
    print()
    for f in yamls:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        print(f"  {data.get('id', f.stem):<30} — {data.get('title', '')}")
    print()
    print("상세 계획: make feature-plan ID=<id>")
    return 0


def show_feature_plan(feature_id: str) -> int:
    candidates = list(FEATURES_DIR.glob(f"{feature_id}.yaml"))
    if not candidates:
        candidates = list(FEATURES_DIR.glob("*.yaml"))
        candidates = [c for c in candidates if yaml.safe_load(c.read_text(encoding="utf-8")).get("id") == feature_id]
    if not candidates:
        print(f"오류: feature manifest '{feature_id}'를 찾을 수 없습니다.", file=sys.stderr)
        print(f"  features/ 디렉터리의 YAML 파일 목록: {[f.name for f in FEATURES_DIR.glob('*.yaml')]}", file=sys.stderr)
        return 1

    data = yaml.safe_load(candidates[0].read_text(encoding="utf-8"))
    _id = data.get("id", feature_id)
    title = data.get("title", "")
    owner = data.get("owner", "")
    audience = ", ".join(data.get("audience", []))
    description = data.get("description", "").strip()

    print(f"# Feature Plan: {_id}")
    print()
    print(f"**{title}**")
    if description:
        print()
        print(description)
    print()
    print(f"- Owner: {owner}")
    print(f"- Audience: {audience}")
    print()

    def _section(label: str, items: list | dict | str | None) -> None:
        if not items:
            return
        print(f"## {label}")
        print()
        if isinstance(items, list):
            for item in items:
                print(f"  - {item}")
        elif isinstance(items, dict):
            for k, v in items.items():
                if isinstance(v, dict):
                    print(f"  - **{k}**")
                    for kk, vv in v.items():
                        print(f"    - {kk}: {vv}")
                else:
                    print(f"  - {k}: {v}")
        else:
            print(f"  {items}")
        print()

    _section("변경 시 함께 갱신할 소스 파일", data.get("source_files"))
    _section("변경 시 함께 갱신할 설정 파일", data.get("config_files"))
    _section("관련 문서", data.get("docs"))
    _section("관련 명령", data.get("commands"))
    _section("관련 테스트", data.get("tests"))
    _section("Validation gate", data.get("validation"))
    _section("생성되는 output", data.get("generated_outputs"))
    _section("Package 영향", data.get("package_impact") or data.get("package_include"))
    _section("Release checklist 영향", data.get("release_checklist_impact"))

    notes = data.get("notes")
    if notes:
        print("## 주의사항")
        print()
        for note in notes:
            print(f"  ⚠️  {note}")
        print()

    dangerous = data.get("dangerous_combinations")
    if dangerous:
        print("## 위험 조합")
        print()
        for combo in dangerous:
            severity = combo.get("severity", "WARN")
            msg = combo.get("message", "")
            print(f"  [{severity}] {msg}")
            conditions = combo.get("conditions", [])
            for cond in conditions:
                print(f"    - {cond}")
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="기능 변경 시 갱신해야 할 파일/테스트/명령을 출력합니다. (maintainer용)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "사용법:",
        nargs="?",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--list", action="store_true", help="등록된 feature manifest 목록 출력")
    parser.add_argument("--id", dest="feature_id", metavar="ID", help="출력할 feature ID")
    args = parser.parse_args()

    if args.list:
        return list_features()
    if args.feature_id:
        return show_feature_plan(args.feature_id)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
