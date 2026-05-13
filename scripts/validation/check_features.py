#!/usr/bin/env python3
"""feature-check: features/*.yaml 매니페스트 정합성 점검 (파일 수정 없음).

- referenced source_files, config_files, docs가 실제로 존재하는지 확인한다.
- commands가 Makefile에 선언되어 있는지 확인한다.
- tests 경로가 존재하는지 확인한다 (괄호 설명이 붙은 항목은 파일명만 추출).
- monitoring_dashboards.yaml의 dashboard_count가 ops/grafana/dashboards/*.json 실제 개수와 일치하는지 확인한다.
- dashboard_files 목록이 실제 파일과 일치하는지 확인한다.

make feature-check에서 호출한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML이 필요합니다: pip install pyyaml")

ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = ROOT / "features"
MAKEFILE = ROOT / "Makefile"

# 이 필드의 항목은 파일 존재 여부를 검사한다
FILE_FIELDS = ("source_files", "config_files", "docs", "dashboard_files")
# 이 필드의 항목은 Makefile에 선언 여부를 검사한다
COMMAND_FIELDS = ("commands",)
# tests 항목은 경로 부분만 추출해 존재 여부를 검사한다
TESTS_FIELD = "tests"

# "path/to/file.py (설명)" 형식에서 파일 경로만 추출
_FILE_DESC_RE = re.compile(r"^([^\s(]+)")


def _extract_path(item: str) -> str:
    m = _FILE_DESC_RE.match(item.strip())
    return m.group(1) if m else item.strip()


def _makefile_targets() -> set[str]:
    text = MAKEFILE.read_text(encoding="utf-8")
    return set(re.findall(r"^([a-zA-Z][a-zA-Z0-9_-]*):", text, re.MULTILINE))


def check_manifest(path: Path, makefile_targets: set[str]) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(ROOT)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{rel}: YAML 파싱 오류: {exc}"]

    if not isinstance(data, dict):
        return [f"{rel}: YAML 루트가 dict가 아니다"]

    for field in FILE_FIELDS:
        for item in data.get(field, []) or []:
            fpath = _extract_path(str(item))
            if not (ROOT / fpath).exists():
                errors.append(f"{rel}: {field}: 파일 없음: {fpath!r}")

    for field in COMMAND_FIELDS:
        for item in data.get(field, []) or []:
            cmd = str(item).strip()
            if cmd.startswith("make "):
                target = cmd.split()[1]
                if target not in makefile_targets:
                    errors.append(f"{rel}: commands: make target 없음: {target!r}")

    for item in data.get(TESTS_FIELD, []) or []:
        fpath = _extract_path(str(item))
        if "/" in fpath and not (ROOT / fpath).exists():
            errors.append(f"{rel}: tests: 파일 없음: {fpath!r}")

    # monitoring_dashboards.yaml 전용 검사
    if data.get("id") == "monitoring_dashboards":
        dashboard_dir = ROOT / "ops/grafana/dashboards"
        actual_set = {p.name for p in dashboard_dir.glob("*.json")} if dashboard_dir.exists() else set()
        declared_files: list[str] = [_extract_path(str(f)) for f in data.get("dashboard_files", []) or []]
        declared_set = {Path(f).name for f in declared_files}

        missing = declared_set - actual_set
        extra = actual_set - declared_set
        for name in sorted(missing):
            errors.append(f"{rel}: dashboard_files: 선언됐지만 파일 없음: {name!r}")
        for name in sorted(extra):
            errors.append(f"{rel}: dashboard_files: 파일 있지만 선언 안 됨: {name!r}")

        declared_count = data.get("dashboard_count")
        actual_count = len(actual_set)
        if declared_count != actual_count:
            errors.append(
                f"{rel}: dashboard_count 불일치: "
                f"선언={declared_count}, 실제={actual_count}"
            )

    return errors


def main() -> int:
    if not FEATURES_DIR.exists():
        print("[feature-check] features/ 디렉터리 없음 — 건너뜀")
        return 0

    manifests = sorted(FEATURES_DIR.glob("*.yaml"))
    if not manifests:
        print("[feature-check] features/*.yaml 없음 — 건너뜀")
        return 0

    makefile_targets = _makefile_targets()
    all_errors: list[str] = []
    for manifest in manifests:
        all_errors.extend(check_manifest(manifest, makefile_targets))

    if all_errors:
        print(f"[feature-check] {len(all_errors)}개 오류 (검사 매니페스트: {len(manifests)}개)")
        for e in all_errors:
            print(f"  ✗ {e}")
        return 1

    print(f"[feature-check] 통과: {len(manifests)}개 매니페스트 점검 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
