# 변경 이력

이 파일은 사용자와 운영자에게 의미 있는 버전별 릴리스 노트만 기록한다. 긴 내부 유지보수 기록은 `docs/archive/changelog/`에 보존한다.

## [Unreleased]

### Added

- 문서 lifecycle, ownership, source-of-truth, 검증 방식을 추적하는 `docs/manifest.yaml`을 추가했다.

### Changed

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
