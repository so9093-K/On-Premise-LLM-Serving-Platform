# 13. 변경 가이드

프로젝트 변경은 **기준 코드·설정 → 연관 계약·생성 파일 → 검증 → Build → Runtime 적용** 순서로 반영된다.

```text
변경 작업
   ↓
기준 코드 / 설정 수정
   ↓
연관 계약·생성 파일 반영
   ↓
검증 / 테스트
   ↓
필요한 Image Build
   ↓
Runtime 적용 / 배포
   ↓
정상 동작 확인
```

이 장은 자주 발생하는 변경 작업을 기준으로 **변경 위치, 실제 반영 경로, 검증·배포 범위**를 연결한다. 설정 항목의 의미는 [5. 설정 체계와 Source of Truth](./05_configuration.md), 검증 단계는 [8. 테스트와 검증](./08_testing_validation.md), 배포 방식은 [10. 배포](./10_deployment.md)를 따른다.

---

## 13.1 변경 흐름

변경 작업은 세 범위를 함께 확인한다.

| 범위 | 확인 내용 | 예시 |
|---|---|---|
| 변경 범위 | 직접 수정하는 코드·설정 | Gateway route, `model_serving.yaml`, Dockerfile |
| 실행 영향 | 변경이 반영되는 서비스·Runtime | Gateway, Main Model, Prometheus |
| 검증·배포 범위 | 필요한 검증, Build, 배포 | `make validate`, Platform Build, Full Deploy |

일반적인 작업 순서는 다음과 같다.

```text
1. 변경 영역 결정
       ↓
2. Source of Truth 수정
       ↓
3. 필요한 Schema / 생성 파일 / 연관 설정 반영
       ↓
4. make validate / make test
       ↓
5. app-only 또는 full-stack 확인
       ↓
6. 필요한 Image Build
       ↓
7. 배포 및 Runtime 확인
```

애플리케이션 내부 변경은 app-only와 Platform Image 검증이 중심이 된다. 모델 Runtime, Compose, GPU, vLLM 실행 환경에 영향을 주는 변경은 full-stack과 Runtime 검증까지 이어진다.

---

## 13.2 주요 변경 작업

| 변경 작업 | 주요 위치 | 실행 영향 | 기본 확인 |
|---|---|---|---|
| API 추가·수정 | Router, contract, schema | Gateway API | `make validate`, `make test` |
| Gateway / Risk Adapter 로직 | `src/ai_model_serving/` | Platform 서비스 | app-only + Platform Build |
| 모델 정책·제한값 | `configs/model_serving.yaml` | Gateway + 모델 정책 | 생성 파일 + 정적 검증 |
| Main Model Profile | `configs/main_model_profiles.yaml` | Main Model Runtime | Profile + 모델 전환 검증 |
| 모델 추가·제거 | Model Registry 관련 설정 | API 목록, Runtime, 모니터링 | `modelctl` + full-stack |
| Unified vLLM | Dockerfile, patch, build 설정 | vLLM Runtime | Unified Image + Runtime 검증 |
| 서비스 / 포트 | `configs/services.yaml` | Compose, 노출, 모니터링 | 생성 파일 + Compose 확인 |
| 네트워크 / 노출 | Exposure profile, Compose | Host 공개 범위 | Compose + full-stack |
| 모니터링 | `configs/monitoring.yaml`, `ops/` | Metrics, Logs, Dashboard | 생성 파일 + Dashboard 확인 |
| CI/CD | `.gitlab-ci.yml`, `scripts/ci/` | Pipeline / Deploy | Pipeline 단계 확인 |

---

## 13.3 API와 애플리케이션 변경

### API 변경

Endpoint, request/response field, validation constraint, error code, `/v1/models`의 capability·parameter 변경은 API 계약과 함께 반영한다.

```text
Router / Contract 변경
        ↓
Request / Response Schema
        ↓
OpenAPI
        ↓
Error / Model Discovery 확인
        ↓
API Reference
        ↓
validate / test
```

주요 위치는 다음과 같다.

| 영역                 | 주요 위치                                                           | 역할                             |
| ------------------ | --------------------------------------------------------------- | ------------------------------ |
| Endpoint           | `src/ai_model_serving/api/routers/`                             | API 경로와 요청 처리 진입점 정의           |
| Request / Response | `src/ai_model_serving/contracts/`                               | 요청·응답 모델과 애플리케이션 계약 정의         |
| JSON Schema        | `specs/schemas/`                                                | 외부 API 데이터 형식과 제약 조건 정의        |
| OpenAPI            | `specs/openapi.gateway.yaml`, `specs/openapi.risk-adapter.yaml` | 외부 API 명세와 문서화 기준              |
| Error              | `src/ai_model_serving/errors.py`, `configs/error_catalog.yaml`  | 오류 코드, 응답 형식, 오류 카탈로그 관리       |
| API 문서             | `reference/api_reference.md`                                    | API 사용 방법, 제약, 예제와 운영 참고 정보 설명 |


FastAPI 구현과 checked-in OpenAPI의 차이는 `make validate`에서 확인된다. Request/response schema와 error surface도 같은 정적 검증 흐름에 포함된다.

```bash
make validate
make test
```

Gateway와 Risk Adapter만으로 확인 가능한 변경은 app-only에서 검증한다.

```bash
make start
make ready-local
```

실제 Main Model, Embedding, Prompt Risk와 연결되는 요청 흐름이 바뀌면 full-stack까지 확인한다.

```bash
make compose-up
make ready-full
```

### Gateway / Risk Adapter 로직 변경

Gateway orchestration, authentication, retrieval, PII·Secret detector 등 애플리케이션 로직은 Platform Image에 포함된다.

```text
애플리케이션 코드
      ↓
validate / test
      ↓
app-only 또는 full-stack
      ↓
Platform Image
      ↓
Gateway / Risk Adapter 적용
```

Platform Image만 다시 만들 때는 다음 명령을 사용한다.

```bash
make build-image
```

관련 실행 흐름은 [7. 로컬 개발과 빌드](./07_local_dev_build.md), API 계약은 [API Reference](./reference/api_reference.md)를 참고한다.

---

## 13.4 설정 변경

설정 변경은 해당 Source of Truth에서 시작하고, 실제 consumer인 애플리케이션·Compose·생성 파일에만 반영한다. 모든 설정이 생성 파일을 갖는 것은 아니다.

| 설정 | 주요 반영 대상 |
|---|---|
| `configs/model_catalog.yaml` | Model Registry, `/v1/models`, Runtime target |
| `configs/model_serving.yaml` | 모델 endpoint, 요청 정책, 운영 제한 |
| `configs/main_model_profiles.yaml` | Main Model 실행 profile |
| `configs/services.yaml` | Service 이름, port, host bind metadata |
| `configs/exposure_profiles.yaml` | Host port 공개 범위 |
| `configs/deploy_profiles.yaml` | Secondary Runtime 초기 상태 |
| `configs/gpu_budgets.yaml` | Runtime GPU 자원 판단 |
| `configs/auth_profiles.yaml` | Authentication mode |
| `configs/monitoring.yaml` | Monitoring scrape와 live metric 검증 기준 |
| `configs/env_contract.yaml` | `.env` 예시 파일 키 목록 |

### 생성 파일이 연결된 설정

일부 설정은 checked-in Runtime·Compose 파일의 입력으로 사용된다.

```text
Source Config
     ↓
Renderer
     ↓
Generated File
     ↓
Drift Validation
```

변경한 설정이 renderer 입력인 경우에만 생성 파일을 갱신한다. 생성 파일과 무관한 설정은 해당 consumer의 검증만 수행한다.

```bash
make render-runtime-assets
make validate
```

`make validate`는 Runtime asset과 Compose override가 현재 Source of Truth와 일치하는지 확인한다.

### 환경변수 계약

새 환경변수나 허용값 변경은 다음 영역을 함께 확인한다.

- `configs/env_contract.yaml`
- `.env.local.example`
- `.env.compose.example`
- 관련 settings / script

```bash
make validate
```

환경변수와 YAML의 적용 관계는 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 관리한다.

---

## 13.5 모델 추가·제거와 Main Model Profile

모델 변경은 **logical model 추가·제거**와 **기존 `local-main` 실행 profile 변경**을 구분한다.

### 모델 추가·제거

모델 추가는 Model Registry뿐 아니라 API 모델 목록, Runtime topology, Compose, Monitoring까지 연결된다.

변경 전 영향 범위는 plan-only 명령으로 확인할 수 있다.

```bash
make model-propose-add \
  ID=<model-id> \
  ROLE=<role> \
  UPSTREAM=<upstream-model-id> \
  PORT=<port> \
  ENDPOINT=<endpoint>
```

모델 제거 계획:

```bash
make model-propose-remove ID=<model-id>
```

이 명령들은 Source 파일을 직접 수정하지 않고 변경 계획을 출력한다.

실제 모델 추가의 주요 반영 흐름은 다음과 같다.

```text
Model Catalog / 모델 참고 문서
        ↓
Model Serving / Service Registry
        ↓
Compose Runtime
        ↓
/v1/models
        ↓
Monitoring
        ↓
GPU / Runtime Validation
```

대표 변경 위치:

- `configs/model_catalog.yaml`
- `configs/model_serving.yaml`
- `docs/reference/models/<model-id>.md`
- `configs/services.yaml`
- `ops/compose/full-stack.private-network.yaml`
- `configs/monitoring.yaml`

변경 후 registry와 생성 파일을 확인한다.

```bash
make model-validate
make render-runtime-assets
make validate
```

새 GPU Runtime이 추가되면 실제 실행까지 검증한다.

```bash
make compose-up
make ready-full
make runtime-validate
```

### Main Model Profile 변경

`configs/main_model_profiles.yaml`은 `local-main`의 실제 실행 profile을 정의한다. 변경 내용은 model revision, Runtime Image, command, GPU allocation과 모델 전환 과정에 반영된다.

```text
Profile 변경
    ↓
Compatibility 확인
    ↓
Model Cache 준비
    ↓
Main Model 시작 / 전환
    ↓
Inference 확인
```

주요 확인 항목:

- upstream model과 pinned revision
- Runtime Image
- context / sequence / batch 설정
- GPU memory fraction과 전체 GPU budget
- compatibility status
- capability와 canary 범위

Target model cache를 미리 준비할 수 있다.

```bash
make main-model-prepare PROFILE=<profile-id>
```

Release Pipeline에서는 Main Model profile 또는 관련 compatibility 입력이 변경된 경우 Hugging Face profile 검증이 추가로 실행된다. 실제 모델 전환 흐름은 [6. 모델 운영](./06_model_operations.md)을 따른다.

---

## 13.6 Unified vLLM / Runtime Image 변경

Unified vLLM Image는 Main Model, Embedding, Korean Embedding, Prompt Risk Runtime이 공유한다.

주요 Build 입력:

- `ops/images/vllm-unified/Dockerfile`
- `ops/images/vllm-unified/requirements.media.lock`
- `ops/patches/apply_gemma4_multimodal_patches.py`
- `ops/patches/transformers_llama_head_dim_guard.py`
- `configs/vllm_unified_build.yaml`

```text
Dockerfile / Patch / Compatibility
              ↓
Unified vLLM Image Build
              ↓
새 Image Digest
              ↓
Runtime 적용
              ↓
Readiness
              ↓
GPU / Inference Validation
```

로컬 Build:

```bash
make build-vllm-unified-image
```

Runtime 확인:

```bash
make compose-up
make ready-full
make runtime-validate
```

`release` Pipeline에서는 Unified vLLM build 입력 변경 시 `build-vllm-derived`가 새 image digest를 생성한다. 자동 변경 감지 범위 밖의 명시적인 재빌드는 `BUILD_VLLM_DERIVED=1` Pipeline을 사용한다.

CI artifact와 배포 전달 방식은 [9. CI/CD](./09_cicd.md), 실제 적용과 완료 기준은 [10. 배포](./10_deployment.md)를 따른다.

---

## 13.7 Compose / Network / Exposure 변경

서비스 추가, port, volume, dependency, host 공개 범위 변경은 실제 Compose 구성에 반영된다.

### 서비스 / 포트

`configs/services.yaml`은 서비스 이름과 port registry의 기준이다.

```text
서비스 / 포트
      ↓
Runtime Endpoint / Monitoring
      ↓
Compose / Exposure
      ↓
Runtime Validation
```

```bash
make render-runtime-assets
make validate
make compose-config
```

실행 환경까지 반영할 경우:

```bash
make compose-up
make ready-full
```

### 노출 설정

`configs/exposure_profiles.yaml`은 host에 publish되는 service port 범위를 정의한다.

```text
Exposure Profile
      ↓
Compose Override
      ↓
Effective Host Ports
```

```bash
python scripts/compose/render_exposure_overrides.py
make validate
make compose-config
```

실행 환경의 Exposure mode 변경 계획은 다음 명령으로 확인한다.

```bash
make exposure-plan MODE=<private_network|master_open>
```

### Base Compose

`ops/compose/full-stack.private-network.yaml` 변경은 배포 시 Runtime 구성 변경으로 처리된다. Rolling 요청에 이 파일 변경이 포함되면 배포 스크립트가 Full 배포로 전환한다.

실행 구조와 네트워크는 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 설명한다.

---

## 13.8 모니터링 변경

모니터링 변경은 **데이터 생성 → 수집 → 조회 → 대시보드** 흐름을 연결해 확인한다.

```text
지표 / 로그
    ↓
Collector / Scrape
    ↓
Prometheus / Loki
    ↓
Grafana
```

### 지표 / 로그 변경

애플리케이션 metric이나 log field를 변경하면 다음 항목을 함께 확인한다.

- application metric / logging 구현
- `configs/monitoring.yaml`
- Prometheus rule 또는 scrape 대상
- Grafana Dashboard query
- 로그 수집이 변경되는 경우 Alloy / Loki 설정

### 대시보드 변경

프로비저닝된 Grafana Dashboard는 `ops/grafana/dashboards/*.json`에서 관리한다. Dashboard의 panel·query 계약은 JSON 자체가 소유하며, `configs/monitoring.yaml`에는 scrape와 live metric 검증에 필요한 값만 둔다.

```bash
make render-runtime-assets
make validate
```

실제 데이터까지 확인할 경우:

```bash
make compose-up
make runtime-validate
```

운영 화면과 지표는 [11. 관측성](./11_observability.md), 이상 상태 분석은 [12. 운영 관리 및 장애 대응](./12_operations.md)을 따른다.

---

## 13.9 CI/CD와 배포 로직 변경

Pipeline 변경은 **검증 → Build → Artifact → Deploy** 연결을 기준으로 확인한다.

주요 위치:

- `.gitlab-ci.yml`
- `scripts/validation/`
- `scripts/build/`
- `scripts/ci/`

```text
Pipeline Rule / Script
        ↓
Validate / Test
        ↓
Build Artifact
        ↓
Image Digest
        ↓
Deploy
```

Shell script syntax와 repository contract는 `make validate`에서 확인한다.

```bash
make validate
```

Platform Build가 변경된 경우 로컬에서도 동일한 build entry point를 확인한다.

```bash
make build
```

배포 스크립트 변경은 Rolling / Full 결정, Release 활성화, 변경 서비스 계산, Runtime Profile 적용, Readiness와 복구 흐름에 영향을 줄 수 있다. 대상 환경 검증은 [10. 배포](./10_deployment.md)의 완료 기준까지 이어진다.

---

## 13.10 변경 후 검증과 문서 반영

### 검증 범위

| 변경 유형 | 기본 검증 | 실행 환경 확인 | 빌드 / 배포 범위 |
|---|---|---|---|
| Gateway / Risk Adapter Python | `make validate`, `make test` | `make ready-local` | Platform / Rolling 중심 |
| API / Schema | `make validate`, `make test` | app-only 또는 full-stack | Platform Image |
| 일반 Config | `make validate` + 생성기 입력일 때만 생성 파일 갱신 | 영향 서비스 확인 | 변경 내용 기준 |
| Main Model Profile | config/profile 검증 | model prepare + switch + `ready-full` | Main Model / Full 가능 |
| Unified vLLM | Unified Image Build | `ready-full`, `runtime-validate` | Runtime Image / Full |
| Compose / Exposure | `make validate`, `make compose-config` | `make ready-full` | Compose / Full 가능 |
| 모니터링 | 생성 파일 + `make validate` | Dashboard + Runtime 검증 | Monitoring 적용 |
| CI/CD | `make validate` + 관련 build | Pipeline 실행 | Pipeline / Deploy |

전체 Platform 품질 gate:

```bash
make build
```

GPU와 vLLM까지 포함한 변경:

```bash
make compose-up
make ready-full
make runtime-validate
```

### 문서 반영 범위

공개 API 동작, 운영 절차, Source of Truth의 역할이 변경되면 관련 문서도 함께 갱신한다.

| 변경 영역 | 관련 문서 |
|---|---|
| 시스템 구성 / 책임 | [3. 시스템 구성](./03_system_components.md) |
| Runtime / 네트워크 / 노출 | [4. 실행 환경과 모드](./04_runtime_modes.md) |
| 설정 | [5. 설정 체계와 Source of Truth](./05_configuration.md) |
| Main Model 운영 | [6. 모델 운영](./06_model_operations.md) |
| 빌드 | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| 검증 | [8. 테스트와 검증](./08_testing_validation.md) |
| CI/CD | [9. CI/CD](./09_cicd.md) |
| 배포 | [10. 배포](./10_deployment.md) |
| 모니터링 | [11. 관측성](./11_observability.md) |
| 장애 대응 | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| API 계약 | [API Reference](./reference/api_reference.md) |

문서 구조나 파일명이 변경되는 경우 문서 index와 navigation 링크도 함께 갱신한다.

### 변경 완료 전 최소 확인

- 공개 API 계약을 바꿨다면 Router/contract와 함께 schema, checked-in OpenAPI, API Reference를 갱신한다.
- Source of Truth 설정이 생성기 입력이라면 생성 artifact를 갱신하고 `make validate`로 drift를 확인한다. 그렇지 않다면 해당 설정의 consumer와 영향 범위만 확인한다.
- 일반 application 변경은 `make validate`, `make test`와 영향 범위의 app-only 또는 full-stack 확인을 한다.
- vLLM image 입력 변경은 Unified derived image build와 `ready-full`, `runtime-validate`까지 연결한다.
- 릴리스 ZIP이 필요한 경우에만 `make package`를 실행한다. package에는 `.env`, `.runtime`, 로그, model cache, test source가 포함되지 않아야 한다.

---

## 13.11 빠른 참조

| 작업 | 주요 위치 | 기본 검증 | 실행 환경 확인 |
|---|---|---|---|
| API endpoint | Router + contract + schema/OpenAPI | `make validate`, `make test` | `ready-local` / `ready-full` |
| Request parameter | `model_serving.yaml` + contract/schema | 생성 파일 + validate/test | 대상 API |
| Gateway logic | `src/ai_model_serving/` | validate/test | `make ready-local` |
| Main Model Profile | `main_model_profiles.yaml` | config/profile 검증 | prepare + switch + readiness |
| 모델 추가 | catalog + serving + 모델 참고 문서 + compose | `model-validate`, validate | full-stack + runtime validation |
| vLLM patch / Dockerfile | `ops/images/vllm-unified/`, `ops/patches/` | Unified Build | ready-full + runtime validation |
| Service port | `services.yaml` | 생성 파일 + validate | compose-config + full-stack |
| Exposure | `exposure_profiles.yaml` | Compose override 재생성 + validate | effective port 확인 |
| Dashboard | Dashboard JSON | `make validate` | Grafana / runtime validation |
| Pipeline | `.gitlab-ci.yml`, CI scripts | validate + 관련 build | Pipeline |
| Deploy logic | deploy script / policy | validate | Release 배포 + readiness |

### 주요 명령

```bash
# 정적 정합성
make validate

# Application 동작
make test

# Source Config에서 생성 파일 갱신
make render-runtime-assets

# Platform 품질 Gate + Image Build
make build

# Full-stack 준비 상태
make ready-full

# GPU / vLLM / Monitoring Runtime 검증
make runtime-validate
```
