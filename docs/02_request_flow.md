# 2. 요청 처리 흐름

클라이언트 요청은 Gateway를 기준 진입점으로 사용한다. Gateway는 요청 스펙과 runtime 상태를 확인한 뒤 기능에 맞는 내부 서비스로 요청을 전달하고, 응답을 외부 API 형식으로 반환한다.

```text
Client / Application
        │
        ▼
      Gateway
        │
        ├─ Chat ───────────────► Main vLLM
        ├─ Embedding ──────────► Embedding vLLM
        ├─ Retrieval ──────────► Embedding-KO vLLM
        └─ Prompt Guard ───────► Risk Adapter ─► risk-prompt vLLM
```

Admin 요청도 Gateway에서 시작하지만 inference 경로와 분리되어 Admin / Control Sidecar로 전달된다.

```text
Operator
   │
   ▼
Gateway /admin/*
   │
   ▼
Admin / Control Sidecar
   │
   ▼
Docker / Runtime Lifecycle
```

## 2.1 클라이언트 요청 흐름

### 외부 API 진입점

Gateway는 외부 클라이언트가 사용하는 모델 API와 운영 API를 구분해 제공한다.

| 구분 | 주요 API | 처리 대상 |
|---|---|---|
| **Models** | `GET /v1/models` | 사용 가능한 logical model과 capability 조회 |
| **Chat** | `POST /v1/chat/completions` | `local-main` |
| **Embedding** | `POST /v1/embeddings` | `local-embed`, `local-embed-ko` |
| **Retrieval** | `POST /v1/retrieval/score`<br>`POST /v1/retrieval/rerank` | Embedding 기반 dense cosine score |
| **Prompt Guard** | `POST /v1/risk/*` | `risk-adapter` / `risk-prompt-vllm` |
| **Runtime Control** | `/admin/runtimes*`<br>`/admin/main-model*` | Runtime 상태와 Main Model 전환 |
| **Operations** | `/health`<br>`/ready`<br>`/metrics` | Liveness, readiness, metrics |

`GET /v1/models`는 logical model catalog를 반환하며, runtime readiness는 `/ready`에서 별도로 확인한다.

일반 모델 호출은 다음 흐름을 따른다.

```text
Client
  │
  │ HTTP Request
  ▼
Gateway :9400
  │
  ├─ Request Body Guard
  ├─ API / Admin 인증
  ├─ Request Contract 검증
  ├─ Runtime 상태 확인
  ├─ 기능별 Routing / Orchestration
  │
  ▼
Internal Service / vLLM Runtime
  │
  ├─ Inference / Detection
  └─ Internal Response
  │
  ▼
Gateway
  │
  ├─ Response Validation 검증
  ├─ Error Mapping
  ├─ Metrics / Request Log
  │
  ▼
Client
```

Gateway 앞단에서 공통 request body 크기 제한을 적용하고, route별 인증과 요청 스펙을 확인한다. Upstream 호출에는 concurrency admission, queue timeout, circuit breaker와 request timeout이 적용된다.

### 공통 처리 항목

| 처리 | 역할 |
|---|---|
| **Request Body Guard** | 전체 HTTP body 크기 제한 |
| **Authentication** | Public API, Admin API, Internal Service 경계별 인증 적용 |
| **Request Validation** | 모델 ID, 입력 형식, request parameter, 기능별 제한 검증 |
| **Runtime State** | 중지·기동 중인 runtime과 Main Model 전환 상태 확인 |
| **Upstream Admission** | 모델별 동시 처리 수와 queue timeout 관리 |
| **Circuit Breaker** | 반복되는 upstream 장애 시 일시적인 요청 차단 |
| **Timeout** | Gateway와 내부 runtime의 응답 시간 제한 |
| **Response Validation** | 내부 응답이 Gateway API 스펙과 일치하는지 확인 |
| **Metrics / Logs** | HTTP 상태, latency, token usage, upstream 상태 기록 |

주요 오류는 모든 기능에서 동일한 platform error envelope로 반환된다.

| HTTP | 대표 상황 |
|---:|---|
| `401` | API 또는 Admin 인증 실패 |
| `422` | 요청 스펙 또는 모델 capability 불일치 |
| `502` | Upstream 오류 또는 응답 스펙 불일치 |
| `503` | Runtime 중지·전환 중, queue timeout, circuit open |
| `504` | Gateway 또는 upstream timeout |

## 2.2 Gateway 처리 흐름

Gateway는 외부 API 형식을 내부 runtime 호출로 변환하는 애플리케이션 경계다.

```text
                  ┌─────────────────────────┐
Client ──────────►│         Gateway         │
                  │                         │
                  │  Request Validation     │
                  │  Runtime State          │
                  │  Routing                │
                  │  Orchestration          │
                  │  Response Validation    │
                  └────────────┬────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
           vLLM          Risk Adapter       Sidecar
        Inference        Detection          Control
```

### 요청 검증

Gateway는 route별 요청 스펙을 기준으로 입력을 검증한다.

| 기능 | 주요 검증 |
|---|---|
| **Chat** | `local-main` model ID, messages, token limit, 지원 parameter, 활성 modality |
| **Embedding** | Model ID, input, dimensions, 지원 parameter |
| **Retrieval** | Query, documents, model, score mode, document 수 |
| **Prompt Guard** | `{prompt}` request 형식과 prompt 길이 |
| **Runtime Control** | Service key, desired state, model profile, switch 요청 값 |

Chat의 이미지·오디오·비디오 입력은 활성 Main Model이 제공하는 modality와 입력별 크기·형식 제한을 함께 확인한다.

### Runtime 상태 확인

Runtime 상태 확인 방식은 기능에 따라 다르다.

| 기능 | 상태 확인 |
|---|---|
| **Chat** | Admin Sidecar에서 활성 Main Model profile과 gate 확인 |
| **Embedding** | Gateway runtime state에서 대상 embedding runtime 확인 |
| **Retrieval** | Gateway runtime state에서 선택한 embedding runtime 확인 |
| **Prompt Guard** | Prompt model을 사용하는 경로에서 `risk_prompt` runtime 상태 확인 |

Main Model 전환 중에는 Chat gate가 닫히며 신규 Chat 요청은 `MAIN_MODEL_SWITCH_IN_PROGRESS`로 응답한다. Sidecar에 접근할 수 없는 경우 Chat은 `MAIN_MODEL_CONTROL_UNAVAILABLE`로 응답한다.

Embedding, Retrieval, Prompt Guard는 각 기능에 필요한 runtime의 상태를 독립적으로 확인한다.

### Upstream 호출

Gateway의 vLLM client는 모델별 admission 설정을 사용한다.

```text
Gateway
   │
   ├─ Concurrency Slot 확인
   │
   ├─ Queue Timeout
   │
   ├─ Circuit Breaker
   │
   ▼
vLLM / Internal Service
```

동시 처리 slot을 확보하지 못하면 `QUEUE_TIMEOUT`, 연속 upstream 실패로 circuit이 열린 경우 `CIRCUIT_OPEN`을 반환한다.

### 응답 처리

Internal service 응답은 기능별 응답 스펙으로 다시 검증한다.

```text
Internal Response
       │
       ▼
Response Validation
       │
       ├─ 정상 ─► Metrics / Logs ─► Client
       │
       └─ 오류 ─► Platform Error ─► Client
```

Chat과 Embedding 응답은 model, choice/data 구조, token usage 등 기능별 형식을 확인한다. Prompt Guard 응답은 detector signal 형식을 확인한 뒤 Gateway를 통해 반환한다.

## 2.3 기능별 처리 흐름

### Chat

Chat 요청은 `local-main`을 통해 현재 활성 Main Model runtime으로 전달된다.

```text
Client
  │
  │ POST /v1/chat/completions
  ▼
Gateway
  │
  ├─ Main Model in-flight 등록
  │
  ├─ Sidecar에서 active profile / gate 조회
  │
  ├─ 현재 deployed modality 확인
  │
  ├─ Chat Request 검증
  │
  ├─ Runtime용 Request 정규화
  │
  ▼
main-llm-vllm :9401
  │
  │ POST /v1/chat/completions
  ▼
Main Model
  │
  ▼
Gateway
  │
  ├─ Chat Response 검증
  ├─ Token Usage / Metrics 기록
  │
  ▼
Client
```

#### Main Model Gate

Chat은 요청마다 Admin Sidecar의 Main Model 상태를 확인한다.

```text
Admin Sidecar
   │
   ├─ active_profile
   ├─ gate
   └─ last_operation
```

`gate=open`인 경우 현재 profile의 `deployed_input`을 Chat validation에 사용한다. 모델 전환 중에는 gate가 닫혀 신규 요청 유입을 멈추고, 진행 중인 요청 수는 Gateway의 in-flight counter로 추적한다.

Main Model profile이 바뀌어도 외부 model ID는 `local-main`을 유지한다.

#### 멀티모달 입력

현재 기본 Main Model profile은 text, image, audio, video 입력을 제공한다.

```text
messages[].content
   │
   ├─ text
   ├─ image_url
   ├─ input_audio
   └─ video_url
        │
        ▼
Gateway Input Validation
        │
        ▼
main-llm-vllm
```

Gateway는 현재 활성 profile의 modality와 `configs/model_serving.yaml`의 입력 제한을 조합해 허용 범위를 결정한다.

#### Streaming

`stream=true` 요청은 vLLM SSE 응답을 Gateway가 chunk 단위로 relay한다.

```text
Client
  │
  │ stream=true
  ▼
Gateway
  │
  ▼
main-llm-vllm
  │
  │ SSE chunks
  ▼
Gateway
  │
  │ text/event-stream
  ▼
Client
```

Streaming 경로에도 동일한 request validation, concurrency admission, circuit breaker가 적용된다. Gateway는 upstream SSE chunk를 수신하는 즉시 순서대로 전달한다.

### Embedding

Embedding API는 요청의 `model` 값으로 사용할 runtime을 선택한다.

```text
Client
  │
  │ POST /v1/embeddings
  ▼
Gateway
  │
  ├─ Model 선택
  ├─ Runtime 상태 확인
  ├─ Embedding Request 검증
  ├─ Runtime용 Request 정규화
  │
  ├──────── local-embed ──────► embedding-vllm :9402
  │
  └──────── local-embed-ko ───► embedding-ko-vllm :9406
                                   │
                                   ▼
                              Embedding Vector
                                   │
                                   ▼
                                Gateway
                                   │
                                   ▼
                                 Client
```

| Model | Runtime | 기본 Dimension | 용도 |
|---|---|---:|---|
| `local-embed` | `embedding-vllm:9402` | `768` | 범용 Embedding |
| `local-embed-ko` | `embedding-ko-vllm:9406` | `1024` | Korean Retrieval |

`model`을 생략하면 `local-embed`가 기본 Embedding model로 선택된다.

Gateway는 지원 dimension과 request parameter를 확인하고, runtime 응답의 vector 개수와 dimension도 다시 검증한다.

### Retrieval

Retrieval은 Gateway 내부 `RetrievalService`가 query와 documents의 embedding을 생성한 뒤 cosine similarity를 계산한다.

```text
Client
  │
  │ /v1/retrieval/score
  │ /v1/retrieval/rerank
  ▼
Gateway
  │
  ▼
RetrievalService
  │
  ├─ Request 검증
  ├─ Query Prompt Policy 적용
  ├─ Document Prompt Policy 적용
  │
  ├─ Query Embedding
  │        │
  │        ▼
  │   embedding-ko-vllm
  │
  ├─ Document Embedding
  │        │
  │        ▼
  │   embedding-ko-vllm
  │
  ├─ Cosine Similarity
  │
  ├─ score  → 입력 순서 유지
  └─ rerank → score 내림차순 정렬
           │
           ▼
        Client
```

기본 Retrieval model은 `local-embed-ko`다.

| API | 결과 |
|---|---|
| `/v1/retrieval/score` | 입력 document 순서를 유지하면서 각 cosine score 반환 |
| `/v1/retrieval/rerank` | cosine score 기준으로 document를 정렬하고 `top_n` 적용 가능 |

현재 Retrieval은 요청에 포함된 query와 documents를 즉시 embedding하고 Gateway process에서 cosine similarity를 계산하는 stateless 구조다.

`local-embed-ko`의 기본 prompt policy는 query에 `query: ` prefix를 적용하고 document는 원문을 사용한다.

### Prompt Guard

Prompt Guard 관련 요청은 Gateway에서 Risk Adapter로 전달된다.

```text
Client
  │
  │ POST /v1/risk/detectors/prompt/assessments
  ▼
Gateway
  │
  ├─ Prompt Request 검증
  ├─ risk_prompt Runtime 상태 확인
  ├─ Internal Service Token 전달
  │
  ▼
risk-adapter :9405
  │
  ├─ Prompt Detector 선택
  │
  ├─ Detector Input Guard
  │
  ▼
risk-prompt-vllm :9403
  │
  │ POST /v1/chat/completions
  │ max_tokens=1
  │ temperature=0
  ▼
Kanana Safeguard-Prompt
  │
  │ <SAFE>
  │ <UNSAFE-A1>
  │ <UNSAFE-A2>
  ▼
Risk Adapter
  │
  ├─ Label Parsing
  ├─ Signal 정규화
  │
  ▼
Gateway
  │
  ├─ Risk Response 검증
  │
  ▼
Client
```

Prompt detector는 Kanana Safeguard-Prompt가 생성한 단일 label을 A1 Prompt Injection, A2 Prompt Leaking signal로 변환한다.

현재 `/v1/risk/*` endpoint group에는 Prompt Guard와 함께 ~~local PII/Secret detector~~, aggregate endpoint가 구성되어 있다.

| API | Backend |
|---|---|
| `/v1/risk/detectors/prompt/assessments` | Kanana `risk-prompt-vllm` |
| ~~`/v1/risk/detectors/pii/assessments`~~ | Risk Adapter in-process PII detector |
| ~~`/v1/risk/detectors/secret/assessments`~~ | Risk Adapter in-process Secret detector |
| `/v1/risk/assessments` | ~~PII → Secret~~ → Prompt 순차 처리 |

Aggregate 요청은 세 detector 결과를 하나의 signal response로 합친다. 응답은 탐지 결과와 system signal을 제공하며 최종 허용·차단 판단은 호출 측 policy layer가 담당한다.

## 2.4 외부 공개 경로와 내부 서비스 경로

### Service 연결

Gateway 뒤의 서비스는 Compose DNS와 container port로 연결된다.

| 호출 | 내부 주소 | 용도 |
|---|---|---|
| Client → Gateway | Host `:9400` | 외부 API 진입점 |
| Gateway → Main vLLM | `http://main-llm-vllm:9401/v1` | Chat |
| Gateway → Embedding vLLM | `http://embedding-vllm:9402/v1` | 범용 Embedding |
| Gateway → Embedding-KO vLLM | `http://embedding-ko-vllm:9406/v1` | Korean Embedding / Retrieval |
| Gateway → Risk Adapter | `http://risk-adapter:9405` | Prompt Guard / Risk 요청 |
| Risk Adapter → Prompt vLLM | `http://risk-prompt-vllm:9403/v1` | Prompt attack detector |
| Gateway → Admin Sidecar | `http://admin-sidecar:8080` | Runtime / Main Model control |

### `private_network`

`private_network` exposure profile에서는 Gateway와 Grafana만 host에 publish한다.

```text
Host
  │
  ├─ :9400  Gateway
  └─ :9411  Grafana

Compose Network
  │
  ├─ admin-sidecar :8080
  ├─ risk-adapter :9405
  ├─ main-llm-vllm :9401
  ├─ embedding-vllm :9402
  ├─ embedding-ko-vllm :9406
  ├─ risk-prompt-vllm :9403
  └─ Prometheus / Loki / Alloy / DCGM / cAdvisor
```

애플리케이션 요청은 Gateway를 통해 내부 runtime으로 전달된다.

### `master_open`

`master_open`은 신뢰된 사내망에서 runtime과 운영 endpoint를 직접 진단할 수 있도록 추가 port를 host에 publish한다.

```text
Gateway         :9400
Main vLLM       :9401
Embedding       :9402
Prompt vLLM     :9403
Risk Adapter    :9405
Embedding-KO    :9406
Prometheus      :9410
Grafana         :9411
DCGM Exporter   :9412
cAdvisor        :9413
Loki            :9414
```

Admin / Control Sidecar는 `master_open`에서도 Compose 내부 `:8080` 경계를 유지한다.

제품·애플리케이션 연동은 Gateway API를 사용해 request validation, runtime routing, response validation을 적용한다. `master_open`의 직접 runtime port는 진단 경로로 사용한다.

### Admin Control 경로

운영 요청은 Gateway의 `/admin/*` API에서 Sidecar 내부 API로 변환된다.

```text
Operator
   │
   │ /admin/runtimes/*
   │ /admin/main-model/*
   ▼
Gateway :9400
   │
   │ Internal Control Request
   ▼
Admin Sidecar :8080
   │
   ├─ GPU Budget
   ├─ Runtime Start / Stop
   ├─ Main Model Switch
   └─ Operation Status
   │
   ▼
Docker / Runtime
```

Docker socket과 container control은 Admin / Control Sidecar에 집중된다.

## 2.5 인증·권한 경계

권한 경계는 **Public API**, **Admin API**, **Internal Service** 세 surface로 나뉜다. 실제 인증 활성화 여부는 `AUTH_MODE`와 해당 profile에서 파생된 환경변수로 결정된다.

```text
Client
  │
  │ Public API Auth
  ▼
Gateway /v1/*
  │
  ├──────── Internal Service Auth ───────► Risk Adapter
  │
  └──────── Internal Control Auth ───────► Admin Sidecar

Operator
  │
  │ Admin Auth
  ▼
Gateway /ready /metrics /admin/*
```

### Endpoint 경계

| 경계 | Endpoint | 인증 설정 |
|---|---|---|
| **Liveness** | `/health` | 인증 없이 liveness 확인 |
| **Public API** | `/v1/*` | `API_KEY_REQUIRED`에 따라 API Bearer token 적용 |
| **Admin / Operations** | `/ready`, `/metrics`, `/admin/*` | `ADMIN_API_KEY_REQUIRED`에 따라 Admin Bearer token 적용 |
| **Gateway Internal** | `/internal/main-model/drain-status` | `INTERNAL_SERVICE_AUTH_REQUIRED`에 따라 Internal Service token 적용 |
| **Gateway → Risk Adapter** | Risk Adapter `/v1/risk/*` | `INTERNAL_SERVICE_AUTH_REQUIRED`에 따라 Internal Service token 적용 |
| **Gateway → Sidecar** | Sidecar control API | `INTERNAL_SERVICE_TOKEN`으로 내부 호출 보호 |

Gateway의 `/v1/*`와 `/admin/*`는 서로 다른 bearer credential을 사용할 수 있다.

### 인증 설정 연결

인증 동작은 `configs/auth_profiles.yaml`의 profile과 환경변수 조합으로 결정된다.

| 경계 | 주요 설정 |
|---|---|
| Public API | `AUTH_MODE`, `API_KEY_REQUIRED`, `API_KEYS` |
| Admin / Operations | `ADMIN_API_KEY_REQUIRED`, `ADMIN_API_KEY(S)`, `ADMIN_ENDPOINTS_INTERNAL_ONLY` |
| Internal Service | `INTERNAL_SERVICE_AUTH_REQUIRED`, `INTERNAL_SERVICE_TOKEN` |
| Exposure | `EXPOSURE_MODE`, `EXPOSURE_AUDIENCE` |

Repository baseline은 `AUTH_MODE=local_open`이며, 실제 배포에서는 적용된 `.env`와 exposure profile이 요청 접근 범위를 결정한다.

### 요청 경계 정리

```text
Public Client
    │
    ▼
Gateway
    │
    ├─ API 스펙
    ├─ 기능별 Routing
    ├─ Runtime 상태
    └─ Response 스펙
         │
         ▼
Compose Internal Services


Operator
    │
    ▼
Gateway Admin API
    │
    ▼
Admin Sidecar
    │
    ▼
Docker / Runtime Control
```

외부 애플리케이션 경로와 runtime 제어 경로가 Gateway에서 분리되고, Docker 제어 권한은 Sidecar 내부 경계에 집중된다.

## 다음 문서

- [3. 시스템 구성](./03_system_components.md)
- [4. 실행 환경과 모드](./04_runtime_modes.md)
- [5. 설정 체계와 Source of Truth](./05_configuration.md)
