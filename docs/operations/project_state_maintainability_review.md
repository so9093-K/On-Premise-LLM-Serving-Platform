# 프로젝트 현 상태와 유지보수성 검토

이 문서는 현재 패키지를 사람이 운영·유지보수할 수 있는지 **기능 UX 흐름, 문서 정합성, 레거시 제거 상태** 관점에서 정리한 active 상태 문서다. 실제 운영 판단은 이 문서와 `reports/refactor/current_refactor_state.md`, `reports/refactor/current_handoff_summary.md`를 기준으로 한다.

## 현재 상태 요약

| 영역 | 상태 | 판단 |
|---|---|---|
| 처음 프로젝트 진입 | 개선됨 | `docs/operations/first_project_guide.md`, `make help`, `make guide`로 Day-0 흐름을 따라갈 수 있음 |
| 빌드/제거/재빌드 | 개선됨 | `make build-pipeline`, `make first-run`, `make rebuild-full`, `make remove-plan`, `make reset`으로 목적별 진입점 정리 |
| 인증 제어 | 개선됨 | `auth-status`/`auth-doctor`/`auth-plan`/`auth-apply`와 `ENV=<path>` 후보 env 진단으로 사람이 profile 단위 관리 가능 |
| 비인증 모드 | 개선됨 | `local_open`은 로컬 개발용으로 정리되고, non-local 비인증은 doctor가 위험으로 표시 |
| OpenAPI | 개선됨 | checked-in schema injection, generated error surface, snapshot diff release gate로 FastAPI loose schema 회귀 위험 축소 |
| Risk vLLM patch | 관리 가능하지만 장기 위험 | metadata/label/verify/removal-check는 있으나 vendor patch이므로 제거 조건 추적 필요 |
| 모델 관리 | read-only + plan-only | `modelctl list/status/validate/diff`와 `propose-add/propose-remove`로 상태와 변경 영향 파악 가능, write-mode apply는 아직 보류 |
| 모델 파라미터 discovery | 완료 | `/v1/models`가 모델별 `capabilities`, 사용자 조정 가능 `request_parameters`, risk `fixed_parameters`를 노출 |
| 문서 | 한국어 중심 단일 흐름 | 별도 한국어판/영어판을 나누지 않고, 명령어/API path/env var/schema field만 원문 identifier 유지 |
| 릴리스 위생 | 양호 | `.env`, `.runtime`, cache, pycache, dist, timestamp runtime validation report, 오래된 dated report 제외 정책 유지 |

## 기능 UX 흐름

### 1. 처음 실행

```bash
make help
make guide
make init-env-local
make start
make ready-local
```

로컬 app-only 확인은 vLLM이 없어도 가능해야 한다. `/ready`가 아닌 `/health` 기준으로 먼저 확인하고, 실제 모델 runtime은 full-stack에서 검증한다. 처음 프로젝트를 받은 운영자는 `docs/operations/first_project_guide.md`를 기준으로 app-only와 full-stack 경로를 선택한다.

### 2. 빌드·제거·재빌드

```bash
make build-pipeline
make first-run
make rebuild-full
make rebuild-app
make rebuild-risk-vllm
make remove-plan
make reset
```

`make build-pipeline`은 서비스를 기동하지 않는 통합 파이프라인 빌드다. `make first-run`과 `make rebuild-full`은 `make bootstrap`의 읽기 쉬운 alias다. `make remove-plan`은 삭제 대상 미리 보기이며, 실제 통합 제거는 `make reset`으로 수행한다.

### 3. 인증 확인과 변경

```bash
make auth-status
make auth-doctor
make auth-plan MODE=strict
make auth-apply MODE=strict
```

운영자는 개별 env flag를 직접 외우지 않고 profile을 선택한다. 후보 env는 root `.env`로 반영하기 전에 다음처럼 점검한다.

```bash
make auth-status ENV=/tmp/candidate.env
make auth-doctor ENV=/tmp/candidate.env
make auth-plan MODE=strict ENV=/tmp/candidate.env
```

`auth-apply`는 `--yes` 없이는 dry-run으로 종료하며 파일을 변경하지 않는다.

### 4. 모델 상태와 변경 계획 확인

사용자-facing 클라이언트는 모델별 설정 UI를 하드코딩하지 말고 `/v1/models`의 `request_parameters`를 읽어 구성한다. 운영자는 정책 변경 시 `configs/model_serving.yaml`, request schema, ModelRegistry projection, 문서를 함께 맞춘다. 자세한 기준은 `docs/operations/model_parameter_discovery.md`를 본다.


```bash
make model-status
make model-validate
make model-diff
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm
make model-propose-remove ID=local-main
```

모델 추가/제거는 아직 자동 apply 기능을 제공하지 않는다. 현재는 registry, contract, schema, runtime matrix, monitoring projection, GPU budget 영향 범위를 plan-only로 확인하는 단계다.

### 5. full-stack 운영 증빙

```bash
make compose-up
make ready-full
make runtime-validate
make operator-reports
make release-check-full
```

Docker/GPU/vLLM이 필요한 live 검증과 서비스 기동 없는 static release gate를 혼동하지 않아야 한다. timestamped runtime validation report는 운영 서버 증빙이며 release/source handoff ZIP에는 포함하지 않는다.

## 오류·레거시 식별 결과

- 날짜가 붙은 `current_refactor_state_*.md`와 `project_inventory_phase*.*`는 active handoff에 남기지 않는다.
- `reports/refactor/phase*_summary_*`, validation summary snapshot, documentation consistency snapshot, removed-legacy marker report는 active source tree에서 제거된 상태를 유지한다.
- 과거 version rebaseline 보고서 `reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md`는 active source tree에서 제거했다. 해당 결정은 현재 `CHANGELOG.md`, `VERSION`, `version_manifest.json`, `docs/release/versioning_policy.md`가 담당한다.
- `src/ai_model_serving/validation.py`, `scripts/validation/runtime_validation.py`, `make ready`, `make build`, `make bootstrap` 같은 compatibility facade는 legacy debris가 아니라 하위 호환 entrypoint다. 제거하려면 별도 migration plan과 deprecation window가 필요하다.
- `.runtime/`은 로컬 secret/runtime state이며 release package에는 포함하지 않는다. 로컬 존재 자체를 오류로 보지 않는다.

## 유지보수 관점의 남은 위험

1. Risk vLLM vendor patch는 upstream 변경에 취약하다. `make risk-vllm-patch-removal-check`와 patch 없는 candidate image smoke로 제거 조건을 계속 추적해야 한다.
2. 모델 add/remove는 아직 write-mode가 아니며, 실제 파일 변경 자동화 전에는 proposal plan과 reviewer checklist를 먼저 고정해야 한다.
3. admin endpoint의 network-only 보호는 app-level CIDR enforcement가 아직 없다.
4. OpenAPI snapshot diff는 release gate에 포함되었지만, examples/tags까지 더 넓히면 문서 drift를 더 빨리 잡을 수 있다.
5. generated runtime report는 명령 재실행 시 갱신되므로 source 문서와 generated evidence의 역할을 계속 분리해야 한다.

## 다음 권장 작업

- `modelctl propose-add/propose-remove` 결과를 파일로 저장하는 plan artifact schema를 추가한다.
- OpenAPI snapshot diff 범위를 examples/tags까지 확장한다.
- admin endpoint CIDR allowlist 또는 `ADMIN_AUTH_MODE`를 설계한다.
- Risk vLLM patch 없는 candidate image에서 Kanana config canary와 실제 vLLM smoke를 수행해 제거 가능성을 확인한다.
