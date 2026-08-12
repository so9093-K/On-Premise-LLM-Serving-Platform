# 3. 시스템 구성

AI Model Serving Platform은 외부 API를 처리하는 **Gateway**, runtime과 container를 제어하는 **Admin / Control Sidecar**, 실제 모델 추론을 수행하는 **vLLM Runtime**, 위험 신호를 정규화하는 **Risk Adapter**, 그리고 metrics·logs·GPU·container 상태를 수집하는 **관측성 스택**으로 구성된다.

각 구성 요소는 별도의 책임 경계를 갖는다. 핵심 원칙은 **외부 API 처리, 모델 inference, privileged container control을 서로 분리하는 것**이다.

## 3.1 전체 시스템 구성

![AI 모델 서빙 플랫폼 시스템 구성도](../assets/ai_model_serving_system_architecture.jpg)

> 위 구성도는 주요 요청 경로와 서비스 관계를 나타낸다. 일부 운영 및 관리 컴포넌트는 가독성을 위해 생략되어 있으며, 상세 구성은 아래 컴포넌트별 설명을 참고한다.

### 상세 컴포넌트 맵

```text
Client / Application
        │
        ▼
┌──────────────────────────────┐
│           Gateway            │
│            :9400             │
└───────┬─────────┬────────────┘
        │         │
        │         ├──────────────► main-llm-vllm      :9401
        │         ├──────────────► embedding-vllm     :9402
        │         ├──────────────► embedding-ko-vllm  :9406
        │         │
        │         └──────────────► Risk Adapter       :9405
        │                                │
        │                                ├─ PII Detector    (local)
        │                                ├─ Secret Detector (local)
        │                                └─ Prompt Detector
        │                                      │
        │                                      ▼
        │                               risk-prompt-vllm :9403
        │
        └────────────────────────► Admin / Control Sidecar :8080
                                           │
                                           └─ Docker Engine

┌───────────────────────────────────────────────────────────┐
│                     Observability                         │
│ Prometheus · Grafana · Loki · Alloy · DCGM · cAdvisor    │
└───────────────────────────────────────────────────────────┘
```

요청이 실제로 어떤 순서로 이동하는지는 [2. 요청 처리 흐름](./02_request_flow.md)에서 다룬다. 이 문서에서는 각 구성 요소의 **역할, 책임, 비책임, 의존성과 장애 영향 범위**를 설명한다.

### 구성 요소 요약

| 구성 요소 | 내부 Port | 주요 책임 |
|---|---:|---|
| **Gateway** | `9400` | 외부 API, 인증, 검증, routing, orchestration, retrieval |
| **Admin / Control Sidecar** | `8080` | Runtime lifecycle, main model 전환, GPU budget admission, Docker 제어 |
| **Main Model Runtime** | `9401` | Chat / Multimodal inference |
| **Embedding Runtime** | `9402` | 범용 text embedding |
| **Korean Embedding Runtime** | `9406` | Korean retrieval embedding |
| **Risk Adapter** | `9405` | PII / Secret / Prompt risk signal 정규화 |
| **Prompt Risk Runtime** | `9403` | Prompt detector model inference |
| **Prometheus** | `9090` | Metrics 수집·저장 |
| **Grafana** | `3000` | Metrics / logs 시각화 |
| **DCGM Exporter** | `9400` | GPU telemetry |
| **cAdvisor** | `8080` | Container telemetry |
| **Loki** | `3100` | Log 저장·조회 |
| **Alloy** | host publish 없음 | Container log 수집·전달 |

> 위 port는 container 내부 서비스 기준이다. host publish 여부와 실제 노출 범위는 exposure mode에 따라 달라지며 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 다룬다.

---

## 3.2 Gateway

Gateway는 플랫폼의 **외부 API 경계이자 요청 orchestration 계층**이다.

애플리케이션은 개별 vLLM runtime이나 Risk Adapter의 내부 API를 직접 호출하지 않고 Gateway를 통해 기능을 사용한다.

```text
Client
  │
  ▼
Gateway :9400
  │
  ├─ Main Model Runtime
  ├─ Embedding Runtime
  ├─ Korean Embedding Runtime
  ├─ Risk Adapter
  └─ Admin / Control Sidecar
```

### Purpose

Gateway는 모델별 runtime 차이와 내부 서비스 topology를 외부 호출자에게 숨기고 하나의 API surface를 제공한다.

외부에서는 `local-main`, `local-embed`, `local-embed-ko` 같은 논리 model ID를 사용하며, 실제 upstream runtime과 active main model profile은 내부에서 해석한다.

### Responsibilities

| 책임 | 설명 |
|---|---|
| **API Entry Point** | Chat, Embedding, Retrieval, Risk, Admin API 제공 |
| **Authentication** | 활성 auth profile에 따라 API / Admin 인증 적용 |
| **Request Validation** | schema, parameter, model, modality, media input 제한 검증 |
| **Normalization** | 외부 요청을 runtime에서 처리할 수 있는 형태로 정규화 |
| **Routing** | 기능과 model ID에 따라 내부 runtime 선택 |
| **Orchestration** | 여러 내부 호출이 필요한 기능의 실행 순서 관리 |
| **Runtime Admission** | runtime state, concurrency, circuit 상태에 따라 요청 허용 여부 판단 |
| **Main Model Gate** | model 전환 중 신규 Chat 요청 차단 |
| **Retrieval** | embedding 호출 후 cosine similarity 계산과 rerank 수행 |
| **Risk Forwarding** | Risk API 요청을 Risk Adapter로 전달 |
| **Response Validation** | upstream response 구조와 model-specific contract 검증 |
| **Readiness / Metrics** | dependency readiness 집계와 API metrics 제공 |

### Gateway API Surface

주요 API 영역은 다음과 같다.

```text
/v1/models
/v1/chat/completions
/v1/embeddings
/v1/retrieval/*
/v1/risk/*
/admin/*
/health
/ready
/metrics
```

세부 request / response contract는 [API Reference](./reference/api_reference.md)에서 다룬다.

### Main Model Gate

Main Model은 profile switching을 지원하므로 Chat 요청마다 현재 control state를 확인한다.

```text
Gateway
  │
  │ GET admin-sidecar /main-model
  ▼
Main Model State
  │
  ├─ gate = open
  │     └─ Chat 처리
  │
  └─ gate != open
        └─ 신규 Chat 요청 503
```

model 전환 중에는 `MAIN_MODEL_SWITCH_IN_PROGRESS`, control plane 자체에 접근할 수 없으면 `MAIN_MODEL_CONTROL_UNAVAILABLE`로 처리한다.

Gateway는 현재 처리 중인 main model 요청 수를 별도로 추적하며 Sidecar의 switch drain 과정에 이 정보를 제공한다.

### Secondary Runtime State

Embedding과 Prompt Risk 같은 secondary runtime은 운영 상태에 따라 active / stopped로 관리할 수 있다.

Gateway는 stopped 또는 starting 상태의 runtime으로 신규 요청을 보내지 않는다.

실제 container start / stop은 Gateway가 직접 수행하지 않고 Admin Sidecar에 위임한다.

### Retrieval Ownership

현재 Retrieval은 독립된 retrieval server나 Vector DB를 사용하지 않는다.

Gateway process 내부 `RetrievalService`가 query와 documents를 embedding한 뒤 cosine similarity를 계산한다.

```text
RetrievalService
  │
  ├─ Query / Document Prompt Policy
  ├─ Embedding Runtime 호출
  ├─ Cosine Similarity
  └─ score / rerank 결과 생성
```

현재 구조에는 다음 별도 컴포넌트가 없다.

```text
Standalone Retriever      없음
Vector Database           없음
Persistent Vector Index   없음
```

### Does Not Own

Gateway는 다음 책임을 직접 소유하지 않는다.

- Docker socket 접근과 container lifecycle
- Main Model container 재생성
- GPU budget eviction 실행
- Model weight loading과 inference engine 관리
- Risk signal에 대한 최종 allow / block 정책
- Metrics와 logs의 장기 저장

### Dependencies

| Dependency | 사용 목적 |
|---|---|
| `main-llm-vllm` | Chat / Multimodal inference |
| `embedding-vllm` | 범용 Embedding |
| `embedding-ko-vllm` | Korean Embedding / 기본 Retrieval |
| `risk-adapter` | Risk assessment |
| `admin-sidecar` | Main model gate, runtime control, model switching |

Gateway `/ready`는 main model과 활성 상태로 간주되는 dependency를 probe해 전체 readiness를 계산한다.

운영자가 secondary runtime을 의도적으로 stopped 상태로 둔 경우 해당 runtime은 readiness의 필수 dependency에서 제외될 수 있다.

### Failure Impact

| 장애 | 주요 영향 |
|---|---|
| Gateway 장애 | 모든 외부 API 진입점 중단 |
| Main Model Runtime 장애 | Chat / Multimodal 불가 |
| Embedding Runtime 장애 | 해당 embedding model 사용 불가 |
| Korean Embedding 장애 | `local-embed-ko` 및 기본 Retrieval 불가 |
| Risk Adapter 장애 | Gateway Risk API 불가 |
| Admin Sidecar 장애 | Main Model Chat gate 확인과 Admin runtime control 불가 |

Admin Sidecar는 Gateway의 startup hard dependency가 아니다. Sidecar가 장애 상태여도 Gateway process 자체와 Sidecar를 직접 사용하지 않는 일부 경로는 살아 있을 수 있다.

### 구현 위치

| 영역 | 주요 위치 |
|---|---|
| Gateway application | `src/ai_model_serving/apps/gateway.py` |
| Inference API | `src/ai_model_serving/api/routers/gateway_inference.py` |
| Retrieval API | `src/ai_model_serving/api/routers/gateway_retrieval.py` |
| Risk API | `src/ai_model_serving/api/routers/gateway_risk.py` |
| Runtime Control API | `src/ai_model_serving/api/routers/gateway_runtime_control.py` |
| Gateway service | `src/ai_model_serving/services/gateway_service.py` |
| Retrieval service | `src/ai_model_serving/services/retrieval_service.py` |

---

## 3.3 Admin / Control Sidecar

Admin / Control Sidecar는 플랫폼의 **runtime control plane**이다.

Gateway가 외부 요청 처리에 집중하도록 Docker 제어 권한과 runtime lifecycle을 별도 process로 격리한다.

```text
Gateway
  │
  │ Internal Control API
  ▼
Admin Sidecar :8080
  │
  ├─ Main Model Manager
  ├─ GPU Budget Admission
  ├─ Runtime State
  │
  ▼
Docker Engine
```

### Purpose

Sidecar의 가장 중요한 목적은 **Gateway와 Docker 권한을 분리하는 것**이다.

Docker socket은 Admin Sidecar에 mount되고 Gateway에는 연결되지 않는다.

```text
Gateway
  └─ Docker Socket 없음

Admin Sidecar
  └─ /var/run/docker.sock
```

### Network Boundary

Sidecar의 기본 port는 `8080`이며 Compose network 내부에서만 사용한다.

```text
Gateway ──► http://admin-sidecar:8080
```

Sidecar port는 standard topology에서 host에 publish하지 않는다. 외부 운영 요청은 Gateway의 `/admin/*` API로 들어오고 Gateway가 Sidecar internal API를 호출한다.

설정에 따라 Gateway ↔ Sidecar 요청에는 internal service token을 사용한다.

### Responsibilities

| 책임 | 설명 |
|---|---|
| **Container Status** | 제어 가능한 runtime container 상태 조회 |
| **Runtime Start / Stop** | Secondary vLLM runtime lifecycle 제어 |
| **Main Model State** | active profile, gate, operation 상태 관리 |
| **Main Model Switch** | profile 기반 main model container 교체 |
| **GPU Budget Admission** | 신규 runtime 기동 전 공유 GPU budget 판단 |
| **Eviction Planning** | budget 초과 시 중지 가능한 runtime 계산 |
| **Health Wait** | runtime start 후 health 상태 확인 |
| **State Persistence** | Main model control 상태 유지 |
| **Runtime Reconciliation** | 수동 restart 등으로 생긴 control state 불일치 감지 |
| **Log Target Projection** | 실행 container 정보를 Alloy log target manifest에 반영 |

### Main Model Control

Main Model의 외부 model ID는 `local-main`을 유지하고 내부 profile만 전환할 수 있다.

```text
local-main
   │
   ▼
Active Main Model Profile
   │
   ├─ gemma4-12b-unified-fp8
   └─ gemma4-26b-a4b-fp8
```

profile에는 실제 model revision, runtime image와 command, context, concurrency, modality 같은 deployment input이 연결된다.

구체적인 profile 내용과 전환 절차는 [6. 모델 운영](./06_model_operations.md)에서 다룬다.

### Drain Coordination

Main Model 전환은 신규 요청을 차단한 뒤 기존 Chat 요청을 drain하는 방식으로 진행한다.

```text
Sidecar
  │
  ├─ Main Model Gate Close
  ▼
Gateway
  │
  ├─ 신규 Chat 요청 차단
  └─ In-Flight 요청 완료
        │
        ▼
Sidecar
  │
  └─ Runtime 교체
```

Gateway는 internal drain status를 제공하고 Sidecar는 이를 확인한 뒤 runtime lifecycle 작업을 진행한다.

### GPU Budget Admission

모든 vLLM runtime은 하나의 GPU를 공유한다.

Sidecar는 runtime start 또는 main model activation 전에 현재 활성 runtime과 목표 runtime의 GPU budget을 계산한다.

```text
Current Runtime Budget
        +
Target Runtime Budget
        │
        ▼
GPU Budget Admission
        │
   ┌────┴────┐
   │         │
  Fit      Exceed
   │         │
 Start   Reject / Eviction Plan
```

budget이 부족하면 기본적으로 `409 GPU_BUDGET_EXCEEDED`를 반환하고, 가능한 경우 중지 대상 plan을 함께 제공한다.

### Does Not Own

Admin Sidecar는 다음 책임을 소유하지 않는다.

- 외부 Chat / Embedding / Risk API contract
- 사용자 request validation
- 모델 inference 결과 생성
- Retrieval score 계산
- Risk signal schema의 최종 표현
- Metrics / logs 장기 저장

Sidecar는 **control plane**이며 user data plane 역할을 하지 않는다.

### Dependencies

| Dependency | 사용 목적 |
|---|---|
| Docker Engine | container inspect / start / stop / replace |
| Main Model Profile | main model deployment input |
| GPU Budget Config | runtime admission 판단 |
| Gateway Drain Status | main model switch drain 확인 |
| Model Cache | runtime 기동 시 model artifact 접근 |

### Failure Impact

Sidecar가 중단되면 main model 상태 조회, model switch, runtime start / stop, GPU budget admission이 영향을 받는다.

Gateway는 Sidecar 장애 때문에 process 자체가 시작되지 못하는 구조를 피한다. 다만 Chat은 active main model gate를 확인할 수 없으므로 `503 MAIN_MODEL_CONTROL_UNAVAILABLE`을 반환한다.

Embedding이나 Risk처럼 Sidecar gate를 매 요청마다 사용하지 않는 경로는 해당 runtime이 이미 실행 중이면 별도로 동작할 수 있다.

### 구현 위치

| 영역 | 주요 위치 |
|---|---|
| Sidecar application | `src/ai_model_serving/apps/admin_sidecar.py` |
| Main model control | `src/ai_model_serving/main_model/control.py` |
| Docker backend | `src/ai_model_serving/main_model/docker_backend.py` |
| Main model state | `src/ai_model_serving/main_model/state.py` |
| GPU budget | `src/ai_model_serving/gpu_budget.py` |
| Gateway sidecar client | `src/ai_model_serving/services/sidecar_client.py` |

---

## 3.4 vLLM Runtime

vLLM Runtime은 실제 model weight를 load하고 GPU에서 inference를 수행하는 **model data plane**이다.

Main LLM, Embedding, Korean Embedding, Prompt Risk model은 각각 독립된 vLLM process와 port를 사용한다.

```text
Shared GPU
  │
  ├─ main-llm-vllm       :9401
  ├─ embedding-vllm      :9402
  ├─ embedding-ko-vllm   :9406
  └─ risk-prompt-vllm    :9403
```

### Common Runtime Boundary

공통 원칙은 다음과 같다.

- 모델별 독립 vLLM process와 port 사용
- 모델별 context, concurrency, GPU memory budget 사용
- Gateway 또는 Risk Adapter에서 internal OpenAI-compatible API 호출
- model fallback 사용 안 함
- runtime health와 model readiness 확인

vLLM은 **모델 실행 엔진**이며 외부 API policy나 container lifecycle 정책은 소유하지 않는다.

### Runtime Summary

| Runtime | Service | Port | Runner | Logical Model | 용도 |
|---|---|---:|---|---|---|
| **Main Model** | `main-llm-vllm` | `9401` | generation | `local-main` | Chat / Multimodal |
| **Embedding** | `embedding-vllm` | `9402` | pooling | `local-embed` | 범용 Embedding |
| **Korean Embedding** | `embedding-ko-vllm` | `9406` | pooling | `local-embed-ko` | Korean Retrieval |
| **Prompt Risk** | `risk-prompt-vllm` | `9403` | generation | `risk-prompt` | Prompt detector inference |

### Main Model Runtime

Main Model Runtime은 Gateway의 Chat 요청을 처리한다.

외부에서는 `local-main`을 사용하지만 실제 underlying model과 입력 modality는 active main model profile에 따라 달라질 수 있다.

```text
Gateway
  │ model = local-main
  ▼
main-llm-vllm :9401
  │
  ▼
Active Profile Model
```

Gateway는 Sidecar가 제공하는 active profile의 deployed capability를 사용해 실제 허용 modality를 판단한다.

Main Model container는 profile switch 과정에서 Sidecar에 의해 교체될 수 있으므로 실제 운영 상태는 Compose의 초기 command만으로 판단하지 않는다.

### Embedding Runtime

`embedding-vllm`은 범용 text embedding을 제공한다.

```text
Logical Model : local-embed
Runtime       : embedding-vllm
Port          : 9402
Default Dim   : 768
```

Gateway `/v1/embeddings`에서 model을 생략하면 기본적으로 `local-embed`를 선택한다.

### Korean Embedding Runtime

`embedding-ko-vllm`은 Korean retrieval용 embedding runtime이다.

```text
Logical Model : local-embed-ko
Runtime       : embedding-ko-vllm
Port          : 9406
Default Dim   : 1024
```

현재 기본 Retrieval model은 `local-embed-ko`다.

Retrieval에서 query와 document에 적용되는 prompt policy는 embedding profile에서 관리하고 Gateway가 이를 적용한다.

### Prompt Risk Runtime

`risk-prompt-vllm`은 Prompt detector model을 실행한다.

```text
Risk Adapter
  │
  ▼
risk-prompt-vllm :9403
  │
  ▼
Detector Model
```

Client가 이 model output을 직접 해석하지 않는다. Risk Adapter가 detector input을 만들고 output을 플랫폼 risk signal contract로 정규화한다.

현재 Prompt detector generation은 설정상 `max_tokens=1`, `temperature=0`으로 고정된다.

### Shared GPU Boundary

기준 환경에서는 네 vLLM runtime이 하나의 **NVIDIA RTX 6000 Ada Generation 48 GiB GPU**를 공유한다.

각 runtime은 독립된 GPU memory budget을 갖고, 새로운 runtime 기동 시 Admin Sidecar가 전체 budget을 확인한다.

GPU budget 숫자, startup 순서와 실제 검증 기준은 [6. 모델 운영](./06_model_operations.md)에서 다룬다.

### Does Not Own

vLLM runtime은 다음 책임을 소유하지 않는다.

- 외부 인증과 Gateway public API contract
- Runtime start / stop 정책
- GPU eviction 결정
- Risk final signal schema
- Retrieval cosine similarity 계산
- 외부 exposure 정책

### Failure Impact

| Runtime 장애 | 영향 |
|---|---|
| Main Model | Chat / Multimodal 중단 |
| Embedding | `local-embed` 요청 중단 |
| Korean Embedding | `local-embed-ko`와 기본 Retrieval 중단 |
| Prompt Risk | Prompt detector 실패; PII / Secret local detector는 독립 실행 가능 |

---

## 3.5 Risk Adapter

이 문서에서 **Prompt Guard**는 하나의 단일 container 이름이 아니라 Gateway의 Risk API, **Risk Adapter**, 그리고 Prompt detector용 **Prompt Risk Runtime**이 함께 제공하는 기능 영역을 의미한다.

실제 서비스 경계는 다음과 같다.

```text
Gateway Risk API
      │
      ▼
Risk Adapter :9405
      │
      ├─ PII Detector      ── local
      ├─ Secret Detector   ── local
      └─ Prompt Detector
              │
              ▼
       risk-prompt-vllm :9403
```

`risk-adapter`와 `risk-prompt-vllm`은 서로 다른 서비스다.

### Risk Adapter Purpose

Risk Adapter는 detector별 구현 차이를 숨기고 결과를 공통 **risk signal contract**로 정규화한다.

| Detector | 실행 방식 | 주요 역할 |
|---|---|---|
| **PII** | Risk Adapter process 내부 local detector | 개인정보 노출 signal 탐지 |
| **Secret** | Risk Adapter process 내부 local detector | credential / secret 노출 signal 탐지 |
| **Prompt** | `risk-prompt-vllm` 호출 | Prompt attack signal 탐지 |

### Local Detector와 Prompt Runtime

PII와 Secret detector는 별도 model runtime을 호출하지 않는다.

Prompt detector만 vLLM runtime을 사용한다.

```text
Risk Adapter
  │
  ├─ PII     → Local Detector
  ├─ Secret  → Local Detector
  └─ Prompt  → risk-prompt-vllm
```

따라서 `risk-prompt-vllm` 장애가 PII와 Secret detector의 직접적인 실행 장애를 의미하지는 않는다.

### Risk Signal Contract

Risk Adapter 결과는 크게 detector category와 system signal로 구분된다.

```text
Assessment
  │
  ├─ categories
  │    └─ detector가 발견한 risk signal
  │
  └─ system_signals
       └─ timeout, parse failure, input guard 등 시스템 상태
```

inference 또는 parse 실패를 정상적인 SAFE 결과로 바꾸지 않고 system signal로 표현한다.

### Signal-Only Boundary

Risk Adapter는 **최종 정책 결정 엔진이 아니다.**

설정에서 다음과 같은 policy field를 response에 포함하지 않도록 제한한다.

```text
allow
review
block
decision
action
safe_to_send
final_decision
final_decision_owner
policy_overrides
```

즉 책임 범위는 다음까지다.

```text
Detect → Normalize → Return Signal
```

최종 allow / block / review 정책은 caller 또는 별도의 policy layer가 소유한다.

```text
Risk Signal ≠ Final Policy Decision
```

### Aggregate Assessment

Risk Adapter는 개별 detector API와 aggregate assessment를 제공한다.

현재 aggregate detector order는 다음과 같다.

```text
PII → Secret → Prompt
```

aggregate는 순차 실행되며 detector 일부가 실패하면 system signal과 함께 `partial` 상태가 될 수 있다.

### Input Guard

Risk Adapter는 detector 호출 전에 입력 길이 정책을 적용한다.

입력이 detector 처리 범위를 초과하면 설정된 정책에 따라 detector 호출을 생략하고 `TRUNCATED_INPUT` system signal을 반환할 수 있다.

### Interfaces

Risk Adapter의 주요 내부 API는 다음과 같다.

```text
/v1/risk/detectors/pii/assessments
/v1/risk/detectors/secret/assessments
/v1/risk/detectors/prompt/assessments
/v1/risk/assessments
```

standard private topology에서는 Risk Adapter를 host에 직접 publish하지 않고 Gateway가 내부 network를 통해 호출한다.

### Does Not Own

Risk Adapter는 다음 책임을 소유하지 않는다.

- 사용자 요청의 최종 allow / block policy
- Chat 요청에 Risk 검사를 자동 강제하는 policy gate
- Main Model inference
- Runtime container lifecycle
- GPU budget eviction
- 외부 Client API의 최종 진입점

현재 Risk 기능은 signal 제공 기능이며 Chat 요청을 자동으로 차단하는 inline policy gate가 아니다.

### Dependencies

| Dependency | 사용 목적 |
|---|---|
| Prompt Risk Runtime | Prompt detector inference |
| Risk configuration | detector enablement, signal code, order, timeout, input policy |
| Internal auth policy | Gateway ↔ Risk Adapter 인증 |

### Failure Impact

| 장애 | 영향 |
|---|---|
| Risk Adapter 장애 | Gateway의 모든 Risk API 사용 불가 |
| Prompt Risk Runtime 장애 | Prompt detector 실패; aggregate는 partial 가능 |
| PII local detector 장애 | PII detector 실패 signal 발생 |
| Secret local detector 장애 | Secret detector 실패 signal 발생 |

### 구현 위치

| 영역 | 주요 위치 |
|---|---|
| Risk Adapter application | `src/ai_model_serving/apps/risk_adapter.py` |
| Risk API | `src/ai_model_serving/api/routers/risk_adapter_risk.py` |
| Assessment orchestration | `src/ai_model_serving/services/risk_assessment.py` |
| PII detector | `src/ai_model_serving/detectors/pii.py` |
| Secret detector | `src/ai_model_serving/detectors/secret.py` |
| Risk normalization | `src/ai_model_serving/risk.py` |

---

## 3.6 관측성 스택

관측성 스택은 플랫폼의 요청 처리와 model inference 경로에서 발생하는 **metrics, logs, GPU 상태와 container resource 상태**를 수집하고 조회한다.

```text
Gateway ──────────────┐
Risk Adapter ─────────┤
vLLM Runtimes ────────┤
DCGM Exporter ────────┤
cAdvisor ─────────────┤
                      ▼
                 Prometheus
                      │
                      ▼
                   Grafana

Container Logs
      │
      ▼
    Alloy
      │
      ▼
     Loki
      │
      ▼
   Grafana
```

### Prometheus

Prometheus는 서비스와 runtime의 metrics를 scrape하고 시계열 데이터를 저장한다.

| Source | 주요 관측 내용 |
|---|---|
| Gateway | request, latency, validation rejection, upstream error, streaming, readiness |
| Risk Adapter | assessment, latency, risk signal, system signal |
| vLLM Runtime | request rate, latency, token throughput, KV cache, queue |
| DCGM Exporter | GPU memory, utilization, temperature, power |
| cAdvisor | container CPU, memory, network, OOM / restart signal |

### Grafana

Grafana는 Prometheus와 Loki를 datasource로 사용해 metrics와 logs를 시각화한다.

```text
Grafana
  ├─ Prometheus → Metrics
  └─ Loki       → Logs
```

reference dashboard는 repository의 JSON artifact로 관리하고 provisioning으로 로드한다.

주요 관측 항목은 다음과 같다.

- GPU memory headroom
- GPU utilization
- OOM / container restart signal
- VRAM used vs budget
- vLLM queue / KV cache
- Token throughput
- Requests by model
- Validation rejection / upstream error

### DCGM Exporter

DCGM Exporter는 NVIDIA GPU telemetry를 Prometheus metric으로 제공한다.

```text
GPU Memory Used / Total
GPU Utilization
GPU Temperature
GPU Power
GPU Memory Headroom
```

여러 runtime이 하나의 GPU를 공유하므로 전체 memory headroom은 runtime 안정성 판단의 핵심 지표다.

### cAdvisor

cAdvisor는 container 단위 resource metric을 제공한다.

```text
Container CPU
Container Memory
Container Network
OOM Event
Container Start / Restart Signal
```

vLLM container 집계에는 임의의 container name보다 Compose service label을 사용한다.

### Loki와 Alloy

Loki는 application / container logs의 저장과 조회를 담당하고 Alloy는 container logs를 수집해 Loki로 전달한다.

```text
Container Logs → Alloy → Loki → Grafana
```

Alloy 자체는 사용자가 조회하는 API component가 아니므로 host-published 대상이 아니다.

Admin Sidecar는 실행 중인 container 정보를 바탕으로 Alloy log target manifest를 갱신한다.

### Metrics Privacy Boundary

관측성 metric에는 raw prompt나 model output text를 label로 넣지 않는다.

현재 monitoring contract에서 다음 정보는 metric label 사용이 금지된다.

```text
Raw Prompt
User Text
Model Output Text
```

endpoint, service, logical model ID, status code, risk code, reason, runtime service 같은 운영 메타데이터를 중심으로 사용한다.

### Does Not Own

관측성 스택은 다음 책임을 소유하지 않는다.

- API request routing
- Runtime lifecycle 제어
- Model switching
- GPU budget admission 결정
- Risk policy decision

현재 reference 구성에는 Alertmanager 기반 on-call routing을 기본 계약으로 포함하지 않는다. alert ownership과 escalation 정책은 별도 운영 결정이 필요하다.

### Failure Impact

| 장애 | 주요 영향 |
|---|---|
| Prometheus 장애 | metrics 수집·조회 중단 |
| Grafana 장애 | dashboard 조회 중단 |
| Loki 장애 | 중앙 log 조회 중단 |
| Alloy 장애 | 신규 container log 수집 중단 |
| DCGM Exporter 장애 | GPU telemetry 손실 |
| cAdvisor 장애 | container telemetry 손실 |

관측성 장애는 serving path와 분리되어 있지만 GPU headroom이나 OOM 전조를 볼 수 없게 되므로 운영 위험은 증가한다.

---

## 컴포넌트 책임 경계

플랫폼 전체 책임을 기준으로 보면 다음과 같이 정리할 수 있다.

| Responsibility | Owner |
|---|---|
| 외부 API 진입점 | **Gateway** |
| Client 인증 | **Gateway** |
| Request contract validation | **Gateway** |
| Main Model gate | **Gateway + Admin Sidecar** |
| Chat inference | **Main Model Runtime** |
| Embedding inference | **Embedding Runtime** |
| Retrieval orchestration / cosine | **Gateway** |
| PII detection | **Risk Adapter local detector** |
| Secret detection | **Risk Adapter local detector** |
| Prompt risk inference | **Prompt Risk Runtime** |
| Risk output normalization | **Risk Adapter** |
| 최종 allow / block policy | **플랫폼 외부 Policy Owner** |
| Container lifecycle | **Admin Sidecar** |
| Main model profile switch | **Admin Sidecar** |
| GPU budget admission | **Admin Sidecar** |
| Docker socket access | **Admin Sidecar** |
| Model weight loading | **vLLM Runtime** |
| Metrics storage | **Prometheus** |
| Dashboard | **Grafana** |
| Log collection | **Alloy** |
| Log storage | **Loki** |
| GPU telemetry | **DCGM Exporter** |
| Container telemetry | **cAdvisor** |

### Trust Boundary

보안 관점의 핵심 경계는 다음과 같다.

```text
External Client
      │
      ▼
[ Gateway API Boundary ]
      │
      ├──────────────► [ Model Data Plane ]
      │                    vLLM Runtimes
      │
      ├──────────────► [ Risk Service Boundary ]
      │                    Risk Adapter
      │
      └──────────────► [ Control Plane Boundary ]
                           Admin Sidecar
                              │
                              ▼
                    [ Docker Privilege Boundary ]
```

Gateway는 외부 요청을 받지만 Docker privilege를 갖지 않는다.

Admin Sidecar는 Docker privilege를 갖지만 외부 public API로 노출하지 않는다.

vLLM runtime과 Risk Adapter도 standard private topology에서는 Gateway 뒤의 내부 service로 유지한다.

이 구조의 목적은 **API boundary, model data plane, privileged control plane을 분리하는 것**이다.

### Source of Truth 연결

각 컴포넌트의 실제 동작은 다음 설정과 연결된다.

| 영역 | 주요 Source |
|---|---|
| Service / Port registry | `configs/services.yaml` |
| Model runtime | `configs/model_serving.yaml` |
| Main model profile | `configs/main_model_profiles.yaml` |
| GPU budget | `configs/gpu_budgets.yaml` |
| Exposure | `configs/exposure_profiles.yaml` |
| Authentication | `configs/auth_profiles.yaml` |
| Deployment runtime profile | `configs/deploy_profiles.yaml` |
| Monitoring | `configs/monitoring.yaml` |
| Gateway API contract | `specs/openapi.gateway.yaml` |
| Risk Adapter API contract | `specs/openapi.risk-adapter.yaml` |

이 문서에서는 설정이 어떤 컴포넌트에 영향을 주는지만 설명한다. 설정 우선순위, environment override와 생성 artifact 관계는 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 다룬다.

---

## 다음 문서

시스템 구성 요소의 책임을 이해한 다음에는 각 서비스가 실제 환경에서 어떻게 실행되고 노출되는지 확인한다.

→ [4. 실행 환경과 모드](./04_runtime_modes.md)
