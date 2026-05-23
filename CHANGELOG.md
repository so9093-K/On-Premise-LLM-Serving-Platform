# 변경 이력

이 파일은 사용자와 운영자에게 의미 있는 버전별 릴리스 노트만 기록한다. 긴 내부 유지보수 기록은 `docs/archive/changelog/`에 보존한다.

## [Unreleased]

### Added

- `make sync-env` — `git pull` 이후 `.env`를 템플릿과 동기화한다. 누락 키를 추가하고 폐기 키를 제거하되, 기존 크리덴셜·이미지 태그·커스텀 값은 모두 보존한다. 시크릿을 재생성하지 않는다.
- `setup_env.py --env-file <path>` — `--sync-env` 실행 시 프로젝트 루트가 아닌 다른 경로의 `.env`를 대상으로 지정할 수 있다. 별도 배포 디렉터리의 `.env` 동기화에 사용한다.
- 문서 lifecycle, ownership, source-of-truth, 검증 방식을 추적하는 `docs/manifest.yaml`을 추가했다.

### Changed

- 공통 error code 계약에 `DETECTOR_DISABLED`, `STREAM_LIMIT_EXCEEDED`를 반영하고, Gateway가 Risk Adapter의 `DETECTOR_DISABLED` 410 envelope를 보존하도록 했다.
- retrieval 내부 embedding 호출이 `truncate_prompt_tokens`를 전달하도록 정리했다. 확인되지 않은 `truncation_side`는 silent no-op 대신 422 validation error로 처리한다.
- non-local `local_open`/`custom`/`internal_trusted` auth profile과 production `SKIP_PREFLIGHT=1` 경로의 운영 hard-fail 조건을 강화했다.
- 운영 배포 동작 변경 없이 retrieval contract의 project root 탐색 의존을 runtime settings에서 분리하고, 계층 import boundary를 AST 계약 테스트로 고정했다.
- `bootstrap.sh`(`make rebuild-full`)이 `EXPOSURE_MODE`와 `EXPOSURE_AUDIENCE`를 기존 `.env`에서 읽어 재초기화 후 복원한다. 이전에는 `AUTH_MODE`만 보존되고 `EXPOSURE_MODE`는 초기화됐다.
- `deploy_gitlab_compose.sh` CI/CD 배포 시 `.env` 이미지 참조 업데이트 직후 `make sync-env`를 호출해 신규 템플릿 키를 서버 `.env`에 자동 반영한다.
- ADR canonical 위치를 `docs/adr/`로 통합하고 root `adr/`는 더 이상 사용하지 않는다.
- 설명형 request examples 문서를 `docs/examples/requests.md`로 이동했다.
- `reports/refactor/current_*`에는 실제 current state, handoff, inventory만 남기고 과거 audit snapshot은 archive로 분리했다.

## [0.0.1] - 2026-05-20

### Added

- Gateway 중심의 chat, embedding, retrieval, risk signal API 계약과 운영 문서 기준선을 제공한다.
- 모델 catalog, model cards, runtime config, OpenAPI/JSON Schema, monitoring projection 검증 흐름을 포함한다.
- Docker/GPU full-stack 운영을 위한 compose, Prometheus, Grafana, runtime validation report 생성 흐름을 제공한다.

### Changed

- CHANGELOG는 짧은 release history로 유지하고, 기존 `0.1.0-rc.1` 내부 maintenance 기록은 `docs/archive/changelog/maintenance_journal_0.1.0-rc.1.md`로 이동했다.

### Security

- 인증/인가 동작은 이 문서 재구조화에서 변경하지 않았다.
