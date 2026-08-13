# 1. 프로젝트 개요

AI Model Serving Platform은 Chat, Embedding, Retrieval, Prompt Guard 기능을 하나의 Gateway API로 제공하는 온프레미스 모델 서빙 플랫폼이다.

모델 inference는 독립된 vLLM runtime으로 실행하고, runtime과 container 제어는 Admin / Control Sidecar로 분리한다. 메트릭과 로그는 별도 관측성 스택에서 수집한다.

## 1.1 프로젝트 배경

모델마다 API 형식, 입력 modality, context, GPU 사용량, runtime option이 다르다. 모델을 추가하거나 교체할 때는 runtime 설정뿐 아니라 GPU resource, validation, image, 배포 구성도 함께 관리해야 한다.

이 플랫폼은 모델별 차이를 Gateway와 설정 체계에서 관리하고, 애플리케이션에는 일관된 API를 제공한다.

```text
Client / Application
        │
        ▼
      Gateway
        │
        ├─ Chat
        ├─ Embedding
        ├─ Retrieval
        └─ Prompt Guard
        │
        ▼
   Model Runtime
        │
        ▼
   GPU Resource
        │
        ▼
Validation / Deployment / Monitoring
```

모델 구성, runtime, GPU budget, 배포와 관측을 하나의 프로젝트 안에서 함께 관리하는 구조를 사용한다.

## 1.2 전체 아키텍처

### 시스템 구성도

![AI 모델 서빙 플랫폼 시스템 구성도](../assets/ai_model_serving_system_architecture.jpg)

위 구성도는 클라이언트 요청이 Gateway를 거쳐 모델 runtime과 Prompt Guard로 전달되고, metrics가 운영·모니터링 계층으로 수집되는 서빙 경로를 보여준다.

Admin / Control Sidecar와 로그 수집 계층까지 포함한 현재 주요 경계는 다음과 같다.

```text
Client / Application
        │
        ▼
┌──────────────────────┐
│       Gateway        │
│        :9400         │
└──────────┬───────────┘
           │
           ├──────────────► main-llm-vllm
           │
           ├──────────────► embedding-vllm
           │
           ├──────────────► embedding-ko-vllm
           │
           └──────────────► Prompt Guard
                                  │
                                  ▼
                           risk-adapter :9405
                                  │
                                  ▼
                           risk-prompt-vllm


┌──────────────────────────┐
│ Admin / Control Sidecar  │
│          :8080           │
└────────────┬─────────────┘
             │
             ├─ Runtime start / stop
             ├─ Main model profile switch
             ├─ GPU budget admission
             └─ Container lifecycle


┌──────────────────────────────────────────────────────────┐
│                    Observability                         │
│ Prometheus · Grafana · Loki · Alloy · DCGM · cAdvisor   │
└──────────────────────────────────────────────────────────┘
```

### 주요 경계

| 구성 요소 | 주요 역할 | 연결 구조 |
|---|---|---|
| **Gateway** | API 인터페이스<br>Request / Response 처리<br>멀티모달 입력 검증<br>Routing / Orchestration | `Client` → `Gateway`<br>→ Model Runtime<br>→ Prompt Guard |
| **Admin / Control Sidecar** | Runtime Lifecycle<br>Main Model 전환<br>GPU Budget Admission<br>Container 제어 | `Gateway` → `Sidecar`<br>→ Docker / Runtime |
| **vLLM Runtime** | Model Load<br>Inference 실행<br>모델별 Runtime 설정 적용 | Main LLM<br>Embedding<br>Embedding-KO<br>Prompt Guard Model |
| **Prompt Guard** | Prompt 검사<br>Detector 호출<br>결과 정규화 | `Gateway` → `risk-adapter`<br>→ `risk-prompt-vllm` |
| **관측성 스택** | Metrics 수집<br>Logs 수집<br>GPU / Container 관측<br>Dashboard | Prometheus · Grafana<br>Loki · Alloy<br>DCGM · cAdvisor |

Gateway와 Admin / Control Sidecar는 역할이 분리되어 있다.

```text
Gateway
  API / Request 처리
  Routing / Orchestration

Admin / Control Sidecar
  Runtime 제어
  GPU Budget 확인
  Container Lifecycle
```

Docker socket은 Admin / Control Sidecar에 연결된다. Gateway는 내부 control API를 통해 runtime 상태 조회와 전환 기능을 사용한다.

vLLM runtime은 모델별 독립 process와 port로 구성된다.

| Runtime | 역할 | 기본 Port |
|---|---|---:|
| `main-llm-vllm` | Chat / Multimodal | `9401` |
| `embedding-vllm` | Embedding | `9402` |
| `embedding-ko-vllm` | Retrieval용 Korean Embedding | `9406` |
| `risk-prompt-vllm` | Prompt Guard Model | `9403` |

Main LLM은 외부에 `local-main` alias를 제공하고, 내부에서는 main model profile을 전환할 수 있다. 한 시점에는 하나의 main model profile이 활성화된다.

## 1.3 주요 특징

### Gateway 중심 API

애플리케이션이 사용하는 모델 기능은 Gateway에서 시작한다.

| 기능 | Gateway API |
|---|---|
| Model Listing | `/v1/models` |
| Chat | `/v1/chat/completions` |
| Embedding | `/v1/embeddings` |
| Retrieval | `/v1/retrieval/*` |
| Prompt Guard | `/v1/risk/*` |

Gateway는 외부 API 형식을 유지하면서 request parameter, 이미지·오디오·비디오 입력, response 형식과 내부 runtime 차이를 처리한다.

![Gateway API Reference](../assets/screenshots/scalar_api_reference.jpg)

*Scalar API Reference에서 Chat, Embedding, Retrieval, Prompt Guard API와 요청·응답 스펙을 확인할 수 있다.*

### Runtime 제어 분리

모델 요청 처리와 runtime 제어를 별도 경계로 구성한다.

| 영역 | 담당 |
|---|---|
| API 요청·Routing | Gateway |
| Runtime 상태·전환 | Admin / Control Sidecar |
| Container Lifecycle | Admin / Control Sidecar |
| Model Inference | vLLM Runtime |

이 구조를 통해 API 처리 영역과 Docker 제어 권한을 분리한다.

### 독립 vLLM Runtime

Main LLM, Embedding, Korean Embedding, Prompt Guard model은 각각 독립된 vLLM process로 실행된다.

모델별로 다음 runtime 값을 별도로 구성할 수 있다.

```text
Model / Revision
Context
Concurrency
GPU Memory Budget
dtype
Quantization
Runtime Flags
```

모든 vLLM 서비스는 공통 `vllm-unified` image를 사용하고, 모델별 차이는 profile과 runtime option으로 구성한다.

### 설정 기반 운영

모델과 runtime 동작에 필요한 주요 값은 repository의 설정 파일에서 관리한다.

| 설정 영역 | 주요 파일 | 역할 |
|---|---|---|
| **Model Catalog** | `configs/model_catalog.yaml` | 사용 가능한 모델과 기본 메타데이터 정의 |
| **Main Model Profile** | `configs/main_model_profiles.yaml` | Main LLM별 runtime profile과 전환 대상 정의 |
| **Model Runtime** | `configs/model_serving.yaml` | 모델별 backend, port, context, concurrency, runtime option 설정 |
| **GPU Budget** | `configs/gpu_budgets.yaml` | Runtime별 GPU memory budget과 전체 사용 한도 관리 |
| **Service / Port** | `configs/services.yaml` | 서비스 이름, 내부 port, 연결 정보 정의 |
| **Exposure** | `configs/exposure_profiles.yaml` | Host publish 여부와 외부 노출 범위 설정 |
| **Authentication** | `configs/auth_profiles.yaml` | API 인증 방식과 인증 profile 설정 |
| **Monitoring** | `configs/monitoring.yaml` | Metrics, logs, dashboard 관련 관측성 설정 |
| **Deploy Profile** | `configs/deploy_profiles.yaml` | 배포 환경별 실행 구성과 적용 profile 정의 |

설정 간 우선순위와 생성 artifact는 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 다룬다.

### 단일 GPU Resource 관리

기준 GPU는 **NVIDIA RTX 6000 Ada Generation 48 GiB**이며, 여러 vLLM runtime이 하나의 GPU를 공유한다.

```text
RTX 6000 Ada 48 GiB
        │
        ├─ Main LLM
        ├─ Embedding
        ├─ Embedding-KO
        └─ Prompt Guard Model
```

각 runtime은 `gpu_memory_utilization` budget을 갖고, Admin / Control Sidecar는 runtime 기동과 전환 시 전체 GPU budget을 확인한다.

구체적인 budget과 기동 순서는 [6. 모델 운영](./06_model_operations.md)에서 다룬다.

### Runtime Artifact 관리

Platform application과 vLLM runtime은 별도 image artifact로 관리한다.

```text
Platform Image
  ├─ Gateway
  ├─ Risk Adapter
  └─ Admin / Control Sidecar

vLLM Unified Image
  ├─ Main LLM
  ├─ Embedding
  ├─ Embedding-KO
  └─ Prompt Guard Model
```

Production 배포에서는 검증된 image digest와 model revision을 기준으로 실행 구성을 고정한다.

### 통합 관측성

API, runtime, GPU, container 상태를 metrics와 logs로 수집한다.

| 영역 | 구성 | 역할 |
|---|---|---|
| **Metrics** | Prometheus | 서비스와 runtime의 메트릭 수집·저장 |
| **Dashboard** | Grafana | 메트릭과 로그를 대시보드로 시각화 |
| **Logs** | Loki | 애플리케이션과 container 로그 저장·조회 |
| **Log Collection** | Alloy | container 로그 수집 후 Loki로 전달 |
| **GPU** | DCGM Exporter | GPU 사용률, 메모리, 온도, 전력 메트릭 수집 |
| **Container** | cAdvisor | container CPU, 메모리, 네트워크 사용량 수집 |

Grafana dashboard와 request log 조회 방법은 [11. 관측성과 장애 대응](./11_observability.md)에서 다룬다.

## 1.4 플랫폼 범위

### 제공 기능

| 기능 | 설명 |
|---|---|
| **Chat** | `local-main` 기반 Chat Completions |
| **Embedding** | 범용 text embedding |
| **Retrieval** | Korean embedding 기반 score / rerank |
| **Prompt Guard** | Prompt 검사와 detector 결과 정규화 |
| **Runtime Control** | Runtime 상태 조회, 기동·중지, main model profile 전환 |
| **Observability** | API·model runtime·GPU·container metrics와 logs |

### 실행 모드

| 모드 | 구성 |
|---|---|
| **app-only** | Gateway와 Prompt Guard application service를 로컬 process로 실행 |
| **full-stack** | Gateway, Sidecar, vLLM runtime, Prompt Guard, 관측성 스택을 Docker Compose로 실행 |

실행 방법과 readiness 기준은 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 다룬다.

### 배포 경계

운영 권장 topology인 `EXPOSURE_MODE=private_network`에서는 Gateway와 Grafana를 host에 publish하고, model runtime과 운영 backend는 Compose network 내부에 둔다.

```text
Host Published
  ├─ Gateway
  └─ Grafana

Compose Internal
  ├─ Admin / Control Sidecar
  ├─ Risk Adapter
  ├─ Main vLLM
  ├─ Embedding vLLM
  ├─ Embedding-KO vLLM
  ├─ Prompt Guard vLLM
  ├─ Prometheus
  ├─ Loki
  ├─ Alloy
  ├─ DCGM Exporter
  └─ cAdvisor
```

애플리케이션의 모델 기능은 Gateway API를 기준 인터페이스로 사용한다.

## 다음 문서

- [2. 요청 처리 흐름](./02_request_flow.md)
- [3. 시스템 구성](./03_system_components.md)
- [4. 실행 환경과 모드](./04_runtime_modes.md)
