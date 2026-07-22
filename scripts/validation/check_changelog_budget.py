#!/usr/bin/env python3
"""changelog-budget-check: CHANGELOG.md이 하드 캡에 가까워지면 경고한다 (파일 수정 없음, non-blocking).

CHANGELOG.md은 `tests/contract/test_document_source_of_truth.py`의
`test_changelog_latest_release_matches_version_and_stays_release_note`가 90줄 미만을
강제한다. 그 테스트는 캡을 넘으면 바로 실패(hard fail)라서 발견이 항상 "커밋 직전"이 되고,
그 결과 캡에 걸린 변경들이 CHANGELOG 항목 없이 조용히 스킵되는 일이 반복됐다(2026-07-22
발견: 5개 커밋이 기록 없이 지나감). 이 스크립트는 하드 캡보다 먼저(WARN_THRESHOLD) 경고를
띄워, "release를 끊어서 archive로 옮길 때"라는 신호를 개발 중에 미리 준다 — 통과 여부는
막지 않는다(exit 0 고정).

make validate-docs에서 호출한다.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"

# 계약 테스트가 강제하는 하드 캡(< 90)보다 여유를 두고 먼저 경고한다.
WARN_THRESHOLD = 75
HARD_CAP = 90


def main() -> int:
    if not CHANGELOG.exists():
        print("[changelog-budget-check] CHANGELOG.md 없음 — 건너뜀")
        return 0

    line_count = len(CHANGELOG.read_text(encoding="utf-8").splitlines())

    if line_count >= WARN_THRESHOLD:
        print(
            f"[changelog-budget-check] 경고: CHANGELOG.md {line_count}줄 "
            f"(하드 캡 {HARD_CAP}줄까지 {HARD_CAP - line_count}줄 남음). "
            "release를 끊어 [Unreleased] 항목을 버전 헤딩으로 옮기고, "
            "장문 내부 서술은 docs/archive/changelog/로 분리하는 걸 검토하세요 — "
            "안 그러면 다음 변경들이 캡에 막혀 기록 없이 스킵되기 쉽습니다."
        )
        return 0

    print(f"[changelog-budget-check] 통과: CHANGELOG.md {line_count}줄 (경고 기준 {WARN_THRESHOLD}줄)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
