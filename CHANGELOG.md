# 변경 이력

## 0.1.0-rc.1 - 2026-05-10 (모델별 사용자 조정 가능 파라미터 discovery)

- `/v1/models` 응답에 모델별 `request_parameters`를 추가해 클라이언트가 문서나 OpenAPI schema를 별도로 파싱하지 않고 UI를 구성할 수 있게 했습니다.
- `local-main`은 chat/sampling/tool 관련 parameter, `local-embed`는 embedding dimension/truncation 관련 parameter를 노출합니다.
- `risk-prompt`, `risk-siren`은 사용자 조정 가능 parameter가 없으므로 `request_parameters: {}`로 노출하고, adapter가 내부 고정하는 `max_tokens=1`, `temperature=0`은 `fixed_parameters`로 분리했습니다.
- `ModelRegistry`, `specs/schemas/model_list_response.schema.json`, generated OpenAPI, API 문서, endpoint 참조 문서를 같은 기준으로 정렬했습니다.
- API path, 기존 request schema 의미, model id, compose service topology, Risk vLLM runtime/patch 동작, model add/remove write-mode는 변경하지 않았습니다.

## 0.1.0-rc.1 - 2026-05-10 (레거시/문서/운영 UX 정합성 재점검)

- Phase 29 결과물을 다시 점검해 active report 영역에 남아 있던 과거 version rebaseline 보고서 `reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md`를 제거했습니다.
- 제거된 파일이 `reports/refactor/project_inventory_current.*` 안에 stale entry로 남아 있던 generated report drift를 재생성으로 제거하고 contract test를 추가했습니다.
- `runtime_validation/config.py`의 host URL 해석 순서를 `CLI 인자 > process env/.env > 기본값`으로 명확히 정리했습니다.
- `preflight_compose.sh`가 monitoring port `9410~9413`을 하드코딩하지 않고 `PROMETHEUS_PORT`, `GRAFANA_PORT`, `DCGM_EXPORTER_PORT`, `CADVISOR_PORT` override를 따라 검사하도록 수정했습니다.
- `make package`가 `make refresh-generated-reports`를 먼저 실행해 current generated report를 static live evidence placeholder 기준으로 재생성한 뒤 ZIP을 만들게 했습니다.
- admin/metrics/docs 노출 정책을 `docs/operations/admin_metrics_docs_exposure_policy.md`로 분리하고, runtime validation URL/env 우선순위를 `docs/operations/runtime_validation_config_policy.md`에 문서화했습니다.
- API path, request/response schema, model id, compose service topology, Risk vLLM patch 동작, model add/remove write-mode는 변경하지 않았습니다.

## 0.1.0-rc.1 - 2026-05-10 (Day-0 빌드/제거/재빌드 UX 정리)

- Phase 28 결과물을 다시 점검하고, 처음 프로젝트를 받은 운영자 관점의 전체 가이드 `docs/operations/first_project_guide.md`를 추가했습니다.
- `make build-pipeline`, `make first-run`, `make rebuild-full`, `make rebuild-app`, `make rebuild-risk-vllm`, `make remove-plan` alias를 추가해 통합 빌드·전체 재빌드·부분 재빌드·삭제 미리 보기 UX를 더 직관적으로 만들었습니다. 기존 `make build`, `make bootstrap`, `make build-image`, `make build-risk-vllm-image`, `make cleanup-plan`은 하위 호환으로 유지합니다.
- `README.md`, `docs/README.md`, `docs/build/build_ux.md`, `docs/operations/day0_quickstart.md`, `docs/operations/operator_workflows.md`, `docs/operations/configuration_lifecycle.md`, `scripts/README.md`를 새 alias와 Day-0 운영 흐름에 맞춰 최신화했습니다.
- governance validation과 contract test가 새 Day-0 guide 및 alias help 문구를 확인하도록 보강했습니다.
- API path, request/response schema, model id, compose service topology, Risk vLLM patch 동작, model add/remove write-mode는 변경하지 않았습니다.

## 0.1.0-rc.1 - 2026-05-10 (Auth/OpenAPI/Risk patch 운영 UX 강화)

- Phase 26 결과물을 다시 점검해 운영자가 후보 env를 root `.env`로 옮기기 전에 진단할 수 있도록 `auth-status --env <path>`와 `auth-doctor --env <path>`를 추가했습니다.
- `load_settings(..., env_file=...)`를 지원해 명시 env 파일을 process environment와 분리해 읽도록 했습니다. secret 값은 출력하지 않습니다.
- FastAPI generated OpenAPI에 표준 error response surface를 주입해 static OpenAPI보다 약한 200/422 중심 문서로 회귀하지 않게 했습니다.
- `scripts/validation/openapi_snapshot_diff.py`를 추가하고 `release_check.py`에 포함해 strict auth 기준 static/generated OpenAPI drift를 검사합니다.
- `make risk-vllm-patch-removal-check`를 추가해 Risk vLLM vendor patch 제거 후보 상태를 명시적으로 점검합니다. 이미 patch가 적용된 image만으로 제거 가능성을 증명하지 못한다는 제한도 출력합니다.

## 0.1.0-rc.1 - 2026-05-10 (OpenAPI/env/help 한국어 중심 마감)

- Phase 25 ZIP을 새 프로젝트처럼 다시 풀어 전체 파일, 기능 UX, 문서, release hygiene을 재검토했습니다.
- static OpenAPI YAML, Risk Adapter generated OpenAPI description, `.env.*.example` 주석, setup/auth CLI help, `version_manifest.json`에 남아 있던 영어 중심 운영자 문구를 한국어 중심으로 정리했습니다.
- `auth-apply`는 `--yes` 없이 실행할 때 실패가 아니라 성공적인 dry-run으로 종료하도록 바꿨습니다. env 파일은 변경하지 않습니다.
- `.env`가 없는 상태의 `auth-status`에는 기본 설정값으로 표시 중이라는 안내와 env 초기화 명령을 추가했습니다.
- 한국어 중심 hygiene contract test가 OpenAPI spec, env example, auth/setup CLI, version manifest까지 검사하도록 확장했습니다.

## 0.1.0-rc.1 - 2026-05-10 (기능 UX 재감사와 한국어 출력 패치)

- Phase 24 결과물을 기준으로 전체 파일·문서·CLI·generated report를 다시 감사했습니다.
- `auth-status`, `auth-doctor`, `auth-plan`, `auth-apply`의 사람이 읽는 출력과 경고 문구를 한국어 중심으로 정리했습니다.
- `modelctl list/status/validate` 출력 label을 한국어 중심으로 바꿔 운영자가 현재 모델 lifecycle/projection/GPU budget 상태를 더 쉽게 파악하도록 했습니다.
- runtime validation Markdown과 live evidence Markdown renderer를 한국어 중심으로 수정했습니다.
- command terminology policy와 governance/test oracle이 오래된 영어 marker에 고정되지 않도록 한국어 필수 문구로 정렬했습니다.
- status board, FastAPI docs UX, deployment/operator/project management 문서에 남아 있던 영어 설명 문장을 정리했습니다.

## 0.1.0-rc.1 - 2026-05-10 (한국어 중심 문서/UX 유지보수 패치)

- 운영 문서와 handoff report를 한국어 중심으로 정리했습니다. 별도 한국어판/영어판을 병렬 유지하지 않고, 한국어 본문 안에 명령어/API path/환경 변수 원문만 보존합니다.
- `auth_control_plane`, `risk_vllm_patch_lifecycle`, `project_state_maintainability_review`를 운영자 UX 흐름 중심으로 업데이트했습니다.
- runtime/operator generated Markdown report renderer를 한국어 중심으로 수정해 `make operator-reports` 재실행 후에도 영어 중심 report로 되돌아가지 않게 했습니다.
- 과거 phase 문서를 설계 이력으로 정리하고, 현재 기준 문서는 stable handoff/current-state 문서로 모았습니다.
- 한국어 중심 문서 hygiene contract test를 추가했습니다.


## 0.1.0-rc.1 - 2026-05-10 (프로젝트 전체 감사 hardening patch)

### 재감사·패키징 정합성

- `scripts/validation/run_tests.py`가 pytest 완료 표시 후 wrapper에서 멈춘 것처럼 보일 수 있는 현상을 재현하고, pytest를 in-process로 실행하도록 수정했습니다.
- release package가 timestamped `reports/runtime/runtime_validation_*` 파일은 제외하면서도 `live_evidence_bundle.*`가 제외된 runtime report를 가리킬 수 있는 사이드이펙트를 수정했습니다. 이제 패키징 stage에서 live evidence bundle을 static placeholder로 다시 렌더링합니다.
- dated `current_refactor_state_*.md`, `project_inventory_phase*.*`, phase summary handoff report 참조가 active tree에 재유입되지 않도록 cleanup policy와 governance validation을 강화했습니다.

## 0.1.0-rc.1 - 2026-05-10 (프로젝트 전체 감사 patch)

### 운영 제어 UX

- 인증 profile 생성값, `auth-status`/`auth-doctor`, `auth-plan`/`auth-apply`, generated OpenAPI security 표시를 같은 control-plane 기준으로 정렬했습니다.
- 모델 관리는 read-only `modelctl list/status/validate/diff`를 기준으로 lifecycle/exposure/projection drift를 확인하도록 정리했습니다.

### 문서·레거시 정리

- 현재 handoff entrypoint를 stable path인 `reports/refactor/current_refactor_state.md`, `reports/refactor/current_handoff_summary.md`, `reports/refactor/project_inventory_current.*`로 통일했습니다.
- active `reports/refactor/`에서 오래된 phase별 중간 summary/validation/report snapshot을 제거하고, 설계 이력은 `docs/refactor/phase*.md`에 보존했습니다.
- retired-source cleanup policy와 governance validation이 stale current-state path와 phase report snapshot 재유입을 막도록 강화했습니다.

### 디버깅·릴리스 gate

- `release_check.py`에 step timeout 옵션을 추가하고, auth/model/operator report 후반부는 in-process 실행으로 정리해 subprocess hang side effect를 줄였습니다.
- timestamped `reports/runtime/runtime_validation_*` 산출물은 generated evidence로 유지하며 release/source handoff package에서는 제외합니다.

## 0.1.0-rc.1 - 2026-05-07 (patch)

### 레거시 정리·문서 최신화

- Phase 13 이후 남은 문서 drift를 정리했습니다. Infisical 설정은 별도 전용 env 파일 없이 `make init-env-compose`가 생성하는 `.env`와 `.env.compose.example`을 기준으로 설명합니다.
- `make help`에 `live-evidence`, `release-check`, `release-check-full` operator/release workflow를 명시했습니다.
- 중복 governance required-file 항목과 오래된 refactor 중간 보고서를 정리하고, 최신 cleanup/report 산출물을 기준으로 릴리스 handoff를 단순화했습니다.

### 버그 수정

- **`truncate_prompt_tokens=0` 허용 오류 수정** (`validation.py`): `0`이 유효한 값으로 통과되던 조건문 오류를 수정했습니다. 허용 값은 `-1` 또는 `1..max_tokens`만 인정합니다.
- **`encoding_format: base64` 요청 수락 → 502 오류 수정** (`validation.py`, `embedding_request.schema.json`): 요청에서 `base64`를 허용하더라도 `validate_embedding_response`는 `list[float]`만 받아 502가 발생했습니다. 요청 레이어에서 `base64`를 즉시 거부(`422`)하도록 수정했고, JSON Schema도 `const: "float"`으로 동기화했습니다.

### 계약·문서 변경

- **`api_contract_matrix.csv` `admin_auth` 컬럼 추가**: `/ready`, `/metrics` 엔드포인트의 조건부 admin 인증(`conditional(ADMIN_API_KEY_REQUIRED)`)을 명시하는 컬럼을 추가했습니다. `validate_contracts.py`와 `test_runtime_policy.py`도 함께 업데이트했습니다.

### 환경·빌드 수정

- **Makefile `python` 명령 자동 탐지**: `PYTHON ?= $(shell command -v python3.12 || command -v python3 || command -v python)` + `export PYTHON_BIN`을 추가해 시스템에 `python` 명령이 없어도 `python3.12` → `python3` 순으로 자동 선택합니다.
- **`build_all.sh`, `package_release.sh` `PYTHON_BIN` 미사용 수정**: 두 스크립트가 `python`을 하드코딩하고 있어 Makefile에서 `PYTHON_BIN`을 export해도 적용되지 않던 문제를 수정했습니다.


### Kanana risk runtime 보강

- **risk 전용 vLLM image 분리**: `RISK_VLLM_IMAGE` 기본값을 `ai-model-serving-risk-vllm-kanana:0.1.0-rc.1`로 분리하고, `ops/docker/Dockerfile.risk-vllm-kanana`를 추가했습니다. main Gemma4 runtime에는 영향을 주지 않으면서 Kanana Llama config 호환성 pin(`transformers==4.52.4`)을 적용합니다.
- **컨테이너 내부 HF config preflight 추가**: `make build-risk-vllm-image`, `make risk-vllm-config-check`, `scripts/models/check_risk_vllm_image_config.sh`를 추가해 host venv가 아니라 실제 `RISK_VLLM_IMAGE` 내부에서 Prompt 2.1B와 Siren 8B config 로딩을 확인합니다.
- **Prompt 2.1B shape fact 기록**: `risk-prompt`의 `hidden_size=1792`, `num_attention_heads=24`, `head_dim=128`, `requires_runtime_head_dim_support=true`와 Siren 8B 비교 shape를 catalog/model card/test에 기록했습니다.
- **runtime validation 누락 문서 복구**: `harness/runtime_validation_plan.md`, `docs/operations/runtime_validation_workplan.md`를 추가하고, 정적 runtime validation 문서가 release ZIP에서 빠지지 않도록 package 제외 규칙을 수정했습니다.

### 문서 개선

- **README "어디서 시작하나요?" 내비게이션 테이블 추가**: 6가지 상황별 시작 문서와 핵심 명령을 한눈에 확인할 수 있는 표를 추가했습니다.
- **`make ready` vs `make ready-local` 구분 경고 추가**: `make init-env-compose` 사용 후 `make ready`가 `HTTP 000`으로 실패하는 compose hostname 충돌 원인과 `make ready-local` 사용 안내를 README §5와 `day0_quickstart.md` §1에 추가했습니다.
- **`python3.12` 명시**: README와 `day0_quickstart.md` 전체에서 `python` → `python3.12`로 명령을 정정했습니다.
- **HF token 설정 위치 안내 보강**: `google/embeddinggemma-300m` Gemma 라이선스 동의 필요 여부, `HF_TOKEN`과 `HUGGING_FACE_HUB_TOKEN` 동일 값 설정, preflight 동작까지 상세 설명을 추가했습니다.

---

## 0.1.0-rc.1 - 2026-05-06

운영 전 기준선 후보입니다. `0.1.8`~`0.1.16`에서 진행한 내부 안정화 작업을 하나의 release-candidate 기준선으로 통합했습니다.

### 포함된 주요 내용

- Gateway와 Risk Adapter의 local app-only 실행 흐름 정리
- FastAPI Swagger UI `/docs`, ReDoc `/redoc`, OpenAPI JSON `/openapi.json` 기본 활성화
- Prometheus `9410`, Grafana `9411`, DCGM exporter `9412` 기본 활성화
- Gateway `/ready`가 Risk Adapter `/ready` 호출 시 admin bearer token 전달
- `.env` 비파괴 초기화와 `make sync-runtime-secrets` 추가
- `make compose-up` preflight와 Prometheus bearer-token file 복구
- `PYTHON_BIN` 기반 smoke test 실행
- static/live OpenAPI admin auth 및 401 응답 metadata 정합성 보강
- runtime Docker dependency와 contract/test dependency 분리
- 한국어 중심 운영 문서 정리
- 레거시 review/report 산출물 제거

### 알려진 제한

- Docker/GPU/vLLM full-stack 실측은 target GPU host에서 별도 수행해야 합니다.
- full-stack 실측 통과 전에는 정식 `0.1.0`이 아니라 `0.1.0-rc.1`로 유지합니다.

<details>
<summary>운영 전 내부 안정화 이력</summary>

- `0.1.8`: admin-key 생성, Prometheus bearer-token-file, HF token 전달, telemetry compose 보강
- `0.1.9`: 레거시 report 제거, compose/platform image metadata 정렬, startup/cleanup UX 개선
- `0.1.10`: `.env` fallback/settings isolation 수정
- `0.1.11`: FastAPI Docs 기본 활성화, 모니터링 기본 활성화, Grafana provisioning 추가
- `0.1.12`: 한국어 중심 문서 전환
- `0.1.13`: Gateway readiness admin auth 전달, 비파괴 init-env, 모니터링 flag 정리
- `0.1.14`: 문서 범위 확장, runtime secret resync, reset-version image tag 보강
- `0.1.15`: 과한 한국어 테스트 제거, OpenAPI auth UX 정렬, runtime lock 분리
- `0.1.16`: compose-up preflight, smoke `PYTHON_BIN`, live OpenAPI metadata, config version semantics 보강

</details>
