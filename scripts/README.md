# 스크립트 안내

이 디렉터리는 운영자가 `make` 명령 뒤에서 실제로 호출하는 실행 스크립트를 모아 둔 곳이다. 설명은 한국어를 기본으로 쓰고, 파일명·환경 변수·API 경로·명령어는 원문을 유지한다.

> 어디서 시작할지 모르면 `docs/README.md`를 먼저 본다.

## 기본 흐름

```bash
HF_TOKEN=hf_xxx make first-run
source .venv/bin/activate
make compose-up
make ready-full
python scripts/validation/runtime_validation.py
make compose-down
```

로컬에서 Gateway와 Risk Adapter만 확인할 때는 다음 흐름을 사용한다.

```bash
make setup-dev
make validate
make test
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
| `config/` | `.env` 생성 |
| `models/` | model registry CLI, vLLM command rendering, HF/unified image checks |
| `ops/` | start/stop/status/ready/smoke/reset/clean 같은 운영 명령 |
| `validation/` | contract validation, static validation, deterministic test runner, live runtime validation |
| `validation/governance/` | 정적 계약 검증 체크 구현. 프로덕션 패키지(`src/`)가 아니라 여기 사는 이유는 서비스 실행에 필요 없고 런타임 이미지에 실릴 이유도 없기 때문이다. |
| `validation/runtime/` | live runtime 검증 체크 구현. 살아있는 스택을 밖에서 찔러보는 도구라 서비스 자신이 품지 않는다. |
| `lib/` | shell/python shared helpers |

## 주요 스크립트

| 파일 | 용도 |
|---|---|
| `build/setup_dev.py` | macOS/Ubuntu 개발용 `.venv`를 준비·재사용한다. `.env`, runtime state, Docker/GPU는 변경하지 않는다. |
| `build/check_dev_environment.py` | Python 정책과 운영 shell helper에 필요한 Bash 4 이상을 진단한다. |
| `build/refresh_dependency_locks.sh` | Ubuntu x86_64에서 `.python-version`·Dockerfile의 digest 고정 Python resolver로 runtime/contract lock을 재생성하고 새 venv 설치까지 확인한다. |
| `config/setup_env.py` | `.env`를 생성한다. 기본 target은 기존 `.env`를 덮어쓰지 않는다. `local_open`은 `master_open/private_lan` 전체-stack 사내망 정책과 함께 생성한다. |
| `auth/auth_plan.py` / `auth/auth_apply.py` | secret을 출력하지 않고 auth profile 변경 계획을 보여주거나 managed auth flag만 적용한다. |
| `validation/validate_contracts.py` | OpenAPI refs, generated OpenAPI schema injection, JSON Schema, config, release hygiene 정책을 검증한다. |
| `validation/run_test.sh` | Python 버전과 test 환경을 고정하고 unit/contract test를 실행한다. |
| `lib/version_refs.py` | `VERSION` 문자열이 박혀 있는 모든 자리를 한 번만 선언한다. `build/reset_version.py`(생성)와 `validation/governance/versioning.py`(검증)가 같은 표를 읽으므로 두 목록이 갈라질 수 없다. |
| `lib/gateway_runtime_state.sh` | 배포와 `compose-up`이 Gateway에 초기 Runtime 지시를 전달하고 상태 디렉터리 소유권을 준비하는 공통 규칙을 제공한다. `runtime-state.json` 자체는 쓰지 않는다. |
| `compose/compose_service_diff.py` | 두 Release의 렌더된 Compose 정의를 비교해 실제로 변경된 서비스만 출력한다. Release 절대경로 차이는 제거한다. |
| `build/reset_version.py` | 프로젝트 버전을 `lib/version_refs.py`가 선언한 모든 자리에 한 번에 반영한다. 선언된 자리가 파일에서 사라졌으면 조용히 넘기지 않고 실패한다. |
| `build/check_python.py` | 현재 interpreter가 `>=3.12,<3.15`인지 fail-fast로 확인한다. |
| `ops/up_services.sh` | 로컬 app-only Gateway/Risk Adapter를 실행하고 `/health`를 기다린다. |
| `ops/ready_local.sh` | app-only `/health` 상태를 strict하게 확인한다. app service가 내려가 있으면 실패하며 vLLM은 요구하지 않는다. |
| `ops/ready_full.sh` | strict `/ready`와 smoke test를 실행한다. 실제 vLLM runtime이 필요하다. |
| `compose/preflight_compose.sh` | full-stack compose 전 exposure config를 먼저 검증하고, 통과한 뒤 Docker, GPU 표시, effective compose host-published port, secret 상태를 점검한다. compose 내부 `expose` ports는 host port 검사 대상이 아니다. host bind와 port는 `docker compose config` 결과를 따른다. |
| `validation/runtime_validation.py` | 실제 runtime 검증 결과를 `reports/runtime/` 아래에 기록한다. |
| `models/check_hf_model_config.py` | Docker/GPU 없이 Transformers `AutoConfig`만 로드해 vLLM·bitsandbytes 이전 config loader 문제를 분리한다. |
| `build/package_release.sh` | 배포 ZIP을 만들고 secret, log, cache, egg-info, generated runtime report를 제외한다. ZIP root는 항상 `ai_model_serving_platform/`로 고정한다. |
| `ops/clean_all.sh` | build/test 산출물과 runtime report를 정리한다. 실행 중 local service가 있으면 중단한다. `--dry-run`으로 실제 삭제 대상을 먼저 볼 수 있다. `--all`은 로그도 정리하며, 모델 cache는 `PURGE_MODEL_CACHE=1`, runtime secret은 `PURGE_RUNTIME_SECRETS=1`일 때만 삭제한다. |
| `build/reset_version.py` | VERSION, OpenAPI, pyproject, env 예시, platform image tag를 같은 버전으로 맞춘다. |

## 운영 주의사항

- `make init-env-compose`는 기존 `.env`가 있으면 실패하고 보존한다.
- `.runtime/prometheus/admin_api_key`만 사라졌거나 손상되었다면 `.env`를 다시 만들지 말고 `make compose-up`을 실행한다. Compose 기동 전 이 파일을 자동 복구한다. 이 파일은 Prometheus Compose secret source이므로 일반 파일이어야 하며, non-root Prometheus image가 읽을 수 있도록 `0644` 권한으로 생성된다.
- `make start`는 vLLM을 시작하지 않는다. app-only 확인용이다.
- app-only 확인은 `make ready-local`, strict full-stack 확인은 `make ready-full`을 사용한다.
- full-stack 기동인 `make compose-up`에는 Docker/GPU/포트/secret preflight가 포함된다.
- `make compose-up`은 `configs/deploy_profiles.yaml`의 기본 `retrieval_ready`를 적용해 Prompt Risk 모델을 시작하지 않는다. 다른 정의된 초기 구성이 필요하면 `RUNTIME_PROFILE=main_only make compose-up`처럼 명시한다.
- 라이브 검증은 `make runtime-validate`, 실행 전 정적 검증은 `make validate`로 수행한다.
- 삭제 전에는 `make clean-dry-run`으로 삭제 대상을 확인한다.

- `.runtime/`은 정상적인 로컬 runtime state다. `make init-env-compose` 이후 존재할 수 있으며, `make clean-all`은 기본적으로 보존한다. 테스트와 패키징 정책은 `.runtime`의 로컬 존재가 아니라 release/source ZIP 포함 여부를 검사해야 한다.
- `package_release.sh`는 `.runtime`, `.venv`, `venv`, `env`, `.tox`, logs, run, cache, pycache, egg-info를 제외한다.

## Full-stack 진단

- `validate_vllm_compose.py`: compose vLLM command와 model serving/catalog/card 정책 정합성을 검증한다. Embedding pooling token budget 오류와 risk detector quantization drift를 사전에 막는다.
- `compose_diagnostics.sh`: `make ready-full` 실패 시 docker compose 상태와 주요 서비스 로그를 수집하고, 알려진 vLLM 장애 패턴을 요약한다.
- `check_hf_model_config.py`: weight load 이전에 발생하는 HF config 문제를 Docker 없이 재현하는 내부 canary다. Main Model profile 전수 검사는 CI의 `check_main_model_profiles.py`, risk runtime 검사는 `check_risk_vllm_image_config.sh`가 소유한다.
- `prepare_main_model_cache.py`: allowlisted main-model profile의 고정 revision 전체 snapshot을 공용 HF cache에 준비하고 local-only로 재검증한다. `make main-model-prepare PROFILE=<id>`로 실행하며 active runtime은 변경하지 않는다.
- `render_main_model_boot_override.py`: locked/configured/persisted profile 우선순위를 검증해 일회성 Compose boot projection을 원자적으로 생성한다. 공식 로컬·CI 실행 경로는 임시 파일을 사용하고 종료 시 삭제한다.

Risk detector의 `bitsandbytes` 설정은 운영 기본값이다. 원인 분리를 위한 A/B 테스트는 별도 override에서 수행하고, 기본 compose에서 임의 제거하지 않는다.

## Unified vLLM 이미지와 Kanana patch 점검

- `make first-run`: platform image와 모든 served model이 공유하는 unified vLLM image를 만들고, image 내부 Kanana config check를 실행한다.
- `make build-vllm-unified-image`: `ops/images/vllm-unified/Dockerfile`에서 26B/12B/embedding/embedding-ko/risk-prompt 공용 vLLM unified 이미지를 빌드하는 고급/수동 target이다.
- `make first-run`과 `make compose-up`은 `RISK_VLLM_IMAGE` 안의 label, metadata, Kanana risk model config load를 확인한다.
- `SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK=1 make compose-up`: image-internal config check만 건너뛴다. production 승격용으로 쓰지 않는다.

## 인증 제어 플레인 점검

`make auth-status`로 현재 public/admin/internal auth 상태를 확인하고, `make auth-doctor`로 위험 조합을 탐지한다. 후보 env 파일은 `make auth-status ENV=<path>`와 `make auth-doctor ENV=<path>`로 root `.env` 반영 전에 점검한다. 변경 전에는 `make auth-plan MODE=strict ENV=<path>`로 plan을 보고, 적용은 `make auth-apply MODE=strict ENV=<path>`로 managed auth flag만 수정한다. secret 값은 출력하지 않는다.

## Risk vLLM patch 생명주기

`build_vllm_unified_image.sh`는 `ops/patches/` 아래 patch script를 포함한 unified 이미지를 빌드한다. `check_risk_vllm_image_config.sh`는 그 안의 Kanana patch label, metadata, config loading을 검증한다.

## OpenAPI snapshot diff

`scripts/validation/openapi_snapshot_diff.py`는 strict auth 기준 generated OpenAPI와 checked-in static OpenAPI의 path/method/security/operationId/response status/contract schema drift를 확인한다. `make validate`의 정적 검증 단계에 포함된다.
