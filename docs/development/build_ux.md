# 빌드 UX와 명령 의미론

build, start, readiness, deploy, release는 서로 다른 동작이다.

이 프로젝트는 이 동작들을 의도적으로 분리한다. 로컬 개발, CI, 릴리스 패키징, 운영이 서비스를 실수로 기동하거나, readiness 실패를 숨기거나, 생성된 런타임 상태를 배포하지 않도록 명령 이름을 보수적으로 설계했다.

## 핵심 역할

```text
make build          = 통합 빌드 (정적 검증·결정론적 테스트·플랫폼 이미지·패키지 생성, 서비스 기동 없음)
make start          = 로컬 app-only 서비스 기동
make ready-full     = 운영 스택이 실제로 준비됐음을 검증
make reset          = 통합 제거/초기화 (서비스 중지 + 플랫폼/risk 이미지 + 아티팩트)
make first-run      = 처음 full-stack 준비 (make bootstrap 별칭)
make rebuild-full   = 전체 재빌드 (make bootstrap 별칭)
make bootstrap      = 전체 재빌드 (.venv + 의존성 + .env + 검증 + 플랫폼/risk 이미지 + risk config check)
```

`make first-run`, `make rebuild-full`은 새 담당자가 목적을 바로 이해하기 위한 alias다. `make build`는 CI의 릴리스 경로와 같은 검증·패키징·플랫폼 이미지 빌드 범위를 로컬에서 한 번에 실행하는 단일 진입점이다. 일반 branch CI는 패키지 artifact를 만들지 않고 validate·test·플랫폼 이미지 빌드만 수행한다.

`make build`는 플랫폼 Docker image까지 포함하는 명령이므로 Docker CLI와 daemon이
필수다. Docker 없이 ZIP만 만들려면 `make package`를 사용한다. 패키징은 Python
호환성과 API·모델 계약을 검증한 뒤, 소스와 정적 설정만 포함한 ZIP을 만든다.

`make bootstrap`을 실행한 작업 트리에서는 Make가 `.venv/bin/python`을 우선 사용한다.
따라서 이후 `make validate`, `make test`, `make build`는 bootstrap이 `requirements.lock`으로
만든 동일한 Python 환경에서 실행된다. CI는 별도 깨끗한 Python 3.12 이미지에 같은 lock을
설치한 뒤 같은 검증 wrapper를 실행한다. 필요할 때만 `PYTHON_BIN=/path/to/python make test`처럼
명시적으로 interpreter를 바꿀 수 있다.

## 빌드 계층

이 프로젝트에는 목적이 다른 네 가지 빌드 진입점이 있다.

| 계층 | 명령 | 대상 | Runtime-derived 이미지 포함? |
|---|---|---|:---:|
| Day-0 / 전체 설정 | `make first-run` / `make bootstrap` | .venv + 플랫폼 이미지 + vLLM unified 이미지 + config check | 예 (기본값) |
| CI / 릴리스 공통 빌드 | `make build` | 정적 검증 + 결정론적 테스트 + 패키징 + 플랫폼 이미지 | 아니오 |
| 타깃 재빌드 | `make rebuild-app` / `make build-image` | 플랫폼 이미지만 | 아니오 |
| 타깃 재빌드 | `make rebuild-vllm-unified` / `make build-vllm-unified-image` | vLLM unified 이미지(26B/12B/embedding/embedding-ko/risk-prompt 공용) | 예 (이것만) |
| CI derived 이미지 | `build-vllm-derived` | vLLM unified 이미지(`vllm-unified`) 빌드/push | 예 (명시 opt-in) |

**`make build`는 vLLM unified 이미지를 빌드하지 않는다.** CI와 릴리스 파이프라인은 vLLM runtime에 의존하지 않고 플랫폼 아티팩트만 재현 가능하게 생성해야 하기 때문이다. vLLM unified 이미지는 `make first-run`, `make bootstrap`, `make rebuild-vllm-unified`, `make build-vllm-unified-image`로만 생성된다.

**`embedding-ko-vllm`은 별도 derived Dockerfile 빌드 대상이 아니다.** `EMBEDDING_KO_VLLM_IMAGE` 환경 변수로 지정한 vLLM unified 이미지를 그대로 쓴다(별도 build 없음). 2026-07-24부터 derived Dockerfile은 `ops/images/vllm-unified/Dockerfile` 하나뿐이며, 26B/12B main-LLM/risk-prompt/embedding/embedding-ko가 전부 이 이미지를 쓴다. 로컬 make target(`make build-vllm-unified-image`)은 이 이미지를 직접 빌드하고, CI에서는 `build-vllm-derived` 또는 `ops/images/vllm-unified/README.md`의 수동 fallback 절차로 빌드·push·pin한다.

### vLLM unified 이미지를 다시 빌드해야 하는 시점

다음 중 하나가 변경됐을 때만 `make rebuild-vllm-unified` 또는 `make build-vllm-unified-image`가 필요하다.

- `ops/images/vllm-unified/Dockerfile` 수정
- `ops/patches/transformers_llama_head_dim_guard.py` 또는 `ops/patches/apply_gemma4_multimodal_patches.py` 수정
- `configs/recommended_images.yaml`의 unified-image base digest 또는 compatibility pin 변경
- vLLM base 이미지 교체

앱 코드만 변경한 경우 vLLM unified 이미지는 그대로 사용할 수 있다.



CI/CD와 로컬 실행의 경계:

- CI/CD: release, registry push, deploy orchestration
- Local: CI 없이 build/test/compose 재현
- Shared: Dockerfile, compose validation, public model-list schema

### `make bootstrap`의 unified vLLM 이미지 빌드 제어

`bootstrap`/`first-run`/`rebuild-full`은 세 가지 동작 모드를 제공한다.

```bash
# 기본: unified vLLM 이미지를 항상 재빌드
make first-run

# auto: unified vLLM 이미지가 이미 존재하면 skip — 개발 반복 재빌드용
SKIP_RISK_VLLM_IMAGE_BUILD=auto make rebuild-full

# 강제 skip: unified vLLM 이미지 빌드를 항상 건너뜀 (이미지 직접 관리할 때)
SKIP_RISK_VLLM_IMAGE_BUILD=1 make rebuild-full
```

`SKIP_RISK_VLLM_IMAGE_BUILD=auto`는 이미지 존재 여부를 `.env`의 `RISK_VLLM_IMAGE` 태그로 확인한다. 이 변수는 unified 이미지의 소비 경로 중 하나다. 이미지가 있으면 config check는 수행하지만 빌드는 건너뛴다. unified 이미지 빌드는 첫 10–20분의 가장 긴 단계이므로, 앱 코드만 반복 수정할 때는 이 옵션을 사용한다.

## 명령 경계

| 명령 | 역할 | 서비스 기동? | vLLM 필요? | 주 사용자 |
|---|---|:---:|:---:|---|
| `make validate` | 실행 전 계약·설정·생성물 drift 정적 검증 | 아니오 | 아니오 | 개발자 / CI |
| `make test` | unit·contract 테스트 (`runtime/docker/gpu` 제외) | 아니오 | 아니오 | 개발자 / CI |
| `make build` | 정적 검증 + 결정론적 테스트 + 패키징 + 플랫폼 이미지 빌드 | 서비스 유지 없음 | 아니오 | 릴리스 / CI |
| `make rebuild-app` | `make build-image` 별칭. 플랫폼 이미지만 재빌드 | 아니오 | 아니오 | 개발자 / 운영자 |
| `make build-image` | 플랫폼 Docker 이미지만 빌드. validate·test·패키징은 생략하며 `make bootstrap` 내부에서도 호출됨 | 아니오 | 아니오 | 개발자 / 운영자 |
| `make rebuild-vllm-unified` | `make build-vllm-unified-image` 별칭. 모든 served model이 공유하는 unified vLLM 이미지를 재빌드 | 아니오 | Docker image만 필요 | 운영자 / 디버깅 |
| `make build-vllm-unified-image` | 26B/12B/embedding/embedding-ko/risk-prompt 공용 vLLM 이미지를 빌드하는 고급 target. 일반 운영자는 `make first-run`/`make bootstrap` 사용 | 아니오 | Docker image만 필요 | 운영자 / 디버깅 |
| `make package` | 정적 검증 통과 후 릴리스 ZIP 생성 | 아니오 | 아니오 | 릴리스 / CI |
| `make start` | 로컬 app-only Gateway·Risk Adapter 기동 | 예 | 아니오 | 개발자 |
| `make ready-local` | 로컬 Gateway·Risk Adapter `/health` 엄격 확인 | 아니오 | 아니오 | 개발자 |
| `make ready-full` | 실제 upstream vLLM까지 포함한 엄격 readiness + smoke | 아니오 | 예 | 운영자 / CI |
| `make status` | 로컬 상태 정보 표시; `READY_MODE=full`로 의존성 상세 확인 | 아니오 | 선택 | 개발자 / 운영자 |
| `make stop` | 로컬 서비스 종료; compose 스택도 함께 종료 | 아니오 | 아니오 | 개발자 |
| `make clean` | 생성 아티팩트 제거; 트래킹된 서비스 실행 중이면 거부 | 아니오 | 아니오 | 개발자 / CI |
| `make clean-all` | 아티팩트·로그·선택적 대형 캐시 제거 | 아니오 | 아니오 | 개발자 / CI |
| `make remove-plan` | 삭제 대상 미리 보기 (`make clean-dry-run` alias) | 아니오 | 아니오 | 개발자 / 운영자 |
| `make reset` | 통합 제거/초기화. 서비스 중지 + 플랫폼 이미지 + 로컬 unified vLLM 이미지 삭제 + clean-all; 플래그로 model cache, runtime secret, venv, base image까지 확장 가능 | 아니오 | 아니오 | 개발자 / 운영자 |
| `make first-run` | `make bootstrap` 별칭. 처음 full-stack 준비 | 아니오 | Docker image 필요 | 운영자 |
| `make rebuild-full` | `make bootstrap` 별칭. 전체 재빌드 | 아니오 | Docker image 필요 | 개발자 / 운영자 |
| `make bootstrap` | `.venv` 생성 + 의존성 설치 + `.env` 초기화 + validate + test + 플랫폼 이미지 빌드 + unified vLLM 이미지 빌드 + Kanana config check | 아니오 | Docker image 필요 | 개발자 / 운영자 |
| `make compose-up` | runtime secret 동기화 + preflight 후 full-stack compose 기동 | 예 | 예 | 운영자 |
| `make compose-down` | full-stack compose 스택 종료 | 아니오 | 아니오 | 운영자 |
| `make guide` | 상황별 명령 추천 가이드 출력 | 아니오 | 아니오 | 개발자 / 운영자 |
| `make operator-reports` | runtime target, storage path, monitoring projection, operator status, live evidence 산출물 통합 생성 | 아니오 | 아니오 | 운영자 / 릴리스 |

## 분리 이유

**`build`는 `start`가 아니다.** CI와 릴리스 작업은 로컬 프로세스를 남기지 않고 재현 가능하게 검증·패키징해야 한다. 런타임 기동은 app-only 개발에서는 `make start`, full-stack compose에서는 `make compose-up`이 담당한다.

**`ready`는 `health`가 아니다.** 로컬 app-only health 확인은 Gateway·Risk Adapter 프로세스가 살아 있음만 증명한다. full-stack readiness는 설정된 vLLM upstream이 도달 가능하고 smoke 검증이 통과함을 증명한다. 이 때문에 `make ready-local`과 `make ready-full`을 분리했다.

**`reset`은 `clean`보다 강하다.** `make clean`은 Docker 이미지를 지우지 않는다. 플랫폼/unified vLLM image까지 정리하려면 `make reset`을 사용하고, 실행 전에는 `make remove-plan`으로 삭제 범위를 확인한다.

`make package`는 Python 호환성 및 API·모델 계약 검증이 통과한 뒤 ZIP을 만든다.

## 사용자 시나리오

### 로컬 app-only 개발

GPU나 vLLM 없이 Gateway·Risk Adapter가 정상 부팅하고 `/health`를 노출하는지 확인한다.

```bash
make guide
make init-env-local
make start
make ready-local
make status
make stop
```

### Full-stack compose 검증

Docker, NVIDIA runtime, 모델 접근 권한, 설정된 런타임 리소스가 있는 호스트에서 수행한다.

```bash
HF_TOKEN=hf_xxx make first-run
source .venv/bin/activate
make compose-up
make ready-full
make runtime-validate
make operator-reports
make compose-down
```

### 개발 반복 재빌드

앱 코드(`src/`, `configs/`, `scripts/`)를 수정했지만 vLLM unified 이미지는 그대로일 때 사용한다.

```bash
SKIP_RISK_VLLM_IMAGE_BUILD=auto AUTH_MODE=local_open make rebuild-full
make rebuild-app
```

`ops/images/vllm-unified/Dockerfile`, `ops/patches/`, `configs/recommended_images.yaml`의 unified-image base digest 또는 compatibility pin이 변경된 경우에는 `SKIP_RISK_VLLM_IMAGE_BUILD=auto`를 사용하지 않고 전체 `make first-run`을 실행하거나 `make rebuild-vllm-unified`를 직접 호출한다.
main-LLM(26B/12B)이 쓰는 digest를 갱신해야 하는 경우에는 로컬 `make rebuild-vllm-unified`가 아니라 release/tag pipeline의 `build-vllm-derived`를 `BUILD_VLLM_DERIVED=1` 또는 `DEPLOY_MODE=full`로 실행해 새 digest를 만들고 배포 `.env`의 `AUDIO_VLLM_IMAGE`(및 `VLLM_IMAGE`/`RISK_VLLM_IMAGE`/`EMBEDDING_KO_VLLM_IMAGE`)에 pin한다.

### 전체 초기화 + 재빌드

환경 오염, 머신 이관, 릴리스 전 완전 초기화가 필요할 때 사용한다.

```bash
make remove-plan
PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset
HF_TOKEN=hf_xxx AUTH_MODE=local_open make rebuild-full
source .venv/bin/activate
make compose-up
make ready-full
```

`make reset`은 `.env`와 upstream/base vLLM 이미지는 보존하고, 이 프로젝트가 만든 platform image와 local unified vLLM image는 삭제한다. base image까지 지워야 할 때만 `PURGE_BASE_IMAGES=1 make reset`을 사용한다.

`make bootstrap`은 `.env`의 `HF_TOKEN`을 확인하고, 없으면 경고를 출력한 후 계속 진행한다. 토큰 누락 시 `make compose-up` 단계에서 모델 pull이 실패한다. 기본 동작은 unified vLLM 이미지 빌드와 image 내부 Kanana config check까지 포함한다.

### 릴리스 패키징

```bash
make validate
make test
make package
```

`make package`는 정적 검증 통과 후 `dist/` 아래에 릴리스 ZIP을 생성한다. ZIP 루트는 `ai_model_serving_platform/`으로 고정된다. 런타임 상태·시크릿·로그·모델 캐시·Python 바이트코드·`*.egg-info`는 제외된다.

## 실패 의미론

- `make ready-local`: Gateway 또는 Risk Adapter `/health` 응답 없으면 비정상 종료.
- `make ready-full`: strict `/ready` 또는 smoke 검증 실패 시 비정상 종료.
- `/ready`는 JSON body가 `not_ready`일 때 HTTP 503을 반환한다. 로드 밸런서·Kubernetes probe가 부분 준비 상태를 성공으로 혼동하지 않는다.
- `make status`는 정보 표시용이며 엄격한 배포 게이트로 사용하면 안 된다.
- `make clean`은 트래킹된 로컬 서비스가 실행 중으로 보이는 동안 생성 프로세스 상태 삭제를 거부한다.

## 로컬 런타임 상태 정책

`make init-env-compose`는 `.runtime/prometheus/admin_api_key`를 생성한다. Prometheus가 마운트된 파일에서 admin bearer token을 읽기 위해 필요하다. 이는 예상된 로컬 런타임 상태다.

정책:

- 로컬 작업 트리: `make init-env-compose` 또는 `make sync-runtime-secrets` 실행 후 `.runtime/`이 존재할 수 있다.
- `make clean-all`: 비의도적 시크릿 손실 방지를 위해 기본으로 `.runtime/`을 보존한다.
- 완전 재생성: 로컬 런타임 시크릿을 의도적으로 재생성할 때만 `PURGE_RUNTIME_SECRETS=1 make clean-all`을 사용한다.
- 릴리스·소스 패키지: `.runtime/`·가상환경·로그·run 파일·캐시·바이트코드·빌드 메타데이터는 반드시 제외한다.

## 계약 검증용 원문 표기

아래 원문은 `validate_contracts.py` exact-match 검사 대상이다. 한국어 설명은 위 섹션을 기준으로 한다.

- build, start, readiness, deploy, release는 서로 다른 동작이다.
- `make build`는 artifact/image를 생성하고 검증한다.
- `make start`는 local service 또는 compose stack을 시작한다.
- `make ready-full`은 live stack readiness를 증명한다.

## 단순화된 operator entrypoint

명령 수가 많아 보일 때는 아래 명령을 우선 사용한다.

```bash
make guide            # 상황별 명령 선택
make first-run        # 처음 full-stack 준비
make build            # CI/릴리스 공통 빌드
make remove-plan      # 삭제 대상 미리 보기
make operator-reports # 운영 산출물 통합 생성
```

`make operator-reports`는 서비스를 기동하지 않고 `runtime-targets`, `monitoring-projection`, `operator-status`, `live-evidence`를 순서대로 실행한다. 최신 GPU live evidence가 필요하면 이 명령 전에 대상 서버에서 `make runtime-validate`를 실행한다.

## Risk vLLM patch metadata

`make rebuild-vllm-unified` / `make build-vllm-unified-image`는 `ops/patches/`의 Kanana `head_dim` patch를 적용한다. `make risk-vllm-config-check`는 image label, patch metadata, 두 Kanana config를 함께 검증한다. patch 제거는 patch 없는 후보 image에서 이 검증과 실제 vLLM smoke를 통과했을 때만 검토한다.
