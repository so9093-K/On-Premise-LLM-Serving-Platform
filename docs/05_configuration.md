# 5. 설정 체계와 Source of Truth

AI Model Serving Platform의 동작은 **YAML 기반 정책·registry**, **환경변수**, **Compose topology**, **runtime state**가 함께 결정한다.

각 설정의 실제 값과 허용 범위는 해당 Source of Truth를 기준으로 한다. 이 장에서는 설정 구조, 적용 방식, 변경·검증 흐름을 정리한다.

실행 구조는 [4. 실행 환경과 모드](./04_runtime_modes.md), 모델 전환과 GPU 운영 절차는 [6. 모델 운영](./06_model_operations.md), 외부 API 계약은 [API Reference](./reference/api_reference.md)에서 다룬다.

---

## 5.1 설정 구조

설정은 크게 네 영역으로 나뉜다.

```text
Repository Configuration
│
├─ Model / Runtime Policy
│   ├─ model_catalog.yaml
│   ├─ model_serving.yaml
│   ├─ main_model_profiles.yaml
│   └─ gpu_budgets.yaml
│
├─ Service / Deployment Policy
│   ├─ services.yaml
│   ├─ exposure_profiles.yaml
│   ├─ deploy_profiles.yaml
│   └─ auth_profiles.yaml
│
├─ Environment Contract
│   ├─ env_contract.yaml
│   ├─ .env.local.example
│   ├─ .env.compose.example
│   └─ .env.example
│
└─ Runtime / Generated State
    ├─ .env
    ├─ .runtime/*
    └─ generated runtime artifacts
```

YAML 파일은 모델, runtime, 서비스, 보안 정책 같은 **repository-level configuration**을 정의한다. 환경변수는 배포 환경에 따라 달라지는 endpoint, secret, timeout, bind 주소 등의 값을 제공한다.

환경변수 override를 지원하는 항목에는 process environment 또는 `.env` 값이 적용되며, 나머지 repository 정책은 각 YAML 정의를 기준으로 사용한다.

---

## 5.2 Source of Truth

주요 설정 영역의 Source of Truth는 다음과 같다.

| 영역 | Source of Truth | 역할 |
|---|---|---|
| 모델 목록과 capability | `configs/model_catalog.yaml` | 논리 model ID, upstream model, modality, capability 정의 |
| Runtime serving 정책 | `configs/model_serving.yaml` | 공통 endpoint, timeout, admission, embedding/risk routing 정의 |
| Main Model 실행·API profile | `configs/main_model_profiles.yaml` | 선택 가능한 Main Model의 vLLM command, capability, Gateway 요청 정책 정의 |
| GPU resource budget | `configs/gpu_budgets.yaml` | runtime별 GPU budget과 admission 기준 정의 |
| Service / port registry | `configs/services.yaml` | Compose service 이름, container/host port, bind env, exposure category 정의 |
| Exposure mode | `configs/exposure_profiles.yaml` | 어떤 서비스를 host에 publish할지 정의 |
| Deploy Runtime Profile | `configs/deploy_profiles.yaml` | compose-up/full deploy 후 어떤 secondary runtime을 deferred 상태로 둘지 정의 |
| Authentication profile | `configs/auth_profiles.yaml` | `AUTH_MODE`별 인증·관리 endpoint 보호 정책 정의 |
| Environment example contract | `configs/env_contract.yaml` | `.env` 예시 파일에 포함할 키 정의 |
| Monitoring 설정 | `configs/monitoring.yaml` | Prometheus scrape와 live metric 검증 기준 정의 |
| vLLM derived image build | `configs/vllm_unified_build.yaml` | target platform, base image와 compatibility pin 정의 |
| vLLM runtime patch | `ops/images/vllm-unified/Dockerfile`, `ops/patches/` | derived image에 적용할 patch와 적용 조건 정의 |
| 권장 container image | `configs/recommended_images.yaml` | 로컬 build와 초기 env에 사용할 기본 image tag 정의. 실제 배포 재현성은 registry digest가 담당 |
| Error metadata | `configs/error_catalog.yaml` | error code의 의미, retry 권장 여부, operator action 정의 |
| API endpoint | `src/ai_model_serving/api/endpoint_spec.py`, `specs/openapi.gateway.yaml` | 외부 API endpoint 계약 정의 |
| Request / response schema | `specs/schemas/*.json` | API payload 구조와 정적 제약 정의 |
| Compose topology | `ops/compose/*.yaml` | container, network, volume, healthcheck 기본 구조 정의 |
| Build / 운영 명령 | `Makefile`, `scripts/` | 설정 생성·검증·빌드·기동 동작 정의 |

같은 정보가 여러 파일에 보이더라도 위 Source of Truth를 기준으로 해석한다.

예를 들어 기본 host port 숫자는 `configs/services.yaml`에서 관리하고, exposure profile은 해당 service ID를 참조해 공개 범위만 정의한다.

### 현재값, 생성물, 문서의 경계

문서는 설정을 복제해 현재값을 선언하지 않는다. 각 층위의 역할은 다음과 같다.

| 층위 | 기준 | 역할 |
|---|---|---|
| 선언 정책 | `configs/`, 코드, Compose, CI | 다음 실행·배포에서 적용할 값과 계약 |
| 생성물 | `.runtime/`, generated Compose/OpenAPI 등 | 선언 정책을 바탕으로 만든 projection. 원본 정책이 아님 |
| 실제 운영 상태 | 배포 image digest, boot log, `nvidia-smi`, vLLM `/metrics` | 현재 배포되어 실제로 동작하는 사실 |
| 문서 | `docs/` | 기준 파일·절차·결정 배경을 연결하는 안내 |

ADR과 resource 문서는 결정 이유와 검증 이력을 보존한다. 이 문서의 표 또는 과거 실측값보다 현재 설정과 실제 운영 상태를 우선한다.

---

## 5.3 설정값 적용 방식

설정 적용 방식은 영역에 따라 다르다.

### Application 설정

Gateway와 Risk Adapter가 사용하는 일부 runtime 설정은 다음 순서로 해석된다.

```text
Process Environment
        │
        ▼
.env 값
        │
        ▼
YAML 기본값
```

process environment에 값이 명시되어 있으면 `.env`보다 우선한다.

예를 들어 timeout이나 runtime endpoint처럼 환경별 override를 지원하는 항목은 다음 형태로 적용될 수 있다.

```text
REQUEST_TIMEOUT_SECONDS
        ↓
configs/model_serving.yaml timeouts.gateway_request_seconds
```

환경변수가 없으면 YAML의 기본값을 사용한다.

### Repository 정책 설정

다음 영역은 YAML 자체를 canonical definition으로 사용한다.

- model catalog와 capability
- Main Model profile 목록
- GPU budget 정책
- service registry와 category
- exposure / auth / deploy profile 정의
- env contract

이 영역을 변경할 때는 관련 YAML을 직접 수정하고 검증한다.

### `.env` 자동 로딩

local / test / development 실행에서는 repository의 `.env`를 기본 환경값으로 로드할 수 있다.

repository `.env` 자동 로딩은 local / test / development 환경에 적용된다. production 또는 staging 환경에서는 secret과 주요 값을 process environment 또는 배포 환경에서 명시적으로 제공한다.

---

## 5.4 모델과 Runtime 설정

모델 관련 설정은 세 파일의 역할을 구분해서 본다.

| 파일 | 질문 | 주요 역할 |
|---|---|---|
| `model_catalog.yaml` | **어떤 논리 모델인가?** | public identity, upstream model ID, capability, modality, public listing |
| `model_serving.yaml` | **플랫폼이 이 runtime에 어떻게 연결·운영할 것인가?** | endpoint, timeout, admission, routing |
| `main_model_profiles.yaml` | **Main Model을 어떤 계약으로 서빙할 것인가?** | model/revision/image, vLLM command, capability, request limits, request parameter policy, runtime features |

### `configs/model_catalog.yaml`

플랫폼에 등록된 논리 model과 모델 자체의 성격을 정의한다.

대표적으로 다음 정보를 갖는다.

- `local-main`, `local-embed`, `local-embed-ko` 같은 logical model ID
- upstream model ID
- model role과 capability
- 지원 input / output modality
- public model listing 정보
- model-specific API metadata와 lifecycle metadata

Gateway의 model registry와 `/v1/models` projection은 이 catalog와 `model_serving.yaml`을 함께 사용한다.

upstream 모델의 사양과 알려진 제약은 [모델 참고 자료](./reference/models/README.md)에 별도로 정리한다. 이 참고 자료는 실행 설정의 기준이 아니다.

### `configs/model_serving.yaml`

Gateway와 Risk Adapter가 runtime을 사용하는 방식을 정의한다.

주요 영역은 다음과 같다.

```text
model_serving.yaml
├─ models
├─ embedding_profiles
├─ risk_adapter
├─ timeouts
├─ operational_limits
├─ streaming
├─ security
└─ documentation
```

이 파일은 다음과 같은 정책의 기준이 된다.

- Main / Embedding / Prompt Risk runtime endpoint
- Embedding / Prompt Risk의 고정 model revision과 실행 인자
- model별 timeout과 concurrency
- queue와 circuit breaker 관련 제한
- embedding model routing
- 기본 retrieval model
- Risk detector 구성과 실행 순서
- request body 및 retrieval document 제한
- streaming 제한

실제 배포 환경에서는 일부 항목을 `.env`로 override할 수 있다.

### `configs/main_model_profiles.yaml`

`local-main` alias 뒤에서 선택할 수 있는 Main Model runtime profile을 정의한다.

현재 구조에서는 public model ID는 `local-main`으로 유지하면서 실제 model/revision과 vLLM command를 profile 단위로 전환할 수 있다.

profile에는 주로 다음 정보가 들어간다.

- model ID / revision
- runtime image
- vLLM command
- `max_model_len`
- `max_num_seqs`
- `gpu_memory_utilization`
- modality capability
- Gateway request limits와 request parameter policy
- runtime features(tool/reasoning/structured output 등)
- compatibility status와 검증 이력

Main Model 전환과 rollback 절차는 [6. 모델 운영](./06_model_operations.md)에서 설명한다.

---

## 5.5 Runtime State와 Boot Profile

Main Model의 **정의된 profile**과 **현재 선택된 runtime state**는 서로 다른 정보다.

```text
configs/main_model_profiles.yaml
        │
        │ selectable profiles
        ▼
.runtime/main-model/main-model-state.json
        │
        │ persisted selection
        ▼
compose-up boot projection
        │
        ▼
main-llm-vllm
```

`configs/main_model_profiles.yaml`은 선택 가능한 profile을 정의한다.

실제 `compose-up` 시에는 persisted Main Model state와 boot policy를 읽어 temporary Compose override를 생성하고, 그 결과로 Main Model container의 실행 command를 확정한다.

`.runtime/*`는 현재 선택된 상태와 운영 산출물을 저장한다. 지속적으로 관리하는 configuration definition은 `configs/`의 Source of Truth를 기준으로 한다.

---

## 5.6 Service, Port, Exposure 설정

### `configs/services.yaml`

서비스와 port의 canonical registry다.

각 service는 다음 정보를 가진다.

- Compose service name
- container port
- host port 환경변수
- 기본 host port
- bind address 환경변수
- exposure category

예를 들어 `gateway`, `main_llm_vllm`, `grafana` 같은 service ID를 다른 설정과 validator가 공통으로 참조한다.

### `configs/exposure_profiles.yaml`

실행된 서비스 중 어떤 서비스를 host에 publish할지 정의한다.

현재 canonical exposure mode는 다음 두 가지다.

| Mode | Host publish 범위 |
|---|---|
| `private_network` | Gateway와 Grafana 중심 |
| `master_open` | Gateway, model runtime, Risk Adapter, operations endpoint 등 전체 stack 중심 |

Exposure Profile은 실행된 service의 host 공개 범위를 관리한다. runtime 활성 상태는 Deploy Runtime Profile과 각 runtime lifecycle에서 결정한다.

```text
Deploy Runtime Profile
  └─ 어떤 runtime을 실행 상태로 둘 것인가

Exposure Profile
  └─ 실행된 service를 host에 publish할 것인가
```

네트워크 구조와 실제 port 노출은 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 설명한다.

---

## 5.7 Deploy Runtime Profile

`configs/deploy_profiles.yaml`은 compose-up과 full deploy 이후 secondary runtime의 초기 운영 상태를 정의한다. profile을 명시하지 않으면 `default_profile: retrieval_ready`가 적용되어 Prompt Risk 모델은 컨테이너만 생성되고 시작되지 않는다. Risk Adapter와 PII·Secret 검사 경로는 그대로 유지된다.

현재 control 대상은 다음과 같다.

- `embedding`
- `embedding_ko`
- `risk_prompt`

대표 profile은 다음과 같다.

| Profile | 의미 |
|---|---|
| `main_only` | Main Model 중심으로 기동하고 secondary runtime은 deferred |
| `retrieval_ready` (기본) | embedding 계열은 준비하고 Prompt Risk는 deferred |

Main Model Profile과 Deploy Runtime Profile은 서로 다른 실행 축을 관리한다.

```text
Main Model Profile
  └─ 어떤 Main Model을 실행할 것인가

Deploy Runtime Profile
  └─ 어떤 secondary runtime을 함께 활성화할 것인가
```

---

## 5.8 GPU Budget 설정

`configs/gpu_budgets.yaml`은 공유 GPU 환경에서 runtime별 resource budget과 admission 정책을 정의한다.

주요 설정 범주는 다음과 같다.

- GPU device class와 전체 VRAM 기준
- 확보해야 할 reserve memory
- runtime별 target budget
- `gpu_memory_utilization` 기준
- tuning range와 조정 우선순위
- 총 GPU utilization 상한 정책
- resource management 원칙

Main Model profile의 `gpu_memory_utilization`과 GPU budget은 하나의 resource policy로 함께 관리한다. 두 설정의 일관성은 runtime admission과 validation에서 확인한다.

실제 GPU tuning, runtime eviction, model switching 절차는 [6. 모델 운영](./06_model_operations.md)에서 다룬다.

---

## 5.9 인증과 노출 정책

인증과 네트워크 노출은 별도 profile로 관리한다.

### Authentication Profile

`configs/auth_profiles.yaml`은 `AUTH_MODE`별 인증 정책을 정의한다.

대표 mode는 다음과 같다.

- `local_open`
- `internal_trusted`
- `private_network`
- `edge_terminated`
- `strict`
- `custom`

각 profile은 Gateway API 인증, Admin API 인증, 내부 service auth, docs 공개 여부 등을 정의한다.

상태 확인과 변경은 project command를 사용한다.

```bash
make auth-status
make auth-plan MODE=<profile>
make auth-apply MODE=<profile>
make auth-doctor
```

### Exposure Profile

Exposure mode는 별도로 관리한다.

```bash
make exposure-status
make exposure-plan MODE=<mode>
make exposure-apply MODE=<mode>
```

`AUTH_MODE`는 **누가 호출할 수 있는지**를, `EXPOSURE_MODE`는 **어떤 서비스가 network에 공개되는지**를 정의한다. 두 profile을 함께 적용해 접근 경계를 구성한다.

---

## 5.10 환경 파일

프로젝트는 목적에 따라 세 가지 example env 파일을 제공한다.

| 파일 | 용도 |
|---|---|
| `.env.example` | 전체 환경변수 key를 확인하는 참조 파일 |
| `.env.local.example` | app-only 로컬 개발 환경 template |
| `.env.compose.example` | full-stack Compose 실행 환경 template |

example 파일은 실행 환경별 `.env`를 구성하기 위한 template으로 사용한다.

```bash
make init-env-local
```

또는

```bash
make init-env-compose
```

기존 `.env`의 누락 key를 현재 contract에 맞추려면 다음 명령을 사용한다.

```bash
make sync-env
```

### Environment Contract

`configs/env_contract.yaml`은 `.env` 예시 파일에 포함할 key를 정의한다. 실제 실행값의 필수 여부와 유효성은 settings, compose preflight, auth 진단이 판단한다.

주요 contract 영역은 다음과 같다.

```text
common_example_keys
├─ APP_ENV
├─ LOG_LEVEL
├─ GATEWAY_*
├─ AUTH_MODE
├─ EXPOSURE_MODE
└─ COMPOSE_PROJECT_NAME

runtime_override_example_keys
├─ MAIN_LLM_*
├─ EMBEDDING_*
├─ EMBEDDING_KO_*
└─ RISK_PROMPT_*
```

`make validate`는 example env 파일과 이 contract의 drift를 검사한다.

### API 문서·요청 경계 환경변수

다음 값은 환경별로 명시할 수 있는 대표적인 API 실행 경계다. 예시 파일 포함 여부는 `configs/env_contract.yaml`에서, 실제 허용값과 실행 검증은 설정 로더와 compose preflight에서 판단한다.

| 변수 | 역할 |
|---|---|
| `FASTAPI_DOCS_ENABLED` | `/docs`, `/redoc`, `/openapi.json` 활성화 여부. 기본 `true` |
| `CORS_ALLOWED_ORIGINS` | 브라우저 기반 별도 client를 허용할 origin 목록 |
| `REQUEST_TIMEOUT_SECONDS` | Gateway 전체 요청 timeout |
| `RISK_ADAPTER_TIMEOUT_SECONDS` | Gateway의 Risk Adapter 호출 timeout |
| `*_BASE_URL` | vLLM 또는 내부 service endpoint override |
| `*_MAX_CONCURRENCY` | 모델별 Gateway-side 동시 처리 상한 |
| `*_QUEUE_TIMEOUT_SECONDS` | 모델별 admission queue 대기 상한 |
| `READY_FULL_TIMEOUT_SECONDS` / `READY_FULL_INTERVAL_SECONDS` | full-stack readiness polling 시간과 간격 |

Streaming은 Gateway timeout만 늘려 해결되지 않는다. reverse proxy의 buffering을 끄고, Gateway·vLLM·proxy·client의 read timeout을 하나의 요청 예산으로 맞춘다. 상세 호출 경계는 [API 인터페이스](./reference/api_reference.md#34-streaming)를 따른다.

### Secret 관리

API key, internal token, Hugging Face token 같은 secret은 repository의 tracked config에 실제 값으로 기록하지 않는다.

full-stack에서는 `.env`와 `.runtime/` 아래 runtime secret 파일이 함께 사용될 수 있다. 배포 환경에서는 CI/CD 또는 운영 환경에서 secret을 주입한다.

---

## 5.11 Generated Artifact

일부 파일은 canonical config에서 생성되는 projection이다.

Generated artifact는 원본 config를 변경한 뒤 다시 생성하는 방식으로 관리한다.

대표 generated artifact는 다음과 같다.

| Generated Artifact | 주요 입력 |
|---|---|
| `ops/prometheus/prometheus.yml` | `model_catalog.yaml` + `model_serving.yaml` + `monitoring.yaml` |
| `specs/schemas/model_list_response.schema.json` | Model Registry projection |

runtime artifact를 갱신할 때는 다음 명령을 사용한다.

```bash
make render-runtime-assets
```

Exposure Compose override(`exposure_profiles.yaml` + `services.yaml` 입력)는 별도 generator다. Makefile target이 없어 직접 실행한다.

```bash
python scripts/compose/render_exposure_overrides.py
```

`make validate`는 두 generator의 drift를 각각 별도 단계로 검사한다(runtime asset drift, compose override drift).

---

## 5.12 설정 검증

설정을 변경한 뒤 기본 검증은 다음 명령이다.

```bash
make validate
```

현재 정적 검증에는 다음 영역이 포함된다.

| 검증 | 주요 확인 내용 |
|---|---|
| Contract validation | registry, schema, 설정 간 invariant |
| Shell syntax | 운영 shell script 구문 |
| Exposure profile validation | exposure profile 구조와 service reference |
| Compose override drift | generated exposure override 일치 여부 |
| Env contract validation | `.env.*.example`과 env contract 일치 여부 |
| Runtime asset drift | generated artifact 최신 상태 |
| OpenAPI snapshot diff | OpenAPI contract drift |
| Auth profile sanity | auth profile과 생성값 일관성 |

Compose 관련 설정을 변경했다면 effective configuration도 함께 확인한다.

```bash
make compose-config
make exposure-status
```

모델 runtime이나 GPU 설정 변경에는 static validation과 full-stack readiness를 함께 수행한다. 실제 API 계약 확인이 필요한 경우 [8. 테스트와 검증](./08_testing_validation.md)의 runtime validation을 추가한다.

---

## 5.13 변경 영향 범위

설정 변경 시 일반적인 영향 범위는 다음과 같다.

| 변경 영역 | 대표 파일 | 주요 영향 | 일반적인 후속 작업 |
|---|---|---|---|
| 모델 metadata / capability | `model_catalog.yaml` | model listing, capability contract | `make validate`, 관련 API 검토 |
| Gateway runtime 정책 | `model_serving.yaml` | endpoint, timeout, routing, admission | `make validate`, 대상 service 재기동 및 runtime 검증 |
| Main Model profile | `main_model_profiles.yaml` | Main Model boot command, capability, Gateway API 정책 | `make validate`, model prepare / switch 검증 |
| GPU budget | `gpu_budgets.yaml` | runtime admission, co-residency | `make validate`, full-stack readiness |
| Service / port | `services.yaml` | Compose / exposure / Prometheus 생성 | `make validate`, `make compose-config` |
| Exposure mode | `exposure_profiles.yaml` | host publish 범위 | `make validate`, exposure 적용, Compose 재적용 |
| Deploy profile | `deploy_profiles.yaml` | secondary runtime 초기 상태 | compose-up, full deploy 또는 runtime reconcile |
| Auth profile | `auth_profiles.yaml` | API / Admin / internal auth 정책 | `make validate`, auth plan/apply/doctor |
| Environment example contract | `env_contract.yaml` | example env key | example env 갱신, `make sync-env`, `make validate` |
| `.env` | runtime environment | 현재 실행 instance의 endpoint, secret, timeout 등 | 대상 process/container 재기동 가능 |
| API schema | `specs/schemas/*.json` | 외부 API contract | OpenAPI/API Reference 검토, `make validate` |

설정 변경의 세부 배포 절차는 [10. 배포](./10_deployment.md), 변경 유형별 체크리스트는 [13. 변경 가이드](./13_change_guide.md)에서 이어서 다룬다.

---

## 5.14 빠른 참조

설정 변경 시 먼저 확인할 파일을 목적별로 정리하면 다음과 같다.

| 변경하려는 항목 | 먼저 확인할 파일 |
|---|---|
| 모델 추가 / capability 변경 | `configs/model_catalog.yaml` |
| Gateway runtime endpoint / timeout / admission | `configs/model_serving.yaml` |
| Main Model 교체 / vLLM parameter / API capability·limit | `configs/main_model_profiles.yaml` |
| GPU allocation | `configs/gpu_budgets.yaml` |
| Service / port | `configs/services.yaml` |
| Host 공개 범위 | `configs/exposure_profiles.yaml` |
| Secondary runtime 시작 상태 | `configs/deploy_profiles.yaml` |
| 인증 정책 | `configs/auth_profiles.yaml` |
| 환경변수 예시 키 | `configs/env_contract.yaml` |
| Local 실행값 | `.env.local.example` |
| Full-stack 실행값 | `.env.compose.example` |

설정 변경 후에는 최소한 다음 순서로 확인한다.

```bash
make validate
make compose-config        # Compose 관련 변경 시
make exposure-status       # exposure/auth 관련 변경 시
```

실제 model runtime 또는 GPU 동작이 바뀌는 변경은 full-stack 환경에서 추가 검증한다.
