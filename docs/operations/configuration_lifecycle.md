# 통합 설정·관리·빌드·제거 UX

이 문서는 설정 파일, 생성 산출물, 빌드/운영 명령, 삭제 명령이 어떤 책임을 갖는지 한 곳에 정리한다.

## 1. 설정의 단일 원천

| 영역 | 원천 | 생성/검증 대상 |
|---|---|---|
| 모델 catalog | `configs/model_catalog.yaml` | model cards, model contracts, model-list schema projection |
| runtime service | `configs/model_serving.yaml` | vLLM command, runtime target report, compose validation |
| service/port registry | `configs/services.yaml` | compose service name, host/container port projection, bind env mapping, exposure category |
| GPU 예산 | `configs/gpu_budgets.yaml` | operator status bundle, resource docs |
| monitoring label | `configs/monitoring.yaml` | Prometheus/Grafana projection, recording-rule validation |
| auth profile | `configs/auth_profiles.yaml` | AUTH_MODE 기대값, auth-plan/apply, auth_control.py drift check |
| exposure profile | `configs/exposure_profiles.yaml` | EXPOSURE_MODE별 profile, host-published service reference, diagnostics, compose override |
| env contract | `.env.*.example` | enabled model runtime env key 완전성 검증 |
| project inventory | source tree + documentation entrypoints | file ownership, management UX, handoff review matrix |
| runtime matrix | `harness/runtime_validation_matrix.yaml` | runtime validation config-only checks |
| secret/env | `.env` generated from `.env.*.example` | local/compose runtime behavior |

모델과 runtime 관련 해석은 코드에서 직접 YAML을 반복 해석하지 않고 `ModelRegistry` projection을 우선 사용한다.

### Service/port registry boundary

`configs/services.yaml`이 host-published service registry다. 이 파일이
`compose_service`, `container_port`, `default_host_port`, `host_env_port`,
`host_env_bind`, `categories`를 갖고 `render_exposure_overrides.py`, auth doctor/status,
preflight, exposure profile validator가 읽는다. exposure category도 이 registry의
source-of-truth다. `configs/exposure_profiles.yaml`은 profile, `host_published`
reference, diagnostics만 갖는다.

기존 `configs/ports.yaml`은 제거했다. endpoint/runtime/monitoring port 검증도
`configs/services.yaml`의 `default_host_port`를 읽으므로 기본 host port 숫자는 이 registry에
한 번만 기록한다.

## 2. 환경 파일 선택

세 가지 `.env` example 파일이 있다. 각 파일의 역할은 다음과 같다:

| 파일 | 역할 |
|---|---|
| `.env.example` | 전체 key 참조용. 직접 복사하지 않는다. |
| `.env.local.example` | `make init-env-local` 템플릿. localhost 기반 app-only 개발용. |
| `.env.compose.example` | `make init-env-compose` 템플릿. full-stack compose 배포용. image tag·secret 포함. |

| 목적 | 명령 | 설명 |
|---|---|---|
| 로컬 app-only | `make init-env-local` | localhost 기반 Gateway/Risk Adapter 개발용 |
| full-stack compose | `make init-env-compose` | compose 내부 hostname, Prometheus token file 포함 |
| 기존 값 보존 후 재발급 | `make init-env-compose-force` | 비밀키 재발급이 필요한 경우에만 사용 |
| **git pull 후 키 동기화** | **`make sync-env`** | 누락 키 추가·폐기 키 제거. 시크릿·기존 값 보존 |
| runtime secret file 복구 | `make sync-runtime-secrets` | `.env`는 유지하고 `.runtime/prometheus/admin_api_key`만 복구 |

app-only에서 `make init-env-compose`를 쓰면 compose hostname 때문에 readiness가 헷갈릴 수 있다. 이 경우 `.env`를 다시 만들기보다 목적에 맞는 profile로 재초기화한다.

### 인증·노출 모드 변경

`.env` 생성 후 인증 프로파일과 노출 모드는 각각의 apply 커맨드로 변경한다.

**인증 프로파일 (AUTH_MODE):**

| 목적 | 명령 |
|---|---|
| 변경 미리보기 | `make auth-plan MODE=<profile>` |
| 변경 적용 | `make auth-apply MODE=<profile>` |
| 진단 | `make auth-doctor` |

profile 선택: `local_open` (신뢰된 사내망, 전체 stack 공개) · `internal_trusted`
(Gateway 인증을 네트워크에 위임) · `private_network` (API key 필요) ·
`strict` (인터넷 연결 가능 환경)

**노출 모드 (EXPOSURE_MODE):**

| 목적 | 명령 |
|---|---|
| 변경 미리보기 | `make exposure-plan MODE=<mode>` |
| 변경 적용 | `make exposure-apply MODE=<mode> [AUDIENCE=<x>]` |
| 현재 상태 확인 | `make exposure-status` |

mode 선택: `master_open` (`local_open` 기본, 전체 stack,
`private_lan`) · `private_network` (Gateway·Grafana만 host-published)

AUDIENCE 선택: `local_only` · `private_lan` · `vpn` · `public`

**운영 표준 세팅 예시:**

```bash
make init-env-compose
make auth-apply MODE=private_network
make exposure-apply MODE=master_open AUDIENCE=private_lan   # 모든 포트 허용
make compose-up
```

## 3. 빌드와 기동의 분리

| 목적 | 명령 | 서비스 기동 |
|---|---|:---:|
| 정적 검증 | `make validate` | 아니오 |
| 테스트 | `make test` | 아니오 |
| 플랫폼 이미지 빌드 | `make build-image` | 아니오 |
| 통합 파이프라인 빌드 | `make build-pipeline` / `make build` | 아니오 |
| 앱 이미지만 재빌드 | `make rebuild-app` / `make build-image` | 아니오 |
| unified vLLM 이미지 재빌드 | `make rebuild-vllm-unified` / `make build-vllm-unified-image` | 아니오 |
| 전체 재빌드 | `make rebuild-full` / `make bootstrap` | 아니오 |
| 로컬 app-only 기동 | `make start` | 예 |
| full-stack compose 기동 | `make compose-up` | 예 |
| readiness 확인 | `make ready-local` / `make ready-full` | 아니오 |

`make build-pipeline`/`make build`와 `make package`는 서비스를 남기지 않는다. 운영 스택 기동은 `make compose-up`, 로컬 app-only 기동은 `make start`로 분리한다.

## 4. 운영 산출물 관리

운영자용 report는 개별 명령으로도 만들 수 있고, 통합 명령으로도 갱신할 수 있다.

```bash
make operator-reports
```

포함되는 산출물:

| 산출물 | 명령 |
|---|---|
| runtime target inventory | `make runtime-targets` |
| Prometheus/Grafana projection | `make monitoring-projection` |
| static operator bundle | `make operator-status` |
| live evidence bundle | `make live-evidence` |

GPU 서버의 최신 live evidence가 필요하면 `make runtime-validate`를 먼저 실행한다.

## 5. 삭제와 초기화

| 범위 | 명령 | 보존되는 것 |
|---|---|---|
| 미리 보기 | `make remove-plan` | 실제 삭제 없음 |
| 일반 산출물 | `make clean` | logs, model cache, `.runtime`, Docker image. timestamp runtime validation report는 정리 |
| 산출물 + logs | `make clean-all` | model cache, `.runtime`, Docker image |
| 모델 캐시 포함 | `PURGE_MODEL_CACHE=1 make clean-all` | `.runtime`, Docker image |
| runtime secret 포함 | `PURGE_RUNTIME_SECRETS=1 make clean-all` | model cache, Docker image |
| 모델 캐시 + runtime secret 포함 | `PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 make clean-all` | Docker image |
| Docker image 포함 | `make reset` | `.env`, upstream/base vLLM image |

`model_cache/`와 `.runtime/`은 실수 삭제 비용이 크므로 기본 보존한다. compose의 Hugging Face 다운로드 캐시는 기본적으로 `HF_CACHE_DIR=./model_cache/huggingface`에 모으며, vLLM 컨테이너 내부 `/root/.cache/huggingface`로 mount한다.

Compose resource namespace는 `.env`의 `COMPOSE_PROJECT_NAME`으로 고정한다. 새 개발
환경의 기본값은 `ai-model-serving-platform`이며, 기존 배포가 쓰는 `compose`와 분리한다.
같은 Docker host에 여러 개발 clone을 둘 때는 설치마다 고유한 이름을 지정한다.

Full-stack Compose 명령은 `ENV_FILE`, `COMPOSE_FILE`,
`COMPOSE_PROJECT_NAME`을 하나의 실행 context로 해석한다. `ENV_FILE`은 Compose
변수 보간뿐 아니라 컨테이너의 `env_file`에도 같은 절대경로로 전달된다. project
이름 우선순위는 process environment, `ENV_FILE`, 기본값 `ai-model-serving-platform` 순서이며
up/down/logs/config/diagnostics와 CI 배포가 같은 값을 사용한다. 따라서 custom
환경은 직접 `docker compose`를 조합하지 말고 다음처럼 project 명령을 사용한다.

```bash
ENV_FILE=/srv/instance-a.env \
COMPOSE_FILE=ops/compose/full-stack.private-network.yaml \
make compose-up
```

## 6. 추천 단순 흐름

처음 온 운영자는 아래 세 개만 기억하면 된다.

```bash
make guide          # 지금 상황에서 어떤 명령을 쓸지 확인
make first-run      # 처음 full-stack 준비
make rebuild-full   # 전체 재빌드
make operator-reports # 운영 산출물 갱신
```

문제가 생기면 다음 순서로 본다.

```bash
make doctor
READY_MODE=full make status
make compose-diagnostics
```
