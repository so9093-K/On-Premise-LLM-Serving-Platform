# 처음 프로젝트를 받았을 때 전체 가이드

이 문서는 새 담당자가 이 패키지를 처음 받았을 때 **무엇을 먼저 읽고, 어떤 명령을 실행하고, 어떤 결과를 정상으로 봐야 하는지**를 한 흐름으로 정리한다. 빠른 명령만 필요하면 `docs/operations/day0_quickstart.md`를 본다. 명령 의미론과 빌드/제거/재빌드 경계는 `docs/development/build_ux.md`를 기준으로 한다.

## 0. 먼저 이해할 것

이 프로젝트는 Gateway 하나를 통해 생성, 임베딩, 위험 신호 분석을 표준 API로 제공한다.

| 영역 | 기본 역할 |
|---|---|
| Gateway `:9400` | `/v1/chat/completions`, `/v1/embeddings`, `/v1/risk/*`의 public entrypoint |
| Risk Adapter `:9405` | risk detector 결과를 signal-only schema로 정규화 |
| vLLM runtimes | `main-llm-vllm:9401`, `embedding-vllm:9402`, `embedding-ko-vllm:9406`, `risk-prompt-vllm:9403` 모델 runtime |
| Prometheus/Grafana | runtime·GPU·risk signal 운영 관측 |

Risk Adapter는 `allow`, `block`, `decision`, `action` 같은 최종 정책 결정을 하지 않는다. 위험 모델 결과를 **signal-only**로 전달하고, 차단/허용 정책은 제품 정책 레이어에서 결정한다.

## 1. 패키지를 받은 직후 확인

```bash
pwd
find . -maxdepth 2 -type f | sort | head
make help
make guide
```

정상 패키지라면 다음 파일이 있어야 한다.

| 파일/디렉터리 | 의미 |
|---|---|
| `README.md` | 최상위 진입점 |
| `docs/README.md` | 문서 지도 |
| `docs/operations/day0_quickstart.md` | 빠른 시작 |
| `docs/development/build_ux.md` | build/start/ready/reset/bootstrap 의미론 |
| `Makefile` | 운영자가 실행하는 모든 주요 명령의 entrypoint |
| `configs/model_catalog.yaml` | 모델 catalog source of truth |
| `configs/model_serving.yaml` | runtime service/source of truth |
| `specs/`, `contracts/` | API/schema/contract source of truth |
| `docs/operations/project_maintainability_status.md` | 현재 유지보수 상태와 남은 위험 |

릴리스 ZIP에는 실제 `.env`, `.runtime/`, `model_cache/`, `dist/`, `__pycache__`, timestamped live runtime report가 포함되면 안 된다.

## 2. 먼저 어떤 경로를 선택할지 결정

| 상황 | 선택 | 핵심 명령 |
|---|---|---|
| GPU 없이 코드와 API docs만 확인 | app-only | `make init-env-local` → `make start` → `make ready-local` |
| Docker/GPU 서버에서 전체 runtime 확인 | full-stack | `HF_TOKEN=hf_xxx make first-run` → `make compose-up` → `make ready-full` |
| CI/릴리스와 같은 정적 산출물 생성 | 통합 빌드 | `make build` |
| 앱 이미지만 빠르게 다시 빌드 | app rebuild | `make rebuild-app` |
| unified vLLM 이미지 재빌드 | unified image rebuild | `make rebuild-vllm-unified` |
| 삭제 범위를 먼저 보고 싶음 | plan | `make remove-plan` |
| 전체 제거/초기화 | reset | `make reset` |
| 전체 재빌드 | full rebuild | `make rebuild-full` 또는 `make bootstrap` |

`make first-run`, `make rebuild-full`은 `make bootstrap`의 읽기 쉬운 alias다. `make build`는 서비스를 기동하지 않는다.

## 3. App-only 경로

GPU/vLLM 없이 Gateway와 Risk Adapter process만 확인한다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python3.12 -m pip install --requirement requirements.lock
python3.12 -m pip install --no-deps -e ".[contract]"

make init-env-local
make validate
make test
make start
make ready-local
make auth-status
make model-status
make stop
```

정상 기준:

- `make ready-local`이 Gateway/Risk Adapter `/health`를 확인한다.
- app-only에서는 vLLM이 없으므로 strict `/ready`가 `not_ready`일 수 있다. 이때 `make ready-full`이 아니라 `make ready-local`을 사용한다.
- `make init-env-compose`로 만든 `.env`는 compose hostname을 담기 때문에 app-only 확인에는 맞지 않는다.

## 4. Full-stack 경로

Docker, NVIDIA Container Toolkit, GPU, Hugging Face token이 준비된 host에서 실행한다.

```bash
HF_TOKEN=hf_xxx make first-run
source .venv/bin/activate
make compose-up
make ready-full
make runtime-validate
make operator-reports
make validate
make test
make compose-down
```

정상 기준:

- `make first-run`은 `.venv`, dependency, `.env`, validate, test, platform image, unified vLLM image, image 내부 Kanana config check를 수행한다.
- `make ready-full`은 실제 vLLM upstream과 smoke까지 통과해야 성공한다.
- `make runtime-validate`는 GPU/vLLM live evidence를 `reports/runtime/`에 생성한다.
- 생성된 live evidence는 운영 서버의 증빙이고, source handoff ZIP에는 timestamped runtime report를 포함하지 않는다.

## 5. 빌드·재빌드·제거 명령 선택

| 하고 싶은 일 | 명령 | 서비스 기동 여부 | 비고 |
|---|---|:---:|---|
| 정적 검증 | `make validate` | 아니오 | 계약·문서·정책·projection 검사 |
| 테스트 | `make test` | 아니오 | deterministic pytest wrapper |
| 통합 빌드 | `make build` | 아니오 | validate + test + platform image + package |
| 플랫폼 이미지만 재빌드 | `make rebuild-app` / `make build-image` | 아니오 | 앱 코드 반복 수정 시 사용 |
| unified vLLM 이미지 재빌드 | `make rebuild-vllm-unified` / `make build-vllm-unified-image` | 아니오 | Dockerfile/Transformers/vLLM patch 변경 시 사용 |
| 전체 재빌드 | `make rebuild-full` / `make bootstrap` | 아니오 | .venv부터 unified image/Kanana config check까지 |
| 삭제 미리 보기 | `make remove-plan` | 아니오 | 실제 삭제 없음 |
| 일반 산출물 제거 | `make clean` | 아니오 | 이미지·모델 캐시·시크릿 보존 |
| 로그 포함 산출물 제거 | `make clean-all` | 아니오 | 기본적으로 `.runtime`, model cache 보존 |
| 통합 제거/초기화 | `make reset` | 아니오 | 서비스 중지 + platform/unified vLLM image + 산출물 제거 |

완전 초기화가 필요할 때만 다음을 사용한다.

```bash
PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset
HF_TOKEN=hf_xxx make rebuild-full
```

`make reset`은 `.env`와 upstream/base vLLM image를 기본 보존한다. base image까지 삭제하려면 `PURGE_BASE_IMAGES=1 make reset`을 명시한다.

## 6. 인증 UX 확인

현재 인증 상태는 반드시 명령으로 확인한다.

```bash
make auth-status
make auth-doctor
make auth-plan MODE=strict
```

후보 env 파일을 root `.env`로 복사하기 전에 점검할 수 있다.

```bash
python scripts/config/setup_env.py --profile compose --output /tmp/candidate.env --force
make auth-status ENV=/tmp/candidate.env
make auth-doctor ENV=/tmp/candidate.env
make auth-plan MODE=strict ENV=/tmp/candidate.env
```

운영 기준:

- `local_open`은 외부 접근이 차단된 신뢰된 사내망의 비인증 full-stack 모드다.
  `master_open/private_lan`이 함께 적용되어 vLLM endpoint도 host-publish된다.
- `private_network`, `edge_terminated`, `strict`는 public/admin/internal-service auth 상태를 명확히 구분한다.
- 인증 모드는 API 기능을 바꾸지 않고 접근 경계만 바꿔야 한다.

## 7. 모델 관리 UX 확인

현재 단계의 모델 관리는 read-only와 plan-only를 기본으로 한다.

```bash
make model-list
make model-status
make model-validate
make model-diff
```

모델 추가/제거는 바로 파일을 수정하지 않고 계획부터 만든다.

```bash
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm
make model-propose-remove ID=local-main
```

이 명령은 실제 파일을 쓰지 않는다. id, port, endpoint, runtime service 충돌과 GPU budget 경고, 영향 파일 목록, 후속 검증 절차를 보여준다.

## 8. OpenAPI와 contract 확인

```bash
make validate
python scripts/validation/openapi_snapshot_diff.py
```

Gateway/Risk Adapter generated OpenAPI는 FastAPI 기본 schema만 믿지 않고 checked-in schema를 주입한다. request/response schema, security, summary, description drift는 정적 검증에서 잡아야 한다.

## 9. Risk vLLM patch lifecycle 확인

Risk vLLM image는 Kanana explicit `head_dim` 호환을 위해 auditable vendor patch를 사용한다. 이 patch는 장기 fork가 아니라 임시 compatibility bridge다.

```bash
make rebuild-vllm-unified
make risk-vllm-config-check
make risk-vllm-patch-removal-check
```

정상 운영 기준:

- Dockerfile inline site-packages patch가 아니라 `ops/patches/` script가 적용되어야 한다.
- image label, patch metadata, hash verify가 있어야 한다.
- upstream Transformers/vLLM 조합이 patch 없이 통과하는지 확인하기 전까지 patch를 제거하지 않는다.

## 10. 장애가 나면 보는 순서

```bash
make doctor
make status
READY_MODE=full make status
make compose-diagnostics
```

자주 보는 증상:

| 증상 | 먼저 볼 것 |
|---|---|
| app-only에서 `make ready-full` 실패 | `make ready-local` 사용 여부, `.env` profile |
| compose에서 Risk Adapter 접근 실패 | `make compose-diagnostics`, compose service 상태 |
| embedding model pull 실패 | `HF_TOKEN`, Gemma 라이선스 동의 |
| Prometheus admin token 오류 | `make sync-runtime-secrets` |
| Risk vLLM config 오류 | `make risk-vllm-config-check` |
| auth mismatch | `make auth-doctor` |

## 11. 릴리스/패키징 전 확인

```bash
make validate
make test
make package
```

패키지에 포함되면 안 되는 것:

- 실제 `.env`
- `.runtime/`
- `model_cache/`
- `logs/`, `run/`
- `__pycache__/`, `*.pyc`
- `dist/` 재포함
- timestamped `reports/runtime/runtime_validation_202*.json|md`

## 12. 새 담당자의 최종 체크리스트

- [ ] 내가 app-only인지 full-stack인지 결정했다.
- [ ] `.env` profile을 목적에 맞게 만들었다.
- [ ] `make validate`, `make test`가 통과했다.
- [ ] full-stack이면 `HF_TOKEN`과 GPU/Docker 접근 권한을 확인했다.
- [ ] `make auth-status`, `make auth-doctor`로 인증 상태를 확인했다.
- [ ] `make model-status`, `make model-validate`로 모델 registry 상태를 확인했다.
- [ ] `make remove-plan`으로 삭제 범위를 먼저 봤다.
- [ ] 릴리스 전 `make validate`, `make test`, `make package`를 실행했다.


## 노출 정책과 runtime 검증 설정

- Admin, metrics, docs 노출 정책은 [`admin_metrics_docs_exposure_policy.md`](admin_metrics_docs_exposure_policy.md)를 기준으로 본다.
- `runtime_validation.py`의 host URL/env 우선순위는 [`runtime_validation_operations.md#설정-우선순위`](runtime_validation_operations.md#설정-우선순위)를 기준으로 본다.
- 패키징 전에는 `make validate`로 API·모델 계약을 확인한다.
