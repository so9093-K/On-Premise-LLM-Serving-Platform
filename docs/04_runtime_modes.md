# 4. 실행 환경과 모드

AI Model Serving Platform은 개발 목적의 **app-only**와 실제 모델 runtime을 포함하는 **full-stack** 두 가지 실행 방식을 제공한다.

- **app-only**: Gateway와 Risk Adapter 중심의 application 개발 환경
- **full-stack**: vLLM runtime, Admin / Control Sidecar, observability를 포함한 전체 서빙 환경

이 문서는 각 서비스가 실제로 어떻게 실행되고 연결되는지, 그리고 어떤 기준으로 준비 상태를 판단하는지를 설명한다.

구성 요소별 책임은 [3. 시스템 구성](./03_system_components.md), 설정 파일과 값의 우선순위는 [5. 설정 체계와 Source of Truth](./05_configuration.md), 모델 전환과 GPU 운영 절차는 [6. 모델 운영](./06_model_operations.md)에서 다룬다.

---

## 4.1 실행 모드

### app-only

app-only는 GPU와 vLLM runtime 없이 Gateway와 Risk Adapter를 로컬 process로 실행하는 개발 모드다.

```text
Developer Host
│
├─ Gateway       localhost:9400
└─ Risk Adapter  localhost:9405
```

주요 실행 명령은 다음과 같다.

```bash
make init-env-local
make start
make ready-local
```

app-only는 다음 작업에 적합하다.

- Gateway / Risk Adapter startup 확인
- API routing과 request validation 개발
- 인증과 error mapping 로직 확인
- OpenAPI / schema 개발
- mock 또는 별도 upstream을 이용한 application 테스트

실제 model loading, GPU resource, vLLM inference, Main Model lifecycle 검증은 full-stack에서 수행한다.

app-only 환경은 `.env.local.example`을 기반으로 생성하며 localhost endpoint를 사용한다.

---

### full-stack

full-stack은 Docker Compose를 사용해 application, model runtime, control plane, observability를 함께 실행한다.

```text
Client
  │
  ▼
Gateway
  │
  ├─ Main Model Runtime
  ├─ Embedding Runtimes
  ├─ Risk Adapter ── Prompt Risk Runtime
  └─ Admin / Control Sidecar ── Docker Engine

Observability
  ├─ Prometheus / Grafana
  ├─ DCGM / cAdvisor
  └─ Loki / Alloy
```

기본 실행 흐름은 다음과 같다.

```bash
make init-env-compose
make compose-up
make ready-full
```

`make compose-up`은 환경 검증, exposure profile 적용, Main Model boot projection 준비, Compose preflight를 수행한 뒤 stack을 기동한다.

Main Model의 실제 실행 profile은 persisted runtime state와 boot policy를 반영해 결정된다.

---

## 4.2 Full-stack 실행 구조

full-stack의 base Compose 정의는 `ops/compose/full-stack.private-network.yaml`에 있다.

| Compose Service | Container Port | 역할 |
|---|---:|---|
| `gateway` | `9400` | 외부 API 진입점 |
| `admin-sidecar` | `8080` | Main Model runtime control |
| `main-llm-vllm` | `9401` | Chat / Multimodal inference |
| `embedding-vllm` | `9402` | 범용 embedding |
| `risk-prompt-vllm` | `9403` | Prompt risk inference |
| `risk-adapter` | `9405` | Risk signal 처리 |
| `embedding-ko-vllm` | `9406` | Korean retrieval embedding |
| `prometheus` | `9090` | Metrics backend |
| `grafana` | `3000` | Dashboard |
| `dcgm-exporter` | `9400` | GPU metrics |
| `cadvisor` | `8080` | Container metrics |
| `loki` | `3100` | Log backend |
| `alloy` | - | Docker log 수집 |

위 표의 port는 **container 내부 port**다. Host에서 접근 가능한 port는 exposure mode에 따라 달라진다.

application과 model runtime은 서로 다른 image 계층으로 실행된다.

```text
Platform Image
  ├─ gateway
  ├─ risk-adapter
  └─ admin-sidecar

vLLM Runtime Image
  ├─ main-llm-vllm
  ├─ embedding-vllm
  ├─ embedding-ko-vllm
  └─ risk-prompt-vllm
```

각 model runtime은 독립 container와 port를 사용하며, GPU resource는 활성 runtime 사이에서 공유된다.

---

## 4.3 네트워크와 서비스 노출

Container 내부 통신과 Host 노출은 별도로 관리한다.

```text
Container Port
  ├─ Compose network 내부 통신
  └─ Host publish 여부는 Exposure Profile이 결정
```

플랫폼은 `private_network`와 `master_open` exposure mode를 사용한다.

| Exposure Mode | 목적 | Host-published 서비스 |
|---|---|---|
| `private_network` | Gateway 중심의 private topology | Gateway, Grafana |
| `master_open` | 신뢰된 네트워크에서의 진단·직접 접근 | 주요 application, runtime, observability endpoint |

### `private_network`

`private_network`에서는 model runtime과 내부 service가 Compose network 안에서 통신하고, 외부 요청은 Gateway를 통해 진입한다.

```text
Host Network
├─ Gateway
└─ Grafana

Compose Network
├─ main-llm-vllm
├─ embedding-vllm
├─ embedding-ko-vllm
├─ risk-prompt-vllm
├─ risk-adapter
├─ admin-sidecar
├─ prometheus
├─ dcgm-exporter
├─ cadvisor
├─ loki
└─ alloy
```

### `master_open`

`master_open`은 주요 runtime과 운영 endpoint를 Host에 publish한다.

| Host Port | Service |
|---:|---|
| `9400` | Gateway |
| `9401` | Main Model Runtime |
| `9402` | Embedding Runtime |
| `9403` | Prompt Risk Runtime |
| `9405` | Risk Adapter |
| `9406` | Korean Embedding Runtime |
| `9410` | Prometheus |
| `9411` | Grafana |
| `9412` | DCGM Exporter |
| `9413` | cAdvisor |
| `9414` | Loki |

`master_open`은 model runtime과 운영 endpoint에 직접 접근해야 하는 진단 환경에서 사용한다. 실제 접근 범위는 `EXPOSURE_AUDIENCE`와 네트워크 정책으로 제한한다.

### Effective Compose 구성

`ops/compose/full-stack.private-network.yaml`은 base Compose 정의이며, 최종 Host exposure는 `EXPOSURE_MODE`를 적용한 effective Compose config로 결정된다.

```text
Base Compose
    │
    ├─ private_network
    │    └─ base topology 사용
    │
    └─ master_open
         └─ exposure.master-open.yaml 결합
    │
    ▼
Effective Compose Config
```

현재 적용된 노출 상태는 다음 명령으로 확인할 수 있다.

```bash
make exposure-status
make compose-config
```

`.env.compose.example`은 개발용 초기값으로 `AUTH_MODE=local_open`, `EXPOSURE_MODE=master_open`, `EXPOSURE_AUDIENCE=private_lan`을 제공한다. 배포 환경에서는 대상 네트워크에 맞는 auth와 exposure profile을 지정한다.

`AUTH_MODE`는 **누가 호출할 수 있는지**, `EXPOSURE_MODE`는 **어떤 서비스가 host network에 공개되는지**를 각각 결정한다. 한 profile이 다른 profile을 대체하지 않는다. `/health`는 liveness probe로 인증 없이 둘 수 있지만, `/ready`, `/metrics`, `/admin/*`는 auth profile과 network boundary를 함께 적용한다. `/docs`, `/redoc`, `/openapi.json`의 공개 여부도 auth profile의 docs 정책을 따른다.

---

## 4.4 기동 순서와 Runtime 의존성

full-stack은 서비스 의존성과 공유 GPU 초기화 순서를 고려해 runtime을 기동한다.

대표적인 vLLM startup 순서는 다음과 같다.

```text
main-llm-vllm
      │ healthy
      ▼
embedding-vllm
      │ healthy
      ▼
embedding-ko-vllm
      │ healthy
      ▼
risk-prompt-vllm
```

vLLM runtime은 초기화 과정에서 GPU memory를 확인하고 runtime memory를 구성한다. 순차 기동은 여러 runtime이 동일 GPU를 사용할 때 초기화 경쟁을 줄이는 역할을 한다.

Gateway는 기본적으로 `risk-adapter`와 `main-llm-vllm`의 상태를 기준으로 기동되며, Admin Sidecar는 Gateway의 hard startup dependency로 두지 않는다.

따라서 Sidecar 장애는 Main Model control에 영향을 주지만 Gateway process 자체의 기동과 직접 결합되지는 않는다.

Prometheus, Grafana, Loki, Alloy 등 observability 계층은 serving path와 독립적으로 운영된다.

---

## 4.5 Health와 Readiness

플랫폼은 process 생존 상태와 실제 serving 가능 상태를 구분한다.

```text
Process Start
    │
    ▼
/health
    │
    ▼
Dependency Ready
    │
    ▼
/ready
    │
    ▼
Inference / Smoke Validation
```

### `/health`

`/health`는 해당 process의 liveness를 확인한다.

Gateway `/health`가 성공해도 model runtime이나 다른 dependency의 준비 상태까지 보장하지는 않는다.

### `/ready`

Gateway `/ready`는 현재 요청 처리에 필요한 dependency 상태를 확인한다.

필수 dependency가 준비되지 않은 경우 HTTP `503`을 반환하며 응답에서 readiness 상태와 준비되지 않은 dependency를 확인할 수 있다.

```text
status: not_ready
phase: waiting_for_dependencies
not_ready_dependencies: [...]
required_not_ready_dependencies: [...]
optional_not_ready_dependencies: [...]
```

Deploy Runtime Profile에서 stopped 또는 deferred로 지정된 secondary runtime은 optional dependency로 처리될 수 있다.

### `make ready-local`

app-only 환경의 application process를 확인한다.

```bash
make ready-local
```

검증 대상은 Gateway와 Risk Adapter의 `/health`다.

### `make ready-full`

full-stack의 실제 serving 가능 상태를 확인한다.

```bash
make ready-full
```

주요 검증 단계는 다음과 같다.

```text
Gateway /ready
      │
      ▼
Dependency Ready
      │
      ▼
Main Model Gate
      │
      ▼
Smoke Validation
Strict Smoke Validation
```

| 확인 방법 | 의미 |
|---|---|
| `/health` | process가 살아 있음 |
| `/ready` | 필요한 dependency가 ready |
| `make ready-full` | main-model gate와 대표 inference 경로가 실제로 동작함 |

`ready-full`은 실패를 무시하는 별도 inference warmup을 수행하지 않는다. Smoke가
Chat(Structured Output 포함), Risk, 일반 Embedding, Korean Embedding 경로를 실제
요청으로 검증하며, 실패하면 full-stack readiness도 실패한다.

---

## 4.6 Runtime 운영 상태

### Main Model

Main Model은 profile 전환이 가능한 runtime이며 Gateway, Admin Sidecar, vLLM Runtime이 역할을 나누어 관리한다.

```text
Gateway
  └─ Chat gate / in-flight request tracking

Admin Sidecar
  └─ drain / container lifecycle / validation / rollback

Main Model vLLM
  └─ model load / inference
```

모델 전환 시 신규 Chat 요청을 제어하고, 기존 요청 drain과 runtime 재기동 및 검증을 거친다.

세부 switch API와 rollback 절차는 [6. 모델 운영](./06_model_operations.md)에서 설명한다.

### Secondary Runtime

Embedding과 Prompt Risk runtime은 Deploy Runtime Profile에 따라 active 또는 deferred 상태로 운영할 수 있다.

현재 secondary runtime control 대상은 다음과 같다.

- `embedding`
- `embedding_ko`
- `risk_prompt`

대표 profile은 다음과 같다.

| Deploy Runtime Profile | 실행 상태 |
|---|---|
| `main_only` | Main Model 중심, secondary runtime deferred |
| `retrieval_ready` | Main + embedding 계열 준비, Prompt Risk deferred |

Deploy Runtime Profile과 Exposure Profile의 역할은 다르다.

```text
Deploy Runtime Profile
  └─ 어떤 runtime을 실행할 것인가

Exposure Profile
  └─ 실행된 service를 어디까지 노출할 것인가
```

예를 들어 `main_only`와 `private_network`를 함께 사용할 수 있다.

---

## 4.7 공유 GPU 실행 모델

기본 runtime 구성에서는 여러 vLLM process가 하나의 NVIDIA GPU를 공유한다.

```text
NVIDIA GPU
├─ Main Model
├─ Embedding
├─ Embedding-KO
└─ Prompt Risk
```

각 runtime은 독립 process와 container로 실행되지만 GPU memory는 공용 resource다.

Runtime 시작과 Main Model 전환 시에는 현재 활성화된 runtime의 GPU budget을 함께 확인한다. Admin Sidecar는 runtime activation 전에 GPU admission을 수행한다.

실제 VRAM budget, priority, eviction 정책은 [6. 모델 운영](./06_model_operations.md)과 `configs/gpu_budgets.yaml`에서 다룬다.

---

## 4.8 실행 모드 선택

작업 목적에 따라 실행 모드를 선택한다.

| 작업 | 권장 모드 | 주요 확인 |
|---|---|---|
| Gateway / Risk Adapter 개발 | app-only | `make ready-local` |
| API contract / validation 개발 | app-only | test + `make ready-local` |
| 실제 Chat inference | full-stack | `make ready-full` |
| Embedding / Retrieval 검증 | full-stack | `make ready-full` |
| Prompt Risk runtime 검증 | full-stack | `make ready-full` |
| Main Model switch | full-stack | Model Operations 검증 |
| GPU budget 변경 | full-stack | Runtime / GPU validation |
| Compose / exposure 변경 | full-stack | `make compose-config`, `make exposure-status` |
| Observability 검증 | full-stack | Prometheus / Grafana / Loki 확인 |

실행 환경과 관련된 주요 source of truth는 다음과 같다.

| 영역                      | 주요 파일                                         | 용도                                  |
| ----------------------- | --------------------------------------------- | ----------------------------------- |
| Base Compose topology   | `ops/compose/full-stack.private-network.yaml` | 전체 서비스의 기본 컨테이너 구성과 연결 관계 정의        |
| Service / port registry | `configs/services.yaml`                       | 서비스 이름, 포트, bind 정보 등 서비스 메타데이터 정의  |
| Exposure profile        | `configs/exposure_profiles.yaml`              | 서비스별 host port 공개 범위 정의             |
| Deploy Runtime Profile  | `configs/deploy_profiles.yaml`                | 배포 시 활성화할 secondary runtime 조합 정의   |
| Model runtime           | `configs/model_serving.yaml`                  | 모델 runtime 연결, 제한값 및 serving 정책 정의  |
| Main Model profile      | `configs/main_model_profiles.yaml`            | Main Model별 runtime 및 실행 profile 정의 |
| GPU budget              | `configs/gpu_budgets.yaml`                    | GPU별 runtime 자원 사용 한도 정의            |
| Compose environment     | `.env.compose.example`                        | full-stack Compose 실행에 필요한 환경변수 예시  |
| Local environment       | `.env.local.example`                          | app-only 로컬 실행에 필요한 환경변수 예시         |


각 설정의 우선순위와 변경 반영 범위는 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 이어서 설명한다.
