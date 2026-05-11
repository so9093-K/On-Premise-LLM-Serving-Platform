# 스크립트 안내

이 디렉터리는 운영자가 `make` 명령 뒤에서 실제로 호출하는 실행 스크립트를 모아 둔 곳이다. 설명은 한국어를 기본으로 쓰고, 파일명·환경 변수·API 경로·명령어는 원문을 유지한다.

## 기본 흐름

```bash
HF_TOKEN=hf_xxx make first-run
source .venv/bin/activate
make compose-up
make ready-full
python scripts/validation/runtime_validation.py
make operator-reports
make compose-down
```

로컬에서 Gateway와 Risk Adapter만 확인할 때는 다음 흐름을 사용한다.

```bash
make init-env-local
make start
make ready-local
make status
make stop
```

## 디렉터리 구조

| 디렉터리 | 역할 |
|---|---|
| `auth/` | auth profile plan/apply/status/doctor와 profile sanity check |
| `build/` | bootstrap, image build, package, Python/version checks |
| `ci/` | GitLab CI/CD deploy entrypoint |
| `compose/` | full-stack compose preflight, up, diagnostics, compose validation |
| `config/` | `.env` 생성과 Infisical 동기화 |
| `models/` | model registry CLI, vLLM command rendering, HF/risk image checks |
| `ops/` | start/stop/status/ready/smoke/reset/clean 같은 운영 명령 |
| `reports/` | runtime target, storage path, monitoring, operator status/evidence reports |
| `validation/` | contract validation, release gate, deterministic test runner, live runtime validation |
| `lib/` | shell/python shared helpers |

## 주요 스크립트

| 파일 | 용도 |
|---|---|
| `reports/operator_guide.py` | 상황별 operator workflow guide를 출력한다. `make guide`에서 호출한다. |
| `config/setup_env.py` | `.env`를 생성한다. 기본 target은 기존 `.env`를 덮어쓰지 않는다. `local_open`/`private_network` profile flag를 drift 없이 생성한다. |
| `sync-runtime-secrets` / `config/setup_env.py --sync-runtime-secrets` | `.env`의 `ADMIN_API_KEY`를 `.runtime/prometheus/admin_api_key`로 다시 기록한다. |
| `auth/auth_plan.py` / `auth/auth_apply.py` | secret을 출력하지 않고 auth profile 변경 계획을 보여주거나 managed auth flag만 적용한다. |
| `auth/auth_profile_sanity.py` | `config/setup_env.py`가 생성하는 local/compose auth profile이 `AUTH_MODE` 기대값과 일치하는지 release gate에서 검증한다. |
| `validation/validate_contracts.py` | OpenAPI refs, generated OpenAPI schema injection, JSON Schema, config, release hygiene 정책을 검증한다. |
| `validation/run_tests.py` | 외부 pytest plugin autoload를 끄고 unit/contract test를 실행한다. |
| `build/check_python.py` | 현재 interpreter가 `>=3.12,<3.15`인지 fail-fast로 확인한다. |
| `ops/start_services.sh` | 로컬 app-only Gateway/Risk Adapter를 실행하고 `/health`를 기다린다. |
| `ops/ready_local.sh` | app-only `/health` 상태를 strict하게 확인한다. app service가 내려가 있으면 실패하며 vLLM은 요구하지 않는다. |
| `ops/ready_full.sh` | strict `/ready`와 smoke test를 실행한다. 실제 vLLM runtime이 필요하다. |
| `ops/ready_check.sh` | backward-compatible alias로 `ready_full.sh`를 호출한다. |
| `compose/preflight_compose.sh` | full-stack compose 전 Docker, GPU 표시, host-published port, secret 상태를 점검한다. compose 내부 vLLM port 9401–9404는 host port 검사 대상이 아니며, monitoring host port는 `.env`의 `PROMETHEUS_PORT`, `GRAFANA_PORT`, `DCGM_EXPORTER_PORT`, `CADVISOR_PORT` override를 따른다. |
| `ops/doctor.sh` | Python, 계약, bash syntax, `.env`, local status를 한 번에 진단한다. |
| `validation/runtime_validation.py` | 실제 runtime 검증 결과를 `reports/runtime/` 아래에 기록한다. |
| `reports/runtime_targets_report.py` | ModelRegistry projection에서 runtime target inventory JSON/Markdown을 생성한다. |
| `models/modelctl.py` | model control plane이다. `list`, `status`, `validate`, `diff`는 읽기 전용이고 `propose-add`, `propose-remove`는 파일 쓰기 없는 변경 계획을 출력한다. |
| `reports/storage_paths_report.py` | `configs/storage_paths.yaml`에서 로컬 저장소/cache/report/secret 경로 inventory JSON/Markdown을 생성한다. |
| `reports/monitoring_projection_report.py` | ModelRegistry와 monitoring config에서 Prometheus scrape, recording rule, Grafana variable projection JSON/Markdown을 생성한다. |
| `reports/operator_status_bundle.py` | runtime target, model inventory, GPU budget, monitoring label, readiness vocabulary를 하나의 operator status bundle로 생성한다. |
| `reports/live_evidence_bundle.py` | operator status bundle과 runtime validation report를 sanitised evidence bundle로 결합한다. `--static-placeholder`는 package용으로 timestamped runtime report를 연결하지 않는다. |
| `reports/refresh_generated_reports.py` | package 전 current generated report를 재생성한다. runtime validation timestamp report는 만들지 않고 static live evidence placeholder를 생성한다. |
| `validation/release_check.py` | 서비스 기동 없는 정적 릴리스 gate를 실행한다. 각 step에는 timeout이 있어 hang 시 실패 step을 명확히 표시한다. |
| `models/check_hf_model_config.py` | Docker/GPU 없이 Transformers `AutoConfig`만 로드해 vLLM·bitsandbytes 이전 config loader 문제를 분리한다. |
| `build/package_release.sh` | 배포 ZIP을 만들고 secret, log, cache, egg-info, generated runtime report를 제외한다. `make package`는 이 스크립트 전에 `reports/refresh_generated_reports.py`를 실행한다. ZIP root는 항상 `ai_model_serving_platform/`로 고정한다. |
| `ops/clean_all.sh` | build 산출물, egg-info와 log를 정리한다. 실행 중 local service가 있으면 중단한다. `--dry-run`으로 삭제 대상을 먼저 볼 수 있다. 모델 cache는 `PURGE_MODEL_CACHE=1`, runtime secret은 `PURGE_RUNTIME_SECRETS=1`일 때만 삭제한다. |
| `build/reset_version.py` | VERSION, OpenAPI, pyproject, env 예시, platform image tag를 같은 버전으로 맞춘다. |

## 운영 주의사항

- `make init-env-compose`는 기존 `.env`가 있으면 실패하고 보존한다.
- `.runtime/prometheus/admin_api_key`만 사라졌다면 `.env`를 다시 만들지 말고 `make sync-runtime-secrets`를 실행한다.
- `make start`는 vLLM을 시작하지 않는다. app-only 확인용이다.
- app-only 확인은 `make ready-local`, strict full-stack 확인은 `make ready-full`을 사용한다.
- full-stack 검증은 Docker/GPU/vLLM이 있는 host에서 `make preflight-compose && make compose-up`으로 수행한다.
- 운영 산출물은 개별 명령(`make runtime-targets`, `make storage-paths`, `make monitoring-projection`, `make operator-status`, `make live-evidence`) 또는 통합 명령 `make operator-reports`로 생성한다. 라이브 검증은 `make runtime-validate`, 릴리스 전 정적 게이트는 `make release-check`로 수행한다. 장시간 환경에서는 `RELEASE_CHECK_STEP_TIMEOUT_SECONDS` 또는 `--step-timeout-seconds`로 step timeout을 조정한다.
- 삭제 전에는 `make remove-plan` 또는 `make cleanup-plan`으로 삭제 대상을 확인할 수 있다. 두 명령은 `make clean-dry-run`의 읽기 쉬운 alias다.

- `.runtime/`은 정상적인 로컬 runtime state다. `make init-env-compose` 이후 존재할 수 있으며, `make clean-all`은 기본적으로 보존한다. 테스트와 패키징 정책은 `.runtime`의 로컬 존재가 아니라 release/source ZIP 포함 여부를 검사해야 한다.
- `package_release.sh`는 `.runtime`, `.venv`, `venv`, `env`, `.tox`, logs, run, cache, pycache, egg-info를 제외한다.

## 계약 검증용 표기

아래 원문은 build/runtime UX contract marker다. 한국어 설명은 위 섹션을 기준으로 한다.

- build와 runtime은 다른 단계다
- `make build`는 서비스를 시작하지 않는다
- make start
- make ready
- make ready-local
- make ready-full
- make guide
- make runtime-targets
- make storage-paths
- make monitoring-projection
- make operator-status
- make live-evidence
- make release-check
- make operator-reports
- make cleanup-plan
- make remove-plan
- make build-pipeline
- make first-run
- make rebuild-full


## Full-stack 진단

- `validate_vllm_compose.py`: compose vLLM command와 model serving/catalog/card 정책 정합성을 검증한다. Embedding pooling token budget 오류와 risk detector quantization drift를 사전에 막는다.
- `compose_diagnostics.sh`: `make ready-full` 실패 시 docker compose 상태와 주요 서비스 로그를 수집하고, 알려진 vLLM 장애 패턴을 요약한다.
- `check_hf_model_config.py`: weight load 이전에 발생하는 HF config 문제를 Docker 없이 재현한다. `make hf-config-check`로 기본 Kanana Prompt config를 확인한다.

Risk detector의 `bitsandbytes` 설정은 운영 기본값이다. 원인 분리를 위한 A/B 테스트는 별도 override에서 수행하고, 기본 compose에서 임의 제거하지 않는다.

## Kanana risk vLLM image 점검

- `make first-run` / `make bootstrap`: platform image와 dedicated `RISK_VLLM_IMAGE`를 만들고, image 내부 Kanana config check를 실행한다.
- `make rebuild-risk-vllm` / `make build-risk-vllm-image`: `ops/docker/Dockerfile.risk-vllm-kanana`에서 dedicated `RISK_VLLM_IMAGE`만 빌드하는 고급/수동 target이다.
- `make risk-vllm-config-check`: `RISK_VLLM_IMAGE` 안에서 label, metadata, Kanana risk model config load를 확인한다.
- `SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK=1 make preflight-compose`: image-internal config check만 건너뛴다. production 승격용으로 쓰지 않는다.

## 프로젝트 관리 inventory

```bash
make project-inventory
python scripts/reports/project_inventory_report.py
```

`reports/refactor/project_inventory_current.*`를 생성한다. 이 파일은 handoff와 관리 UX 검토를 위한 현재 파일/문서/ownership inventory다.

## 인증 제어 플레인 점검

`make auth-status`로 현재 public/admin/internal auth 상태를 확인하고, `make auth-doctor`로 위험 조합을 탐지한다. 후보 env 파일은 `make auth-status ENV=<path>`와 `make auth-doctor ENV=<path>`로 root `.env` 반영 전에 점검한다. 변경 전에는 `make auth-plan MODE=strict ENV=<path>`로 plan을 보고, 적용은 `make auth-apply MODE=strict ENV=<path>`로 managed auth flag만 수정한다. secret 값은 출력하지 않는다.

## 모델 제어 플레인

```bash
python scripts/models/modelctl.py list
python scripts/models/modelctl.py status
python scripts/models/modelctl.py validate
python scripts/models/modelctl.py diff
python scripts/models/modelctl.py propose-add --id new-main --role main_llm --upstream-model-id org/model --port 9499 --endpoint /v1/new-main
python scripts/models/modelctl.py propose-remove local-main
```

`modelctl.py`의 `list/status/validate/diff`는 read-only다. `propose-add/propose-remove`도 파일을 쓰지 않고 영향 파일, 차단 조건, GPU budget 경고, 후속 검증 절차만 출력한다. 실제 모델 add/remove는 catalog, serving config, contracts, model cards, schemas, runtime validation, monitoring projection, tests를 함께 바꾸는 리뷰 대상이다.

## Risk vLLM patch 생명주기

`build_risk_vllm_image.sh`는 `ops/patches/` 아래 patch script를 포함한 dedicated Kanana risk image를 빌드한다. `check_risk_vllm_image_config.sh`는 image label, metadata, Kanana config loading을 검증한다. 자세한 내용은 `docs/operations/risk_vllm_patch_lifecycle.md`를 본다.

## OpenAPI snapshot diff

`scripts/validation/openapi_snapshot_diff.py`는 strict auth 기준 generated OpenAPI와 checked-in static OpenAPI의 path/method/security/summary/description/operationId/response status/contract schema drift를 확인한다. `release_check.py`에도 포함되어 있다.

## Risk vLLM patch removal check

`scripts/models/risk_vllm_patch_removal_check.sh`는 risk image 내부 patch 제거 후보 상태를 점검한다. 이미 patch가 적용된 image만으로 제거 가능성을 증명할 수 없으므로 patch 없는 candidate image에서 config canary와 smoke를 별도로 통과해야 한다.
