# 문서 관리 정책

## 1. 기본 언어

이 프로젝트의 기본 설명 언어는 한국어다. 한국어 사용자를 위해 별도 `*_KO.md` 문서를 추가하는 방식이 아니라, README와 주요 운영 문서 자체를 한국어 중심으로 작성한다.

## 2. 영어 원문 유지 대상

다음은 번역하지 않는다.

- API path: `/docs`, `/v1/chat/completions`
- 환경 변수: `API_KEYS`, `ADMIN_API_KEY_REQUIRED`
- JSON/YAML field: `model`, `messages`, `risk_code`
- 명령어: `make compose-up`, `docker compose`
- 제품명/기술명: `FastAPI`, `Prometheus`, `Grafana`, `vLLM`

## 3. 중복 문서 금지

동일한 내용을 영어 문서와 한국어 문서로 나눠 병렬 유지하지 않는다. 공개/외부 배포가 필요하면 한국어 본문 아래에 짧은 English summary만 둔다.

## 4. Source-of-Truth 정책

문서는 사실을 새로 정의하지 않는다. 각 정보 유형별 source-of-truth를 아래와 같이 확정한다.

| 정보 유형 | Source-of-Truth |
|---|---|
| 모델 목록 | `configs/model_catalog.yaml`, `model_cards/*.json` |
| 모델 runtime 구성 | `configs/model_serving.yaml` |
| risk code | `configs/risk_taxonomy.yaml` |
| API endpoint | `src/ai_model_serving/api/endpoint_spec.py`, `specs/openapi.gateway.yaml` |
| request/response schema | `specs/schemas/*.json` |
| monitoring target | `configs/monitoring.yaml` |
| compose service | `ops/compose/*.yaml` |
| build target | `Makefile`, `scripts/`, `configs/command_terminology_policy.yaml` |
| architectural decision | `docs/adr/*.md` |
| 문서 lifecycle/owner/verification | `docs/manifest.yaml` |
| release history | `CHANGELOG.md` |
| generated evidence | `reports/runtime/*` |

문서가 위 source-of-truth와 충돌하면 source-of-truth가 우선한다. 문서를 느슨하게 만들어 충돌을 숨기지 않는다. 문서는 모델 목록, endpoint, risk code, runtime 구성, build target을 임의로 정의하지 않는다.

## 5. Examples 정책

설명형 examples 문서는 `docs/examples/`에 둔다. root `examples/`에는 실행 가능한 sample payload나 script만 둔다.

- risk code는 `configs/risk_taxonomy.yaml`에 존재하는 code만 사용한다.
- retired endpoint는 active 검증 예시로 작성하지 않는다.
- expected response는 현재 schema와 route lifecycle을 따른다.
- examples는 `tests/contract/test_document_governance.py`로 검증한다.

## 6. ADR 정책

`docs/adr/`는 canonical decision record다. 새 결정은 `docs/adr/README.md`의 template을 사용한다.

- Status: `Proposed` → `Accepted` 또는 `Rejected`
- 대체 시: 원본은 보존하고 `Superseded by ADR-XXXX`로 표시
- `docs/02_decision_register.md`는 ADR index로 운영한다

## 7. 우선순위

| 정보 | 기준 위치 |
|---|---|
| 시작/실행 | `README.md` |
| 전체 구조 | `docs/00_executive_summary.md`, `docs/06_architecture.md` |
| 운영 실행 | `docs/operations/full_stack_runtime.md`, `docs/operations/first_project_guide.md` |
| API | `docs/specs/api.md`, `specs/openapi/*.yaml` |
| 설정 | `docs/specs/configuration.md`, `configs/*.yaml` |
| 모니터링 | `docs/operations/monitoring_ux.md`, `configs/monitoring.yaml` |
| 빌드/패키징 | `docs/development/build_ux.md` |
| 릴리스 | `docs/release/release_checklist.md`, `docs/release/versioning_policy.md` |
| 결정 | `docs/adr/` (canonical), `docs/02_decision_register.md` (index) |

## 8. Reports와 Archive 정책

`reports/runtime/`은 generated runtime evidence다. generator나 source-of-truth를 고치지 않고 결과 파일만 수동 패치하지 않는다.

`reports/refactor/`에는 현재 상태를 대표하는 `current_refactor_state.md`, `current_handoff_summary.md`, `project_inventory_current.*`만 둔다. 과거 audit/review/snapshot은 `reports/archive/` 또는 `docs/archive/`로 이동한다.

`docs/archive/`와 `reports/archive/`는 historical context다. active guide에서 archive 문서를 현재 운영 기준처럼 링크하지 않는다.

`docs/manifest.yaml`은 사람이 읽는 문서와 stable generated/handoff report의 lifecycle registry다. root entrypoint인 `README.md`, `CHANGELOG.md`도 manifest에 등록한다. timestamp가 붙은 live runtime validation evidence(`reports/runtime/runtime_validation_*.md`)는 release package에서 제외되는 일회성 generated evidence이므로 manifest 개별 등록 대상이 아니며, `scripts/validation/runtime_validation.py`와 package policy가 lifecycle을 관리한다.

## 9. Generated Block 정책

다음 Tier 1 reference 문서는 generated block 후보로 관리한다.

- `docs/operations/endpoint_reference.md` — endpoint matrix (현재 `scripts/generate_endpoint_matrix.py`로 부분 생성)
- `docs/models/model_cards.md` — 모델 목록 표
- `docs/operations/model_parameter_discovery.md` — 모델별 파라미터
- `docs/specs/api.md` — API 요약

Generator 전환 전까지는 `tests/contract/test_document_governance.py`가 source-of-truth와의 일치를 검증한다.

## 10. Change Impact Matrix

변경자는 PR에서 아래 범위를 확인한다. 이 matrix는 PR documentation checklist와 연결된다.

| 변경 유형 | 확인 대상 |
|---|---|
| model change | `configs/model_catalog.yaml`, `configs/model_serving.yaml`, `model_cards/*.json`, `docs/models/model_cards.md`, `docs/operations/model_parameter_discovery.md`, `docs/specs/api.md`, `README.md`, `reports/runtime/*`, governance tests |
| endpoint/schema change | `src/ai_model_serving/api/endpoint_spec.py`, `specs/openapi.gateway.yaml`, `specs/schemas/*.json`, `docs/operations/endpoint_reference.md`, `docs/specs/api.md`, `docs/examples/requests.md`, `examples/requests/*.json`, governance tests |
| risk taxonomy change | `configs/risk_taxonomy.yaml`, `docs/specs/risk_signal_contract.md`, `docs/examples/requests.md`, risk schemas, governance tests |
| runtime/port/compose change | `configs/ports.yaml`, `configs/model_serving.yaml`, `ops/compose/*.yaml`, `.env.*.example`, `docs/operations/full_stack_runtime.md`, `docs/operations/day0_quickstart.md`, `reports/runtime/*`, governance tests |
| monitoring/Grafana change | `configs/monitoring.yaml`, `ops/prometheus/**`, `ops/grafana/**`, `docs/operations/monitoring_ux.md`, `docs/operations/status_board_ux.md`, `reports/runtime/monitoring_projection.*`, governance tests |
| CI/CD change | `.gitlab-ci.yml`, `.github/pull_request_template.md`, `Makefile`, `scripts/validation/*`, `docs/operations/gitlab_cicd_deployment.md`, `docs/development/build_ux.md`, governance tests |
| architecture decision change | `docs/adr/*.md`, `docs/adr/README.md`, `docs/02_decision_register.md`, `docs/governance/document_management.md` |
| release/version change | `VERSION`, `pyproject.toml`, `version_manifest.json`, `CHANGELOG.md`, `docs/release/versioning_policy.md`, `docs/release/release_checklist.md`, package scripts |
| examples change | `docs/examples/requests.md`, `examples/requests/*.json`, `specs/schemas/*.json`, `configs/risk_taxonomy.yaml`, governance tests |
| docs structure change | `docs/README.md`, `docs/START_HERE.md`, `docs/governance/document_management.md`, `docs/manifest.yaml`, `.github/pull_request_template.md`, governance tests |
