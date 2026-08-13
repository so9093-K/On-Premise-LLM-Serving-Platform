# 11. 관측성

운영 중인 플랫폼 상태는 서비스 지표, 모델 Runtime과 GPU 자원, 요청 로그를 함께 확인한다.

```text
Service / Runtime
      │
      ├─ Metrics ──→ Prometheus ──→ Grafana
      │
      └─ Logs ─────→ Alloy ───────→ Loki ──→ Grafana
```

배포 직후 상태 확인은 [10. 배포](./10_deployment.md), 검증 명령과 Runtime 검증 범위는 [8. 테스트와 검증](./08_testing_validation.md)에서 설명한다.

---

## 11.1 모니터링 구성

플랫폼의 모니터링 구성은 Metrics와 Logs 두 경로로 나뉜다.

| 구성요소 | 역할 |
|---|---|
| Gateway / Risk Adapter | 요청, 오류, 지연 시간, 준비 상태 등의 서비스 지표 제공 |
| vLLM Runtime | 모델 요청량, 지연 시간, Token 처리량, Queue, KV Cache 지표 제공 |
| DCGM Exporter | GPU 메모리, 사용률, 온도, 전력 등 GPU 지표 제공 |
| cAdvisor | 컨테이너 CPU, 메모리, OOM, 재시작 관련 지표 제공 |
| Prometheus | 서비스와 Runtime 지표 수집·저장 |
| Alloy | 컨테이너 로그 수집 |
| Loki | 로그 저장·검색 |
| Grafana | Metrics와 Logs 통합 조회 |

`private_network` 구성에서는 Grafana가 운영 조회 화면으로 host에 공개되고, Prometheus, DCGM Exporter, cAdvisor, Loki, Alloy는 Compose 내부 네트워크에서 동작한다. 외부 노출 방식은 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 다룬다.

---

## 11.2 데이터 수집 흐름

### Metrics

```text
Gateway ───────────────┐
Risk Adapter ──────────┤
vLLM Runtime ──────────┤
DCGM Exporter ─────────┤──→ Prometheus ──→ Grafana
cAdvisor ──────────────┘
```

Prometheus는 Gateway, Risk Adapter, 각 vLLM Runtime, DCGM Exporter, cAdvisor의 `/metrics`를 주기적으로 수집한다.

vLLM Runtime은 하나의 scrape job으로 수집하고 `model`, `runtime_service` label을 사용해 모델과 실행 서비스를 구분한다.

### Logs

```text
Application / Container Log
          ↓
        Alloy
          ↓
         Loki
          ↓
       Grafana
```

Alloy는 컨테이너 로그 파일을 읽어 Loki로 전달한다. Grafana의 Request Log Explorer에서 Gateway 요청, API 오류, Readiness 실패, Runtime 로그를 함께 조회할 수 있다.

---

## 11.3 서비스 지표

서비스 지표는 요청 흐름과 API 상태를 확인하는 데 사용한다.

| 확인 영역 | 주요 내용 |
|---|---|
| 요청량 | Gateway와 주요 API의 요청 추이 |
| 응답 시간 | HTTP 요청과 upstream 요청의 처리 시간 |
| 오류 | HTTP 오류, upstream 오류, validation rejection |
| 인증 | 인증 실패 발생 추이 |
| 준비 상태 | Gateway와 dependency의 readiness 상태 |
| Streaming | 요청 수, chunk/byte 처리량, 첫 응답 시간, disconnect |

Gateway의 주요 사용자 트래픽은 Chat, Embedding, Risk API를 중심으로 확인한다. `/health`, `/ready`, `/metrics`, API 문서 경로와 같은 운영·제어 요청은 사용자 트래픽 지표와 분리한다.

서비스 상태 이상이 확인되면 요청 지표와 함께 Model Runtime, GPU, 로그를 순서대로 확인한다.

```text
Request / Error 변화
        ↓
Service Latency
        ↓
Model Runtime 상태
        ↓
GPU / Container 자원
        ↓
관련 Request Log
```

---

## 11.4 Model Runtime과 GPU

Model Runtime 상태는 GPU와 컨테이너 자원 지표를 함께 확인한다.

![Grafana Runtime Dashboard](../assets/screenshots/grafana_runtime_dashboard.jpg)

Grafana의 **GPU Capacity and OOM Risk** Dashboard는 모델 Runtime과 GPU 자원 상태를 한 화면에서 제공한다.

| 확인 영역 | 주요 지표 |
|---|---|
| GPU 용량 | GPU Headroom, GPU Memory Used |
| GPU 부하 | GPU Utilization, Temperature, Power |
| 메모리 안정성 | Actual GPU VRAM, OOM / Restarts |
| Runtime 처리 상태 | vLLM Queue Depth, KV Cache Pressure |
| 처리량 | Token Throughput per Model |
| 컨테이너 자원 | System RAM, CPU Cores Used |

### GPU Memory와 Headroom

GPU Memory Used는 실제 GPU 메모리 사용량을 나타내고, GPU Headroom은 현재 사용 가능한 여유 용량을 보여준다.

Main Model 전환이나 Secondary Runtime 시작 전후에는 GPU 사용량과 Headroom 변화를 함께 확인한다. Runtime의 GPU 자원 정책은 [6. 모델 운영](./06_model_operations.md)에서 설명한다.

### Queue와 KV Cache

`vLLM Queue Depth`는 처리 대기 중인 요청 상태를 나타낸다. 요청량 증가와 함께 Queue가 지속적으로 증가하면 Runtime 처리 용량과 응답 시간을 함께 확인한다.

`KV Cache Pressure`는 모델 추론 과정에서 사용하는 KV Cache 상태를 보여준다. Queue, Token Throughput, GPU Memory를 함께 보면 요청 증가와 자원 사용 변화의 관계를 확인할 수 있다.

### OOM과 재시작

OOM과 컨테이너 재시작 신호는 cAdvisor 지표를 기준으로 확인한다.

`0`은 해당 기간에 이벤트가 없음을 나타내고, `No Data`는 exporter 또는 metric 수집 상태 확인이 필요한 경우를 포함한다. Dashboard에서 `No Data`가 표시되면 Prometheus target과 cAdvisor 수집 상태를 함께 확인한다.

---

## 11.5 요청 로그와 오류 추적

Request Log Explorer는 Gateway 요청과 Runtime 로그를 Loki에서 조회한다.

![Request Log Explorer](../assets/screenshots/request_log_explorer_overview.png)

요청 단위 확인에는 다음 필드를 사용한다.

| 조회 기준 | 활용 |
|---|---|
| Request ID | 특정 오류 요청의 처리 흐름 추적 |
| Route | API별 요청 확인 |
| Status Code | 성공 요청과 오류 요청 구분 |
| Error Code | 오류 유형별 요청 확인 |
| Client Host | 호출 대상별 요청 확인 |
| Latency | 응답 지연 요청 확인 |
| Token Usage | Chat 요청의 입력·출력 Token 사용량 확인 |

Request Log Explorer는 다음 영역으로 구성된다.

- **Gateway Request Log**: Gateway와 Risk Adapter의 구조화된 요청 로그
- **API Errors**: HTTP 4xx/5xx 요청
- **Readiness Failures**: `/ready`에서 확인된 dependency 상태
- **Non-JSON Container Errors**: vLLM 등 Runtime의 오류 로그
- **Raw Container Log**: 컨테이너 원본 로그
- **Engine Crash / Traceback**: Runtime crash와 traceback 검색

Request ID 또는 Error Code로 대상을 좁힌 뒤 관련 서비스와 Runtime 로그를 함께 확인할 수 있다.

### 로그 데이터 기준

운영 로그에는 request id, route, status code, latency, service, error code, token 사용량 등 진단에 필요한 정보를 기록한다.

Prompt 원문, 생성 결과, Authorization header, API key, internal token과 같은 민감 정보는 기본 로그와 metric label에 포함하지 않는다.

요청·응답 본문 기록 기능을 명시적으로 활성화한 경우에는 마스킹 처리를 거친 제한된 preview만 기록한다. 관련 설정은 [5. 설정 체계와 Source of Truth](./05_configuration.md)를 참고한다.

`LOG_REQUEST_RESPONSE_BODY`는 기본 `false`다. 이 설정은 운영 진단이 필요한 non-stream 요청의 제한된 preview에만 적용하며, streaming 본문을 수집하는 용도로 사용하지 않는다. Token 사용량, latency, request id, route, status code와 error code는 본문 기록 설정과 독립적으로 남긴다.

---

## 11.6 Grafana Dashboard

Grafana Dashboard는 운영 목적에 따라 구분된다.

| Dashboard | 주요 용도 |
|---|---|
| **GPU Capacity and OOM Risk** | GPU 용량, OOM/재시작, Queue, KV Cache, Token 처리량, 컨테이너 자원 확인 |
| **Usage Today** | GPU workload, 모델별 요청량, rejected request, upstream 오류, Token 처리량 확인 |
| **Request Log Explorer** | 요청 로그, API 오류, Readiness 실패, Runtime 로그 검색 |

기본 Grafana Home Dashboard는 `GPU Capacity and OOM Risk`로 구성된다.

Dashboard JSON은 repository에서 관리한다.

```text
ops/grafana/dashboards/
├─ gpu_capacity_and_oom_risk.json
├─ usage_today.json
└─ request_log_explorer.json
```

운영 Dashboard 변경은 JSON source를 기준으로 반영한다. Grafana UI에서 실험한 변경을 유지할 경우 JSON을 export해 repository에 반영한다.

### Dashboard 비율과 수집 상태 해석

최근 요청이 적은 Dashboard에서 오류율·risk signal 비율은 `rate()` 값만으로 해석하지 않는다. 사람이 최근 window의 실제 이벤트를 읽는 panel은 `increase()`를 사용하고 denominator만 최소 1로 보정한다.

```promql
sum(increase(http_requests_total{service="gateway",status_code=~"5.."}[$window]))
/
clamp_min(sum(increase(http_requests_total{service="gateway"}[$window])), 1)
```

`0`은 exporter가 정상 수집되고 이벤트가 없다는 뜻이고, `No Data`는 exporter·metric·scrape 자체가 없을 수 있다는 뜻이다. OOM·restart처럼 수집 누락을 숨기면 위험한 panel에는 `or vector(0)`로 `No Data`를 강제로 0으로 만들지 않는다. 먼저 Prometheus target과 cAdvisor 수집 상태를 확인한다.

---

## 11.7 주요 확인 지표

일상적인 상태 확인은 서비스, Runtime, GPU, 로그 순서로 진행한다.

### 서비스 상태

| 항목 | 확인 내용 |
|---|---|
| Request Rate | 사용자 요청량 변화 |
| Error | 4xx/5xx 및 upstream 오류 |
| Latency | Gateway와 upstream 응답 시간 |
| Readiness | 주요 dependency 준비 상태 |

### Model Runtime

| 항목 | 확인 내용 |
|---|---|
| Queue Depth | 요청 대기 증가 여부 |
| KV Cache | Cache 사용 압력 |
| Token Throughput | 모델 처리량 변화 |
| Runtime 상태 | 모델 서비스 실행·준비 상태 |

### GPU와 컨테이너

| 항목 | 확인 내용 |
|---|---|
| GPU Memory | 실제 VRAM 사용량 |
| GPU Headroom | 추가 Runtime을 위한 여유 용량 |
| GPU Utilization | GPU 연산 부하 |
| OOM / Restart | 메모리 부족 또는 컨테이너 재시작 신호 |
| CPU / RAM | Runtime별 시스템 자원 사용량 |

### 요청 로그

오류율이나 지연이 증가한 경우 Request ID, Route, Error Code를 기준으로 관련 로그를 확인한다.

---

## 11.8 배포 후 모니터링

배포 완료 후에는 Readiness 결과와 운영 지표를 함께 확인한다.

```text
배포 완료
   ↓
Gateway Health / Readiness
   ↓
Request / Error / Latency
   ↓
Model Runtime 상태
   ↓
GPU Memory / Headroom
   ↓
관련 Request Log
```

### Rolling 배포

Rolling 배포 후에는 Gateway 상태와 요청 흐름을 우선 확인한다.

- Gateway `/health`
- 요청량과 오류율
- 응답 시간
- Gateway / Risk Adapter 관련 로그

### Full 배포

Full 배포 후에는 전체 Runtime 준비 상태와 GPU 자원을 함께 확인한다.

- `make ready-full`
- Main Model과 Secondary Runtime 상태
- GPU Memory / Headroom
- Queue / KV Cache
- OOM / Restart 신호
- 대표 API 요청과 관련 로그

배포 완료 기준과 자동 복구 흐름은 [10. 배포](./10_deployment.md)에 정리되어 있다.

---

## 11.9 상태 이상 확인 흐름

Metrics에서 이상을 확인한 뒤 Request Log와 Runtime 로그로 범위를 좁힌다.

```text
서비스 이상 확인
      ↓
Request / Error / Latency 확인
      ↓
Model Runtime / GPU 확인
      ↓
Request ID / Error Code 확인
      ↓
관련 Container Log 확인
```

| 관찰 결과 | 다음 확인 영역 |
|---|---|
| 오류율 증가 | API Errors, Error Code, upstream 상태 |
| 응답 시간 증가 | Gateway latency, Runtime Queue, GPU 사용률 |
| Queue 증가 | vLLM Runtime, KV Cache, GPU Memory |
| OOM / Restart | GPU Memory, cAdvisor, Runtime 로그 |
| `/ready` 실패 | Readiness Failures와 dependency 상태 |
| 특정 요청 실패 | Request ID 기준 요청·오류 로그 |

세부 장애 대응과 시스템 정리는 [12. 운영 관리](./12_operations.md)에서 이어서 다룬다.

---

## 11.10 설정과 주요 파일

| 영역 | 주요 파일 | 역할 |
|---|---|---|
| Prometheus / live metric 검증 | `configs/monitoring.yaml` | scrape 설정과 필수 service metric 정의 |
| Prometheus | `ops/prometheus/prometheus.yml` | scrape target과 rule 연결 |
| Recording Rule | `ops/prometheus/rules/model_runtime.rules.yml` | Runtime·GPU 운영 지표 계산 |
| Grafana Dashboard | `ops/grafana/dashboards/*.json` | 운영 Dashboard 정의 |
| Grafana Provisioning | `ops/grafana/provisioning/` | datasource와 Dashboard provisioning |
| Loki | `ops/loki/loki-config.yml` | 로그 저장과 retention 설정 |
| Alloy | `ops/alloy/config.alloy` | 컨테이너 로그 수집·전달 |
| Compose | `ops/compose/full-stack.private-network.yaml` | 모니터링 서비스 실행 구성 |

생성되는 Prometheus 설정과 모니터링 projection은 Source of Truth를 기준으로 관리한다. 설정 구조와 generated artifact는 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 설명한다.

주요 운영 문서는 다음과 연결된다.

- [4. 실행 환경과 모드](./04_runtime_modes.md)
- [5. 설정 체계와 Source of Truth](./05_configuration.md)
- [6. 모델 운영](./06_model_operations.md)
- [8. 테스트와 검증](./08_testing_validation.md)
- [10. 배포](./10_deployment.md)
- [12. 운영 관리](./12_operations.md)
