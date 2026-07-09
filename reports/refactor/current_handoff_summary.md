---
document_type: current_snapshot
status: current
audience: operator, release_engineer
note: "이 문서는 특정 시점의 handoff 요약이다. 최신 package version은 VERSION 파일을 기준으로 한다."
---

# 현재 Handoff 요약 — Streaming·Grafana·패키징 재감사 포함

## 현재 추가 사항

- `/v1/models` 응답에 모델별 `request_parameters`를 추가했다.
- `/v1/chat/completions`는 `stream=true`를 공식 request parameter로 허용하고 Gateway SSE fast path로 upstream vLLM chunk를 relay한다.
- `stream_options.include_usage=true`는 `stream=true`와 함께 사용할 때만 허용한다.
- Grafana는 실제 운영 산출물 기준 **2개 dashboard** (`gpu_capacity_and_oom_risk`, `usage_today`)로 구성된다. dashboard JSON은 `ops/grafana/dashboards/*.json`이 source of truth이며, 과거 6개 baseline 표현은 현재 산출물과 맞지 않는 stale 문구다.
- first-run, clean/delete, package flow의 과거 감사 snapshot은 `reports/archive/refactor_audits/first_run_clean_package_audit.md`에 보존한다.
- `local-main`은 chat/sampling/tool 관련 사용자 조정 가능 parameter를 노출한다.
- `local-embed`는 `dimensions`, `encoding_format`, `truncate_prompt_tokens`를 노출한다.
- `risk-prompt`는 사용자가 조정할 수 있는 parameter가 없으므로 `request_parameters: {}`로 노출한다.
- `risk-siren`은 retired 상태이며 `/v1/models` active listing에는 노출하지 않는다. 호환 route는 410 Gone 정책으로 유지한다.
- risk adapter가 detector 호출 시 내부 고정하는 `max_tokens=1`, `temperature=0`은 `fixed_parameters`로 분리해 사용자 입력 form과 구분했다.
- `ModelRegistry`, `specs/schemas/model_list_response.schema.json`, generated OpenAPI, API 문서, endpoint 참조, FastAPI Docs UX 기준을 같은 정책으로 맞췄다.
- `docs/operations/model_parameter_discovery.md`를 추가해 클라이언트 UI와 운영자 변경 절차를 문서화했다.

## 현재 운영자 진입점

| 목적 | 명령/문서 |
|---|---|
| 처음 시작 | `docs/operations/first_project_guide.md`, `docs/operations/day0_quickstart.md`, `make help`, `make guide` |
| full-stack 준비 | `make first-run` / `make bootstrap`, `make compose-up`, `make ready-full` |
| 인증 관리 | `make auth-status`, `make auth-doctor`, `make auth-plan`, `make auth-apply` (`ENV=<path>`로 후보 env 점검 가능) |
| 모델 상태 | `make model-status`, `make model-validate`, `make model-diff`, `make model-propose-add`, `make model-propose-remove` |
| 모델 parameter discovery | `GET /v1/models`, `docs/operations/model_parameter_discovery.md`, `docs/specs/api.md` |
| Streaming Chat API | `POST /v1/chat/completions` with `stream=true`, `docs/operations/streaming_runtime_operations.md` |
| Monitoring/Grafana | `ops/grafana/dashboards/*.json`, `docs/operations/monitoring_ux.md`, `configs/monitoring.yaml` |
| 빌드/제거/재빌드 | `make build-pipeline`, `make first-run`, `make rebuild-full`, `make remove-plan`, `make reset` |
| 운영 산출물 | `make operator-reports`, `make refresh-generated-reports` |
| 릴리스 gate | `make release-check`, `make release-check-full`, `scripts/validation/openapi_snapshot_diff.py` |
| 현 상태 문서 | `docs/operations/project_maintainability_status.md`, `reports/refactor/current_refactor_state.md` |

## 범위

이 handoff는 지금까지 작업한 인증 제어, 비인증 모드, OpenAPI contract, Risk vLLM patch lifecycle, 모델 제어, 모델별 사용자 조정 가능 parameter discovery, streaming API, Grafana 운영 UX, 문서 한국어화, Day-0 빌드/제거/재빌드 UX, release/package hygiene의 사이드이펙트를 재검토한 결과다. 별도 한국어판/영어판을 만들지 않고, 단일 문서 흐름을 한국어 중심으로 유지한다. 파일명, 명령어, API path, 환경 변수, JSON/YAML field, 제품명은 원문을 유지한다.

## 디버깅과 사이드이펙트 검토

| 영역 | 확인 내용 | 판단 |
|---|---|---|
| 인증 UX | status/doctor/plan/apply 출력이 한국어 중심이며 secret을 출력하지 않음. 후보 env도 `ENV=<path>`로 점검 가능 | 유지 |
| 비인증 모드 | `local_open`은 로컬용, non-local 비인증은 doctor가 위험으로 표시 | 유지 |
| OpenAPI | checked-in schema injection + generated error surface + snapshot diff로 FastAPI loose docs 회귀 방지 | 강화됨 |
| `/v1/models` discovery | 모델별 capability와 사용자 조정 가능 `request_parameters`를 함께 노출 | 강화됨 |
| Streaming API | `stream=true` SSE relay, `stream_options.include_usage`, error event/usage metric 정책 | 강화됨 |
| Grafana/Prometheus | 2개 dashboard (`gpu_capacity_and_oom_risk`, `usage_today`), Git-managed provisioning, dashboard JSON source of truth | 강화됨 |
| Risk vLLM patch | vendor patch는 임시 bridge로 문서화, metadata/label/verify/removal-check 유지 | 관리 필요 |
| 모델 관리 | `modelctl`은 read-only + plan-only 상태로 lifecycle/projection/GPU budget/변경 영향 확인 | 안전 |
| generated report | runtime/operator/evidence Markdown이 한국어 중심으로 재생성 | 유지 |
| 패키징 | `make package` 전에 generated report를 static placeholder 기준으로 재생성하고 `.env`, `.runtime`, cache, pycache, dist, timestamp runtime report, dated legacy report 제외 | 강화 |
| 문서 정합성 | active 유지보수 문서가 완료된 작업을 “다음 작업”으로 안내하지 않도록 최신화 | 개선됨 |

## 현재 검증 기준선

```bash
python scripts/validation/validate_contracts.py
python scripts/validation/openapi_snapshot_diff.py
python scripts/validation/runtime_validation.py --config-only
python scripts/compose/validate_vllm_compose.py
python scripts/auth/auth_profile_sanity.py
python scripts/models/modelctl.py validate
python scripts/models/modelctl.py diff
python scripts/validation/run_tests.py -q
python scripts/validation/release_check.py --step-timeout-seconds 60
python -m compileall -q src scripts tests
bash -n scripts/*.sh scripts/lib/*.sh
```

## Target host 후속 검증

아래 항목은 이 정적 handoff 환경이 아니라 Docker/GPU/vLLM target host에서 다시 확인해야 한다.

```bash
make rebuild-risk-vllm
make risk-vllm-config-check
make risk-vllm-patch-removal-check
make compose-up
make ready-full
make runtime-validate
make operator-reports
make release-check-full
```

## 남은 비차단 과제

- 실제 vLLM + proxy 환경에서 `curl -N` streaming smoke와 Grafana 브라우저 렌더링 확인
- OpenAPI snapshot diff 범위를 examples/tags까지 확장
- admin endpoint CIDR allowlist 또는 `ADMIN_AUTH_MODE` 설계
- `modelctl propose-*` 결과를 저장 가능한 plan artifact schema로 확장
- Risk vLLM patch 없는 candidate image에서 Kanana config canary와 실제 vLLM smoke 수행
