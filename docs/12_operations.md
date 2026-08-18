# 12. 운영 관리 및 장애 대응

운영 중 이상이 발생하면 서비스 상태부터 확인하고, 문제가 발생한 기능과 로그, 모델·GPU 상태 순서로 범위를 좁힌다.

```text
이상 감지
   ↓
서비스 상태 확인
   ↓
문제가 발생한 기능 확인
   ↓
관련 로그 확인
   ↓
모델 / GPU 상태 확인
   ↓
복구 작업
   ↓
정상 동작 확인
```

일상적인 지표와 대시보드는 [11. 관측성](./11_observability.md), Main Model 전환과 Runtime 제어는 [6. 모델 운영](./06_model_operations.md), 배포 복구 구조는 [10. 배포](./10_deployment.md)에서 설명한다.

---

## 12.1 운영 점검 흐름

장애 확인은 현재 서비스 상태에서 시작한다.

| 단계 | 확인 내용 | 주요 수단 |
|---|---|---|
| 1. 서비스 상태 | Gateway와 Risk Adapter 실행 상태 | `make status`, `/health` |
| 2. 주요 기능 준비 상태 | Main Model, Embedding, Risk Runtime 상태 | `/ready`, `make ready-full` |
| 3. 요청과 오류 | 실패한 API, 응답 코드, 오류 코드, 응답 시간 | Request Log Explorer |
| 4. 모델 Runtime | Main / Secondary Runtime 실행 상태 | Grafana, Runtime 상태 API |
| 5. GPU / 컨테이너 | GPU 메모리, 요청 대기량, OOM(메모리 부족), 재시작 | Grafana, `make compose-diagnostics` |
| 6. 상세 로그 | 서비스별 오류와 traceback | Loki / Grafana, `make compose-logs` |
| 7. 복구 확인 | 전체 준비 상태와 대표 API 요청 | `make ready-full`, `make runtime-validate` |

---

## 12.2 서비스 상태 확인

### 기본 상태

app-only 환경에서는 다음 명령으로 Gateway와 Risk Adapter 상태를 확인한다.

```bash
make status
make ready-local
```

full-stack 환경에서는 Main Model과 Embedding 등 주요 Runtime 준비 상태까지 함께 확인한다.

```bash
READY_MODE=full make status
make ready-full
```

| 확인 항목 | 의미 |
|---|---|
| `/health` | 해당 서비스 프로세스가 요청에 응답하는 상태 |
| `/ready` | 주요 Runtime을 포함한 요청 처리 준비 상태 |
| `make ready-local` | app-only Gateway / Risk Adapter 상태 확인 |
| `make ready-full` | full-stack Runtime 준비 상태와 대표 추론 경로 확인 |

Gateway `/ready`가 `503`을 반환하면 응답에서 준비되지 않은 Runtime을 확인한다. `make ready-full`은 모델 로딩 중인 서비스와 아직 준비되지 않은 Runtime을 함께 표시한다.

### Full-stack 진단

`make ready-full` 실패 시 Compose 진단 정보가 자동으로 수집된다. 동일한 진단을 수동으로 다시 실행할 수 있다.

```bash
make compose-diagnostics
```

이 명령은 주요 서비스 상태와 최근 로그를 함께 출력하고, vLLM 설정 오류, GPU 메모리 부족, Engine 초기화 실패 등 주요 Runtime 오류 패턴을 확인한다.

---

## 12.3 요청과 오류 추적

서비스가 실행 중이어도 특정 API 또는 특정 요청에서만 문제가 발생할 수 있다. Request Log Explorer에서는 요청 단위로 범위를 좁힌다.

```text
오류 요청 확인
      ↓
Request ID / Route 확인
      ↓
Status Code / Error Code 확인
      ↓
응답 시간과 대상 Runtime 확인
      ↓
관련 서비스 로그 조회
```

| 조회 기준 | 확인 내용 |
|---|---|
| Request ID | 특정 요청과 관련 로그 연결 |
| Route | 영향받은 API 범위 |
| Status Code | HTTP 응답 상태 |
| Error Code | 애플리케이션 오류 유형 |
| `Latency` | 요청 응답 시간 |
| Token Usage | Chat 요청 처리량 변화 |
| Service / Runtime | 연결된 서비스에서 오류가 발생한 위치 |

특정 요청의 오류가 확인되면 동일 시간대의 Gateway 로그와 해당 Runtime 로그를 함께 확인한다.

API Error Code와 응답 형식은 [API Reference](./reference/api_reference.md)를 참고한다.

---

## 12.4 Model Runtime과 GPU 상태

모델 요청 문제는 응답 시간, 요청 대기량, GPU 자원 순서로 확인한다.

### 응답 시간이 증가한 경우

```text
Gateway 응답 시간
      ↓
모델 요청 대기량
      ↓
GPU 사용량 / 메모리
      ↓
필요 시 KV Cache 상태 확인
```

Grafana의 `vLLM Queue Depth`는 모델 요청 대기량을, `KV Cache Pressure`는 모델 처리에 사용하는 캐시 메모리 상태를 보여준다. 요청 대기량이 계속 증가하면 GPU 사용률과 메모리 여유를 함께 확인한다.

### Runtime 시작이 실패한 경우

```text
Runtime 상태
   ↓
GPU 메모리 여유
   ↓
관련 Runtime 설정
   ↓
Runtime 로그
```

Runtime 기동 명령과 GPU 자원 정책은 [6. 모델 운영](./06_model_operations.md)에 정리되어 있다. 실제
Main Model profile·gate·컨테이너 관측 상태는 `GET /admin/main-model`에서 확인한다.

### OOM 또는 컨테이너 재시작

Grafana에서 GPU Memory와 OOM / Restart 지표를 확인한 뒤 Compose 진단을 실행한다.

```bash
make compose-diagnostics
```

### 주요 Runtime 오류 메시지

Compose 진단과 Runtime 로그에서 자주 확인하는 메시지는 다음과 같다.

| 로그 메시지 | 확인 영역 |
|---|---|
| `No available memory for the cache blocks` | KV Cache와 GPU 메모리 여유 |
| `Engine core initialization failed` | GPU OOM, Runtime 초기화 상태 |
| `max_num_batched_tokens ... smaller than max_model_len` | vLLM batching 설정 |
| `kv-cache is not supported with fp8 checkpoints` | Runtime 이미지와 KV Cache 설정 조합 |
| container restart / OOM | GPU와 시스템 메모리 사용량 |

세부 Runtime 설정은 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 확인한다.

---

## 12.5 주요 장애 상황

대표적인 증상과 첫 확인 지점은 다음과 같다.

| 상황 | 우선 확인 | 다음 단계 |
|---|---|---|
| Gateway 접근 실패 | `make status`, Gateway `/health` | Gateway 로그와 Compose 상태 확인 |
| `/ready`가 `503` | 응답에서 준비되지 않은 Runtime | 해당 Runtime 상태와 로그 확인 |
| `make ready-full` 대기 시간 초과 | 모델 로딩 또는 Runtime 재시작 상태 | `make compose-diagnostics` |
| Chat 요청 실패 | Main Model Runtime 상태 | Main Model 로그와 모델 전환 상태 확인 |
| Embedding / Retrieval 실패 | Embedding Runtime 상태 | 해당 Runtime 실행 상태와 로그 확인 |
| Risk 요청 실패 | Risk Adapter와 Prompt Risk Runtime | Risk Adapter / risk-prompt 로그 확인 |
| 응답 지연 증가 | 응답 시간, 요청 대기량, GPU | Request Log와 Runtime 대시보드 확인 |
| OOM / 반복 재시작 | GPU 메모리와 여유 공간 | Runtime 설정과 GPU 자원 정책 확인 |
| 401 / 403 증가 | 인증 설정과 현재 환경 | `make auth-status`, `make auth-doctor` |
| 모니터링 데이터 누락 | Prometheus 수집 대상과 모니터링 서비스 상태 | Prometheus, Alloy, Loki 로그 확인 |

### 모델 로딩 시간이 긴 경우

최초 기동이나 새 모델 준비 과정에서는 Hugging Face 다운로드, 캐시 생성, Runtime 초기화로 준비 시간이 길어질 수 있다.

`make ready-full`은 모델과 주요 Runtime의 준비 상태를 주기적으로 표시한다. 동일 Runtime이 계속 같은 상태에 머물거나 컨테이너가 반복 재시작하면 해당 로그를 확인한다.

필요한 경우 준비 상태 확인 대기 시간을 실행 환경에 맞게 조정할 수 있다.

```bash
READY_FULL_TIMEOUT_SECONDS=2700 make ready-full
```

대기 시간 조정은 모델 로딩이 실제로 진행 중인 경우에 사용한다.

---

## 12.6 모델 전환 문제

Main Model 전환 문제는 전환 진행 상태와 현재 Main Model 상태를 함께 확인한다.

```text
모델 전환 이상
      ↓
전환 진행 상태
      ↓
현재 Main Model 상태
      ↓
Admin Sidecar / Main Model 로그
      ↓
복구 결과 확인
      ↓
Gateway 준비 상태 확인
```

전환 작업은 작업 ID(`operation_id`)로 조회한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/main-model/operations/<operation_id>
```

전체 Runtime 상태와 GPU 자원 사용량은 다음 API에서 확인한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/runtimes
```

상세 확인 항목은 다음과 같다.

- 현재 활성 프로파일과 Main Model 실행 상태
- 모델 전환의 현재 단계와 오류 정보
- GPU 자원 사용량과 사용 가능한 여유
- 자동 복구(rollback) 수행 여부와 결과
- Gateway `/ready` 상태

모델 전환 단계와 시작·중지, 자동 복구 동작은 [6. 모델 운영](./06_model_operations.md)에서 상세히 설명한다.

---

## 12.7 배포 후 이상 상태

배포 직후 문제는 현재 Release, 서비스 상태, Runtime 준비 상태 순서로 확인한다.

```text
배포 결과 확인
      ↓
현재 Release
      ↓
서비스 상태
      ↓
Gateway /health
      ↓
make ready-full
      ↓
관련 요청 / Runtime 로그
```

배포 실패로 자동 복구가 수행된 경우에는 다음 항목을 확인한다.

- 이전 Release 활성화 여부
- 대상 `.env`와 Runtime 상태가 이전 상태로 복원되었는지
- Gateway와 주요 서비스 상태
- Main Model과 Secondary Runtime 상태
- `make ready-full` 결과

Release 구조와 자동 복구 범위는 [10. 배포](./10_deployment.md)에 정리되어 있다.

배포 후 설정과 Compose 상태를 함께 확인할 때는 다음 명령을 사용한다.

```bash
make compose-config
make compose-diagnostics
```

---

## 12.8 로그 상세 확인

Request Log Explorer에서 대상 요청이나 서비스를 식별한 뒤 원본 로그(Raw Log)에서 애플리케이션과 Runtime 오류를 상세 확인한다.

![Request Log Explorer - Raw Logs](../assets/screenshots/request_log_explorer_raw_logs.png)

원본 로그 화면에서는 Gateway, Risk Adapter, vLLM Runtime 등 서비스별 로그와 traceback을 확인할 수 있다.

### Compose 로그

full-stack 서비스 로그는 다음 명령으로 조회한다.

```bash
make compose-logs
```

특정 서비스만 확인할 수 있다.

```bash
bash scripts/compose/compose_logs.sh main-llm-vllm
```

또는 Docker Compose에서 직접 대상 Runtime의 최근 로그를 조회한다.

```bash
docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env \
  logs --tail=160 main-llm-vllm
```

```bash
docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env \
  logs --tail=160 embedding-vllm
```

### app-only 로그

app-only 실행 로그는 다음 명령으로 확인한다.

```bash
make logs
```

로그에서는 Request ID, Route, Status Code, `Latency`(응답 시간), Service, Error Code 등의 정보를 사용해 요청을 추적한다. Prompt 원문, API key, Authorization header와 같은 민감 정보는 운영 로그 확인 과정에서도 별도로 노출하지 않는다.

---

## 12.9 인증과 노출 설정 확인

401 / 403 오류가 증가하거나 예상과 다른 호스트 포트가 노출되면 현재 Auth / Exposure 설정을 확인한다.

```bash
make auth-status
make auth-doctor
make exposure-status
```

설정 적용 전 결과는 plan 명령으로 미리 확인할 수 있다.

```bash
make auth-plan MODE=<auth-mode>
make exposure-plan MODE=<exposure-mode>
```

Auth mode와 Exposure mode의 설정 구조는 [5. 설정 체계와 Source of Truth](./05_configuration.md), 네트워크 노출 방식은 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 설명한다.

---

## 12.10 복구 후 검증

복구 작업 후에는 서비스 상태와 실제 요청 경로를 다시 확인한다.

```text
복구 작업
   ↓
서비스 상태
   ↓
주요 기능 준비 상태
   ↓
Runtime 검증
   ↓
대표 API 요청
   ↓
지표 / 로그 확인
```

full-stack 환경에서는 다음 순서로 확인한다.

```bash
READY_MODE=full make status
make ready-full
make runtime-validate
```

대표 API 요청만 별도로 확인할 때는 다음 명령을 사용한다.

```bash
make smoke
```

복구 완료는 다음 상태를 기준으로 한다.

- 필요한 서비스가 실행 중
- Gateway `/health` 정상
- 주요 Runtime이 요청 처리 준비 상태
- 대표 Chat / Embedding / Risk 요청 성공
- GPU와 Runtime 지표가 정상 범위
- 동일 Error Code 또는 Runtime crash 재발 없음

---

## 12.11 주요 운영 명령

### 자주 사용하는 명령

| 목적 | 명령 |
|---|---|
| 서비스 상태 확인 | `make status` |
| 전체 Runtime 준비 상태 확인 | `make ready-full` |
| 대표 API 요청 확인 | `make smoke` |
| Compose 상태와 로그 진단 | `make compose-diagnostics` |
| Compose 로그 조회 | `make compose-logs` |
| Runtime 검증 | `make runtime-validate` |

### 추가 진단 명령

| 목적 | 명령 |
|---|---|
| app-only 준비 상태 | `make ready-local` |
| full-stack 상태 요약 | `READY_MODE=full make status` |
| app-only 로그 조회 | `make logs` |
| 인증 상태 확인 | `make auth-status` |
| 인증 진단 | `make auth-doctor` |
| 네트워크 노출 상태 확인 | `make exposure-status` |

명령의 검증 범위는 [8. 테스트와 검증](./08_testing_validation.md), 실행 환경별 차이는 [4. 실행 환경과 모드](./04_runtime_modes.md)를 참고한다.

---

## 12.12 주요 파일과 관련 문서

| 영역 | 주요 파일 | 역할 |
|---|---|---|
| 전체 준비 상태 | `scripts/ops/ready_full.sh` | 주요 Runtime 대기, 추론 준비 확인, 실패 진단 |
| 대표 API 확인 | `scripts/ops/smoke_test.sh` | 대표 API 요청 검증 |
| Compose 진단 | `scripts/compose/compose_diagnostics.sh` | 서비스 상태와 주요 Runtime 오류 패턴 확인 |
| Compose 로그 | `scripts/compose/compose_logs.sh` | full-stack 로그 조회 |
| Runtime 검증 | `scripts/validation/runtime_validation.py` | vLLM API·monitoring 실제 연결 검증 |
| Runtime 제어 | Gateway Runtime Control API | Main / Secondary Runtime 상태 확인과 제어 |

관련 문서는 다음과 연결된다.

- [4. 실행 환경과 모드](./04_runtime_modes.md)
- [5. 설정 체계와 Source of Truth](./05_configuration.md)
- [6. 모델 운영](./06_model_operations.md)
- [8. 테스트와 검증](./08_testing_validation.md)
- [10. 배포](./10_deployment.md)
- [11. 관측성](./11_observability.md)
- [API Reference](./reference/api_reference.md)
