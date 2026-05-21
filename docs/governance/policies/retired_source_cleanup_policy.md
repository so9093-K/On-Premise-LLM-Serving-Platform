# Retired Source Cleanup Policy / 은퇴 소스 정리 정책

이 정책은 과거 원천 프로젝트, 과거 리뷰 보고서, runtime mock이 릴리스 패키지에 다시 들어오지 않도록 막는다. 정책 이름과 일부 금지어는 contract 호환을 위해 영어 원문을 유지한다.

## 결정 근거

- `D-001`: 과거 원천 프로젝트 코드는 포함하지 않는다.
- `ADR-0001`: origin transition 문서는 별도 파일로 유지하지 않고 결정 기록으로 흡수한다.
- runtime mock은 `src/`에 포함하지 않는다. test double은 `tests/` 안에서만 허용한다.

## 금지 경로 예시

- `reports/source_file_inventory.csv`
- `docs/reviews/source_file_inventory_summary.md`
- `reports/full_project_model_feature_review_2026-05-06.md`
- `reports/project_ux_and_hardening_review_2026-05-06.md`
- `reports/operational_ux_hardening_review_0.1.6_2026-05-06.md`
- `reports/env_image_automation_review_0.1.7_2026-05-06.md`
- `reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md`

## 리팩터링 report cleanup

`reports/refactor/`에는 현재 handoff에 필요한 요약과 matrix만 둔다. phase-by-phase 중간 실행 보고서, 오래된 backlog snapshot, 초기 diff, 중복 file-review matrix, 날짜가 붙은 과거 maintenance/rebaseline report는 active source tree에 유지하지 않는다. 현재 상태는 `reports/refactor/current_refactor_state.md`, `reports/refactor/current_handoff_summary.md`, `reports/refactor/project_inventory_current.md`를 기준으로 한다. 날짜가 붙은 `current_refactor_state_*.md`와 `project_inventory_phase*.*`는 current handoff가 아니라 stale snapshot으로 본다.

`tests/contract/test_release_hygiene_static.py`, `tests/contract/test_release_package_smoke.py`,
`configs/retired_source_cleanup_policy.yaml`, `scripts/validation/validate_contracts.py`가 이 정책을 회귀 방지한다.
