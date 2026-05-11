# 통합 설정·관리·빌드·제거 UX

이 문서는 설정 파일, 생성 산출물, 빌드/운영 명령, 삭제 명령이 어떤 책임을 갖는지 한 곳에 정리한다.

## 1. 설정의 단일 원천

| 영역 | 원천 | 생성/검증 대상 |
|---|---|---|
| 모델 catalog | `configs/model_catalog.yaml` | model cards, model contracts, model-list schema projection |
| runtime service | `configs/model_serving.yaml` | vLLM command, runtime target report, compose validation |
| 포트 | `configs/ports.yaml` | endpoint docs, compose/preflight policy |
| GPU 예산 | `configs/gpu_budgets.yaml` | operator status bundle, resource docs |
| monitoring label | `configs/monitoring.yaml` | Prometheus/Grafana projection, recording-rule validation |
| local storage path | `configs/storage_paths.yaml` | storage path report, cleanup/package policy review |
| project inventory | source tree + documentation entrypoints | file ownership, management UX, handoff review matrix |
| runtime matrix | `harness/runtime_validation_matrix.yaml` | runtime validation config-only checks |
| secret/env | `.env` generated from `.env.*.example` | local/compose runtime behavior |

모델과 runtime 관련 해석은 코드에서 직접 YAML을 반복 해석하지 않고 `ModelRegistry` projection을 우선 사용한다.

## 2. 환경 파일 선택

| 목적 | 명령 | 설명 |
|---|---|---|
| 로컬 app-only | `make init-env-local` | localhost 기반 Gateway/Risk Adapter 개발용 |
| full-stack compose | `make init-env-compose` | compose 내부 hostname, Prometheus token file 포함 |
| 기존 값 보존 후 재발급 | `make init-env-compose-force` | 비밀키 재발급이 필요한 경우에만 사용 |
| runtime secret file 복구 | `make sync-runtime-secrets` | `.env`는 유지하고 `.runtime/prometheus/admin_api_key`만 복구 |

app-only에서 `make init-env-compose`를 쓰면 compose hostname 때문에 readiness가 헷갈릴 수 있다. 이 경우 `.env`를 다시 만들기보다 목적에 맞는 profile로 재초기화한다.

## 3. 빌드와 기동의 분리

| 목적 | 명령 | 서비스 기동 |
|---|---|:---:|
| 정적 검증 | `make validate` | 아니오 |
| 테스트 | `make test` | 아니오 |
| 플랫폼 이미지 빌드 | `make build-image` | 아니오 |
| 통합 파이프라인 빌드 | `make build-pipeline` / `make build` | 아니오 |
| 앱 이미지만 재빌드 | `make rebuild-app` / `make build-image` | 아니오 |
| risk vLLM 이미지만 재빌드 | `make rebuild-risk-vllm` / `make build-risk-vllm-image` | 아니오 |
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
| local storage/cache/secret path inventory | `make storage-paths` |
| project/file inventory | `make project-inventory` |
| Prometheus/Grafana projection | `make monitoring-projection` |
| static operator bundle | `make operator-status` |
| live evidence bundle | `make live-evidence` |

로컬 저장 위치와 cleanup 정책을 확인하려면 `make storage-paths`를 먼저 본다. GPU 서버의 최신 live evidence가 필요하면 `make runtime-validate`를 먼저 실행한다.

## 5. 삭제와 초기화

| 범위 | 명령 | 보존되는 것 |
|---|---|---|
| 미리 보기 | `make remove-plan` / `make cleanup-plan` | 실제 삭제 없음 |
| 일반 산출물 | `make clean` | logs, model cache, `.runtime`, Docker image |
| 산출물 + logs | `make clean-all` | model cache, `.runtime`, Docker image |
| 모델 캐시 포함 | `PURGE_MODEL_CACHE=1 make clean-all` | `.runtime`, Docker image |
| runtime secret 포함 | `PURGE_RUNTIME_SECRETS=1 make clean-all` | model cache, Docker image |
| 모델 캐시 + runtime secret 포함 | `PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 make clean-all` | Docker image |
| Docker image 포함 | `make reset` | `.env`, upstream/base vLLM image |

`model_cache/`와 `.runtime/`은 실수 삭제 비용이 크므로 기본 보존한다. compose의 Hugging Face 다운로드 캐시는 기본적으로 `HF_CACHE_DIR=./model_cache/huggingface`에 모으며, vLLM 컨테이너 내부 `/root/.cache/huggingface`로 mount한다.

## 6. 추천 단순 흐름

처음 온 운영자는 아래 세 개만 기억하면 된다.

```bash
make guide          # 지금 상황에서 어떤 명령을 쓸지 확인
make first-run      # 처음 full-stack 준비
make rebuild-full   # 전체 재빌드
make project-inventory # 파일·문서·관리 inventory 갱신
make operator-reports # 운영 산출물 갱신
```

문제가 생기면 다음 순서로 본다.

```bash
make doctor
READY_MODE=full make status
make compose-diagnostics
```
