#!/usr/bin/env python3
"""reports-check: generated report 헤딩·내용·배너·버전 레이블 점검 (파일 수정 없음).

make reports-check에서 호출한다.

추가 검사:
- reports/refactor/current_*.md에서 stale phrase(six dashboards, 198 passed, 0.1.0-rc.1 등)를
  Historical note 없이 현재 기준처럼 사용하는 경우를 감지한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECKS: dict[str, tuple[str, str]] = {
    "reports/runtime/runtime_targets.md": ("# 런타임 대상 인벤토리", "## 운영 해석"),
    "reports/runtime/operator_status_bundle.md": ("# 운영 상태 번들", "## 운영 해석"),
    "reports/runtime/monitoring_projection.md": ("# 모니터링 Projection", "## 운영 해석"),
    "reports/runtime/storage_paths.md": ("# 로컬 저장소 경로 리포트", "## 운영 해석"),
    "reports/runtime/live_evidence_bundle.md": ("# 라이브 증빙 번들", "## 운영 해석"),
    "reports/refactor/project_inventory_current.md": ("# 프로젝트 인벤토리와 파일 검토", "## 관리 해석"),
}

GENERATED_BANNER = "<!-- GENERATED FILE"
AMBIGUOUS_VERSION_PATTERN = re.compile(r"(?m)^버전: `")

# stale phrase 검사: 이 패턴이 없는 줄에 stale phrase가 있으면 오류
# "Historical note", "earlier draft", "감사 당시", "draft target", "retired"가 있으면 면제
STALE_EXEMPT_MARKERS = re.compile(
    r"historical note|earlier draft|감사 당시|draft target|retired|historical observation",
    re.IGNORECASE,
)

STALE_PHRASES: list[tuple[str, re.Pattern[str]]] = [
    ("six dashboards", re.compile(r"\bsix\s+(Grafana\s+)?dashboards?\b", re.IGNORECASE)),
    ("six provisioned dashboards", re.compile(r"\bsix\s+provisioned\s+dashboards?\b", re.IGNORECASE)),
    ("198 passed", re.compile(r"\b198\s+passed\b")),
    ("0.1.0-rc.1 (version ref)", re.compile(r"\b0\.1\.0-rc\.1\b")),
]


def _check_stale_phrases(path: Path) -> list[str]:
    """current_*.md에서 stale phrase를 현재 기준처럼 쓰는 줄을 찾는다."""
    errors: list[str] = []
    rel = path.relative_to(ROOT)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [f"{rel}: 읽기 실패"]

    for line_no, line in enumerate(text.splitlines(), 1):
        if STALE_EXEMPT_MARKERS.search(line):
            continue
        for label, pattern in STALE_PHRASES:
            if pattern.search(line):
                errors.append(
                    f"{rel}:{line_no}: stale phrase ({label!r}) — "
                    "Historical note, earlier draft, 감사 당시 기준 등으로 명확히 표시하거나 현재 기준으로 수정하라"
                )
    return errors


def _check_generated_reports() -> list[str]:
    """generated report 헤딩·내용·배너·버전 레이블 점검."""
    errors: list[str] = []
    for rel, (heading, section) in CHECKS.items():
        p = ROOT / rel
        if not p.exists():
            errors.append(f"MISSING: {rel}")
            continue
        text = p.read_text(encoding="utf-8")
        if heading not in text:
            errors.append(f"{rel}: heading missing: {heading!r}")
        if section not in text:
            errors.append(f"{rel}: section missing: {section!r}")
        if GENERATED_BANNER not in text:
            errors.append(f"{rel}: GENERATED 배너 누락 — 직접 수정 가능한 파일로 오인될 수 있다")
        if AMBIGUOUS_VERSION_PATTERN.search(text):
            errors.append(
                f"{rel}: 모호한 '버전: `...' 레이블 발견 — "
                "'Package version:', 'Config schema version:', 'Schema version:' 중 하나로 명확히 표기하라"
            )
    return errors


def main() -> int:
    # --stale-only: current_*.md stale phrase 점검만 수행 (generated reports 생략)
    stale_only = "--stale-only" in sys.argv[1:]

    errors: list[str] = []

    if not stale_only:
        errors.extend(_check_generated_reports())

    # current_*.md stale phrase 점검
    for p in sorted((ROOT / "reports/refactor").glob("current_*.md")):
        errors.extend(_check_stale_phrases(p))

    if errors:
        print(f"[reports-check] {len(errors)}개 오류")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    checked_current = len(list((ROOT / "reports/refactor").glob("current_*.md")))
    if stale_only:
        print(f"[reports-check] 통과: {checked_current}개 current_*.md stale phrase 점검 완료")
    else:
        print(
            f"[reports-check] 통과: {len(CHECKS)}개 generated report + "
            f"{checked_current}개 current_*.md stale phrase 점검 완료"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
