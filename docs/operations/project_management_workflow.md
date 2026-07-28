# 프로젝트 관리 워크플로

이 문서는 프로젝트 관리자가 **문서, 설정, 운영 산출물, 릴리스 gate**를 어디서 확인하고 어떤 순서로 확인해야 하는지 정리한다.

## 1. 가장 먼저 볼 명령

```bash
make guide
make operator-reports
make validate
```

| 목적 | 명령 | 산출물 |
|---|---|---|
| 상황별 명령 선택 | `make guide` | 터미널 가이드 |
| 운영 report 통합 갱신 | `make operator-reports` | `reports/runtime/*.json`, `reports/runtime/*.md` |
| 실행 전 정적 검증 | `make validate` | contract/projection/evidence 검증 |

## 2. 관리 기준 문서

| 영역 | 기준 문서 |
|---|---|
| 처음 시작 | `docs/operations/day0_quickstart.md` |
| 상황별 운영 흐름 | `docs/operations/operator_workflows.md` |
| 설정·빌드·삭제 lifecycle | `docs/operations/configuration_lifecycle.md` |
| 로컬 저장 경로·모델 캐시 | `docs/operations/storage_paths.md` |
| 문서 관리 정책 | `docs/governance/document_management.md` |
| 리팩토링 현재 상태 | `reports/refactor/current_refactor_state.md` |
| 현재 handoff 요약 | `reports/refactor/current_handoff_summary.md` |
| 릴리스 gate | `docs/release/release_checklist.md` |

## 3. 문서 정합성 UX

문서를 수정한 뒤에는 다음을 실행한다.

```bash
make validate
make test
```

필수 원칙:

- 새 사용자 진입점은 `README.md`, `docs/README.md`, `make help`, `make guide` 중 최소 하나에서 찾을 수 있어야 한다.
- 사용자-facing 명령을 추가하면 `Makefile help`, `scripts/README.md`, 관련 operations 문서를 함께 갱신한다.
- phase별 오래된 중간 report를 active package에 계속 쌓지 않는다. 현재 handoff는 `reports/refactor/current_refactor_state.md`, `reports/refactor/current_handoff_summary.md`를 기준으로 한다.

## 5. 통합 관리 UX

권장 루틴:

```bash
make operator-reports   # runtime/storage/monitoring/status/evidence 상태
make validate           # 실행 전 정적 검증
```

GPU 서버에서 runtime까지 갱신하는 경우:

```bash
make runtime-validate
make operator-reports
make validate
```

`make validate`와 `make test`는 각각 독립 실행할 수 있으며, CI와 릴리스에서는 두 명령을 모두 실행한다.

## 5. 정리 기준

- `reports/runtime/` 전체는 host·GPU·현재 운영 상태에 따라 달라지는 생성 산출물이므로 release package에는 포함하지 않는다. 운영 인수인계가 필요하면 대상 서버에서 별도로 생성·보관한다.
- `reports/refactor/validation/*.log` 같은 과거 중간 validation log는 active handoff source가 아니다. 새 검증 결과는 `current_handoff_summary.md`와 필요한 phase 설계 문서로 압축한다.


## 인증 제어 플레인 점검

`make auth-status`로 public/admin/internal auth의 실제 상태를 확인하고, `make auth-doctor`로 위험하거나 일관되지 않은 flag 조합을 탐지한다. 이 점검은 secret 값을 출력하지 않으며 API 기능을 바꾸지 않는다.
