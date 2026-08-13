# 8. 테스트와 검증

코드와 설정 변경에 적용되는 검증 단계와 각 단계의 확인 대상, 실패 시 확인 항목을 설명한다.

프로젝트의 기본 검증 흐름은 다음과 같다.

```text
코드 / 설정 변경
      ↓
정적 검증
make validate
      ↓
자동화 테스트
make test
      ↓
실행 환경 확인
ready-local / ready-full
      ↓
Live Runtime 검증
make runtime-validate
```

| 단계 | 확인 질문 | 주요 대상 |
|---|---|---|
| `make validate` | 설정·계약·생성물이 서로 일치하는가? | Config, Schema, OpenAPI, Compose |
| `make test` | application logic이 예상한 동작을 수행하는가? | Gateway, Risk, Auth, Runtime Control |
| `ready-local` / `ready-full` | 현재 실행된 서비스가 요청을 받을 준비가 되었는가? | Process, Dependency, Inference Path |
| `make runtime-validate` | 실제 vLLM API·고급 요청·모니터링 연결이 동작하는가? | Full-stack Runtime |

---

## 8.1 검증 구조

검증은 변경으로 발생할 수 있는 문제를 가장 가까운 계층에서 확인하도록 구성한다.

예를 들어 API를 변경하면 다음 순서로 범위를 확장한다.

```text
API 변경
  ├─ Schema / OpenAPI 정합성    → make validate
  ├─ 요청 처리 동작             → make test
  ├─ 실행 중인 API              → ready / smoke
  └─ 실제 vLLM·모니터링 연결    → runtime-validate
```

설정 변경도 같은 원칙을 따른다.

```text
Config 변경
  ├─ YAML / 정책 / 참조 관계    → make validate
  ├─ 설정 해석과 decision logic  → make test
  ├─ Runtime 반영               → ready-full
  └─ 운영 환경 증빙             → runtime-validate
```

각 검증 계층의 목적은 다음 세 가지로 정리할 수 있다.

- **정합성 확인** — Source of Truth와 schema, Compose, generated artifact 사이의 일치 여부를 확인한다.
- **동작 검증** — request validation, authentication, runtime control과 같은 application 동작을 확인한다.
- **Live 환경 확인** — Docker, GPU, vLLM, monitoring이 실제 target 환경에서 동작하는지 확인한다.

### `validate`, `test`, `ready`의 차이

```text
make validate
  → 프로젝트 정의가 서로 맞는가?

make test
  → 코드가 기대한 동작을 하는가?

make ready-full
  → 지금 실행된 stack이 요청을 처리할 수 있는가?

make runtime-validate
  → 실제 API 계약과 monitoring 연결이 동작하는가?
```

설정의 Source of Truth와 generated artifact 관계는 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 설명한다.

---

## 8.2 정적 검증 — `make validate`

`make validate`는 repository의 설정, 계약, 생성물 사이의 정합성을 확인한다.

```bash
make validate
```

이 단계는 Python 환경에서 실행되며 live service나 GPU 상태와 독립적으로 사용할 수 있다.

### 검증 대상

검증 항목은 확인하는 영역과 감지하는 불일치를 기준으로 정리할 수 있다.

| 검증 영역 | 확인 내용 | 확인 대상 |
|---|---|---|
| 기본 계약 | YAML/JSON 형식, version, Python 호환성, port·model registry 관계 | 공통 설정과 registry 정합성 |
| API Contract | OpenAPI ref, request/response schema, error surface | 공개 API 호환성 |
| Model / Runtime Policy | model registry, risk budget, resource-control policy | 모델 실행 정책 |
| Shell Script | 운영 shell script syntax | Build·배포·운영 명령 |
| Exposure | exposure profile과 service category coverage | 서비스 host 공개 범위 |
| Compose Projection | exposure 설정에서 생성되는 Compose override | 실제 Compose topology |
| Environment Example Contract | `.env.*.example`과 `env_contract.yaml` | 예시 환경변수 키 누락 |
| Runtime Artifact | Source config에서 생성되는 runtime artifact | runtime 생성물 정합성 |
| OpenAPI Snapshot | FastAPI generated OpenAPI와 checked-in spec | 구현과 API spec 정합성 |
| Auth Profile | 환경 template과 인증 profile projection | 인증 mode 구성 |

### 실행 순서

현재 `make validate`는 다음 순서로 검증한다.

```text
Contract Validation
      ↓
Shell Syntax
      ↓
Exposure Profile
      ↓
Compose Override Drift
      ↓
Environment Contract
      ↓
Runtime Asset Drift
      ↓
OpenAPI Snapshot
      ↓
Auth Profile Sanity
```

### API Contract 검증

Gateway와 Risk Adapter API는 FastAPI 구현, checked-in OpenAPI, JSON Schema를 함께 사용한다.
OpenAPI snapshot 검증은 다음 항목을 비교한다.

- path와 HTTP method
- `operationId`
- security requirement
- response status
- request schema
- response schema와 content type

따라서 route 또는 schema 변경은 `make validate` 단계에서 API spec과 함께 확인할 수 있다.
API 상세 계약은 [API Reference](./reference/api_reference.md)를 참고한다.

### Generated Artifact 검증

일부 runtime·Compose 파일은 canonical config에서 생성된다.

```text
Source Config
     ↓
Renderer
     ↓
Generated Artifact
```

Source of Truth를 변경한 뒤 생성물을 갱신할 때는 다음 흐름을 사용한다.

```bash
make render-runtime-assets
make validate
```

`make validate`의 drift check가 현재 source와 checked-in artifact의 일치 여부를 확인한다.

---

## 8.3 Unit·Contract 테스트 — `make test`

`make test`는 application logic과 프로세스 경계를 넘는 deterministic contract를 pytest로 검증한다.

```bash
make test
```

현재 test wrapper는 다음 두 suite를 실행한다.

```text
tests/unit
      +
tests/contract
```

### Unit Test

Unit Test는 application의 작은 decision unit을 검증한다.

| 영역 | 검증하는 동작 |
|---|---|
| Settings | config loading, environment override, profile resolution |
| Gateway | request validation, routing, error mapping, orchestration |
| Authentication | API key와 admin/internal access decision |
| Risk Adapter | PII·Secret·Prompt Risk 결과 조합과 response contract |
| Main Model Control | state transition, profile validation, switch decision |
| GPU Admission | runtime 시작 가능 여부와 resource decision |
| Upstream Client | timeout, response parsing, error handling |
| Environment Setup | `.env` 생성과 profile projection |

외부 runtime 경계는 fake client와 deterministic fixture를 사용해 재현 가능한 조건으로 검증한다.

### Contract Test

Contract Test는 여러 모듈이나 artifact가 공유하는 규칙을 검증한다.

주요 대상은 다음과 같다.

- OpenAPI / JSON Schema 계약
- 공개 error contract
- authentication·authorization invariant
- model / runtime policy
- release artifact 규칙
- sensitive data handling contract
- 여러 consumer가 공유하는 Source of Truth invariant

정리하면 다음과 같다.

```text
Unit Test
  → decision behavior를 검증

Contract Test
  → module / artifact 사이의 공유 계약을 검증
```

### 테스트 소스와 Release Package

테스트 소스는 source repository에서 build-time 품질 게이트로 사용한다.
Release ZIP은 runtime에 필요한 source/config/ops artifact를 중심으로 구성되며 테스트 소스는 패키징 대상에서 제외된다.

릴리스 준비 단계에서는 source checkout에서 다음 순서로 실행한다.

```bash
make validate
make test
make package
```

로컬 개발과 Build 흐름은 [7. 로컬 개발과 빌드](./07_local_dev_build.md)를 참고한다.

---

## 8.4 실행 환경 검증

정적 검증과 자동화 테스트를 통과한 뒤에는 **현재 실행된 환경**을 확인한다.

실행 환경 검증은 Health → Readiness → Smoke 순서로 범위를 확장한다.

```text
Health
  ↓
Readiness
  ↓
Smoke
```

### app-only — `make ready-local`

```bash
make ready-local
```

app-only에서는 다음 process health를 확인한다.

- Gateway `/health`
- Risk Adapter `/health`

이 단계는 Gateway와 Risk Adapter의 application process가 정상적으로 응답하는지 확인하는 빠른 개발 검증이다.

### full-stack — `make ready-full`

```bash
make ready-full
```

full-stack readiness는 다음 흐름으로 진행된다.

```text
Gateway /health
      ↓
Gateway /ready
      ↓
Main Model serving gate
      ↓
Smoke Test
Strict Smoke Test
```

Gateway `/ready`는 enabled runtime dependency의 준비 상태를 확인한다.
모델 로딩 중에는 dependency 상태를 표시하며 readiness polling을 이어간다.

### Smoke Test — `make smoke`

```bash
make smoke
```

Smoke Test는 대표 API 요청이 실제 inference 경로를 통과하는지 확인한다.

| 경로 | 확인 목적 |
|---|---|
| Gateway `/health` | Gateway process 응답 |
| Gateway `/ready` | dependency readiness |
| `/v1/models` | logical model registry 노출 |
| `/v1/risk/assessments` | Risk aggregate inference path |
| `/v1/chat/completions` | Main Model의 strict JSON Schema structured-output path |
| `/v1/embeddings` / `local-embed` | 일반 embedding path |
| `/v1/embeddings` / `local-embed-ko` | Korean retrieval embedding path |

Risk Adapter host port를 사용할 수 있는 exposure에서는 Risk Adapter health/readiness와 detector API도 함께 확인한다.

`make ready-full`은 마지막 단계에서 동일한 strict smoke script를 실행하므로 full-stack readiness와 대표 inference path를 한 번에 검증한다. 실패를 무시하는 별도 warmup은 두지 않는다.

### Build 검증과의 관계

Platform 전체 Build Gate는 다음 순서다.

```bash
make build
```

```text
make validate
     ↓
make test
     ↓
Platform Image Build
     ↓
Image 내부 Application Import 확인
```

Build 자체의 상세 흐름은 [7. 로컬 개발과 빌드](./07_local_dev_build.md)를 참고한다.

---

## 8.5 Live Runtime 검증 — `make runtime-validate`

`make runtime-validate`는 full-stack의 실제 API 계약과 monitoring 연결을 확인하고 결과를 report로 남긴다.

```bash
make runtime-validate
```

기본 산출물은 `reports/runtime/` 아래에 JSON과 Markdown 형식으로 생성된다.

### 대상 URL 선택과 증빙 범위

후보 환경을 검증할 때 runtime validation의 host URL은 다음 우선순위를 따른다.

```text
CLI 인자 > process env / .env > services.yaml 기반 기본값
```

```bash
# .env의 GATEWAY_BASE_URL보다 CLI 인자가 우선한다.
python scripts/validation/runtime_validation.py --gateway-base http://candidate-gateway:9400

# CLI 인자가 없으면 process env 또는 .env를 사용한다.
GATEWAY_BASE_URL=http://staging-gateway:9400 python scripts/validation/runtime_validation.py
```

`--gateway-base`, `--risk-base`, 각 vLLM runtime `--*-base`, `--prometheus-base`가 후보 endpoint 지정에 사용된다. API key, admin key, internal service token과 raw prompt·응답·token은 명령 출력과 runtime report에 남기지 않는다.

배포 서버의 private-network 구성에서는 Gateway만 host에 공개되고 Risk·vLLM은 Compose 내부 DNS에서만 접근된다. 따라서 전체 API 검증은 Compose 네트워크에 연결된 실행 위치에서 service URL을 명시해 수행한다. host 기본 URL만으로 내부 서비스를 검사해 발생하는 connection refused/DNS 실패는 서비스 장애 증거가 아니다.

### 검증 범위

| 영역 | 주요 확인 내용 |
|---|---|
| Gateway | `/health`, `/ready`, `/v1/models` |
| Risk Adapter | health, readiness, enabled detector, aggregate assessment |
| vLLM Runtime | 각 runtime `/models`와 logical model 연결 |
| Chat | 일반 Chat, streaming Chat |
| Structured Output | text, `json_object`, `json_schema` |
| Advanced Request | logprobs, logit bias, tools + JSON schema, reasoning + JSON schema |
| Embedding | `local-embed`, `local-embed-ko` |
| Metrics | Gateway / Risk Adapter metric scrape |
| Prometheus | active scrape target |
| Grafana | API health, Prometheus datasource, dashboard import |

이 단계는 단순 readiness보다 범위가 넓다.

```text
ready-full
  → 서비스가 실제 요청을 처리할 준비가 되었는지 확인

runtime-validate
  → 실제 API 계약과 monitoring 연결을 확인하고 증빙 생성
```

### Live 검증 결과 수집

실패 결과까지 report로 수집하는 조사 작업에서는 다음 옵션을 사용한다.

```bash
python scripts/validation/runtime_validation.py --allow-failures
```

Runtime report는 check 결과와 latency·상태 정보를 중심으로 기록한다.
인증 token과 raw prompt, model output은 report의 운영 증빙 범위에서 제외한다.

---

## 8.6 변경 유형별 검증 선택

변경 영역에 가까운 검증부터 실행하고 runtime 영향이 있는 경우 live 환경까지 확인한다.

| 변경 영역 | 기본 검증 | Runtime 확인 |
|---|---|---|
| Gateway / Risk Adapter Python 코드 | `make validate` → `make test` | `make ready-local` 또는 관련 smoke |
| API route / schema / error contract | `make validate` → `make test` | API smoke |
| `configs/*.yaml` | `make validate` → `make test` | 영향받는 runtime readiness |
| `.env.*.example` / env contract | `make validate` | app-only 또는 full-stack 기동 |
| Compose / exposure | `make validate` → `make compose-config` | `make compose-up` → `make ready-full` |
| Main Model profile | `make validate` → `make test` | Main Model 전환 / full-stack smoke |
| GPU budget / runtime policy | `make validate` → `make test` | full-stack 기동 → `make ready-full` |
| Platform `Dockerfile` / dependency | `make build-image` | image 실행 후 readiness |
| Unified vLLM Dockerfile / compatibility / patch | Unified vLLM image build | full-stack → runtime validation |
| Monitoring config / dashboard | `make validate` | `make runtime-validate` |
| Release packaging logic | `make validate` → `make test` → `make package` | package artifact 확인 |

### 일반 Application 변경

```bash
make validate
make test
```

### Full-stack 영향이 있는 변경

```bash
make validate
make test
make compose-up
make ready-full
```

### GPU·vLLM·Monitoring 운영 증빙이 필요한 변경

```bash
make runtime-validate
```

이 표는 “모든 명령을 항상 실행하는 규칙”보다 **변경 영향에 맞는 검증 범위를 선택하는 기준**으로 사용한다.

---

## 8.7 테스트와 Validator 추가 기준

새 검증은 **어떤 문제를 확인하려는지**와 **어떤 규칙을 검증할지**를 먼저 정의한다.

```text
확인할 문제
  ↓
검증할 Invariant
  ↓
가장 가까운 검증 계층
```

### Unit / Contract Test가 적합한 대상

- 공개 API 호환성과 error contract
- authentication / authorization decision
- sensitive data handling
- Main Model 상태 전환
- retry와 timeout decision
- GPU admission과 resource policy
- 실제 장애에서 확인된 regression

예:

```text
확인할 문제
Risk response에 원문 secret이 포함됨
       ↓
Invariant
공개 response는 탐지 코드와 count만 제공
       ↓
검증 계층
Unit / Contract Test
```

### Validator가 적합한 대상

Source of Truth와 여러 artifact의 관계를 비교하는 규칙은 validator에서 관리한다.

예:

- YAML source ↔ generated Compose override
- env contract ↔ `.env.*.example`
- FastAPI generated OpenAPI ↔ checked-in OpenAPI
- model registry ↔ port/service registry
- runtime config ↔ generated artifact

이 구조는 같은 규칙을 로컬과 CI의 `make validate`에서 함께 사용할 수 있게 한다.

### Live 검증이 적합한 대상

실제 runtime 환경이 필요한 항목은 readiness, smoke, runtime validation에서 확인한다.

- 실제 vLLM model load
- Hugging Face artifact compatibility
- streaming response
- Prometheus target 상태
- Grafana datasource / dashboard import

장시간 부하·GPU headroom 측정은 배포 승인용 runtime validation에 섞지 않는다. 이는
명시적인 부하 시험으로 별도 계획·시간 상한·성공 기준을 정해 실행한다.

### 유지 기준

테스트와 validator는 다음 질문에 답할 수 있어야 한다.

1. 어떤 문제나 불일치를 확인하는가?
2. 어떤 invariant 또는 decision branch를 검증하는가?
3. 실패 메시지로 원인과 수정 위치를 좁힐 수 있는가?
4. 같은 문제를 이미 확인하는 검증 계층이 있는가?

Source와 artifact 관계가 핵심이면 validator를 강화하고, application behavior가 핵심이면 Unit/Contract Test를 사용한다.
실제 환경 상태가 핵심이면 live validation으로 연결한다.

### 삭제·통합 기준

테스트나 validator도 지속적인 책임이 없으면 유지하지 않는다. 다음 조건이면 삭제하거나 하나의 validator로 통합한다.

- `make validate` 또는 generated artifact의 `--check`가 같은 source/artifact 관계를 이미 확인한다.
- 현재 port, model ID, 기본값처럼 바뀔 수 있는 값을 암기할 뿐, 값이 달라졌을 때 막는 손실을 설명하지 못한다.
- private helper, 함수 호출 순서, 문자열 존재처럼 구현 세부만 고정한다.
- 기본 품질 gate에서 실행되지 않고 실행 주체·실행 시점·릴리스 판단 기준도 없다.
- 실제 판정 없이 “준비됨”, “제거 후보” 같은 상태만 보고한다.

삭제하거나 계층을 옮길 때는 PR 또는 커밋에 **무엇이 그 위험을 대신 막는지**를 남긴다. source/artifact 관계를 validator로 옮겼다면 그 validator가 단일 소유자다.

### 추가 전 기록할 것

새 테스트에는 다음 두 가지를 한 줄로 남긴다.

1. 막는 손실: 공개 API 호환성 파손, 인증 우회, 민감정보 노출, 상태 전환 실패 또는 실제 장애 재발 등
2. 소유 계층: validator, unit/contract test, 또는 live runtime validation

“이번 변경을 확인한다”, “현재 값과 같다”, “나중에 필요할 수 있다”는 유지 근거가 아니다. live service·Docker·GPU가 필요한 증빙은 pytest에 넣지 않고 `make runtime-validate` 같은 명시적 운영 명령으로 분리한다.

---

## 8.8 실패 해석과 빠른 참조

검증 실패는 실패한 계층을 기준으로 원인을 좁힌다.

| 실패 | 우선 확인할 대상 |
|---|---|
| Contract Validation | 변경된 config/schema/model registry와 참조 관계 |
| OpenAPI Snapshot | route, method, security, request/response schema |
| Environment Contract | env key, template, allowed mode |
| Exposure Profile | service category와 host publish 정책 |
| Compose Drift | source config와 generated override |
| Runtime Asset Drift | runtime artifact 생성 상태 |
| Unit Test | 해당 decision function의 behavior |
| Contract Test | 공개 계약 또는 module 간 invariant |
| `ready-local` | Gateway / Risk Adapter process 상태 |
| Gateway `/ready` | dependency와 runtime loading 상태 |
| Smoke Test | Chat / Risk / Embedding inference path |
| Runtime Validation | vLLM, monitoring, advanced inference category |

Generated artifact 갱신은 다음 흐름으로 수행한다.

```bash
make render-runtime-assets
make validate
```

Full-stack 상태와 로그는 다음 명령으로 확인한다.

```bash
make status
make compose-logs
```

### 명령 빠른 참조

| 목적 | 명령 |
|---|---|
| Source / contract / drift 확인 | `make validate` |
| Unit + Contract Test | `make test` |
| 전체 Platform Build Gate | `make build` |
| app-only process health | `make ready-local` |
| full-stack readiness + smoke | `make ready-full` |
| 대표 API smoke | `make smoke` |
| vLLM / monitoring 운영 검증 | `make runtime-validate` |
| Runtime artifact 재생성 | `make render-runtime-assets` |
| Effective Compose 확인 | `make compose-config` |

### 주요 구현 위치

| 영역 | 파일 / 디렉터리 | 역할 |
|---|---|---|
| Validation entry point | `scripts/validation/run_validate.sh` | `make validate` 실행 순서 |
| Contract validator | `scripts/validation/validate_contracts.py` | 공통 API·model·resource contract |
| Environment contract | `scripts/validation/validate_env_contract.py` | env template 정합성 |
| Exposure validator | `scripts/validation/validate_exposure_profiles.py` | service exposure 구조 |
| OpenAPI snapshot | `scripts/validation/openapi_snapshot_diff.py` | runtime OpenAPI와 spec 비교 |
| Runtime artifact renderer | `scripts/render_runtime_assets.py` | generated artifact 생성·drift 확인 |
| Test entry point | `scripts/validation/run_test.sh` | Unit / Contract pytest |
| Test source | `tests/unit/`, `tests/contract/` | 동작 / contract test |
| Local readiness | `scripts/ops/ready_local.sh` | app-only health gate |
| Full readiness | `scripts/ops/ready_full.sh` | full-stack readiness + smoke |
| Smoke | `scripts/ops/smoke_test.sh` | 대표 inference path |
| Runtime validation | `scripts/validation/runtime_validation.py` | live vLLM/monitoring report |

CI에서는 동일한 `run_validate.sh`와 `run_test.sh`를 기본 quality gate로 사용한다.
Pipeline 구조와 배포 gate는 [9. CI/CD](./09_cicd.md), live failure 진단은 [11. 관측성과 장애 대응](./11_observability.md)에서 설명한다.
