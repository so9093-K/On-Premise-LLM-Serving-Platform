# 6. 모델 운영

AI Model Serving Platform의 Main Model 운영은 **현재 상태 확인 → 프로파일 선택 → 모델 전환 → 전환 결과 확인 → 서비스 검증** 순서로 진행한다.

외부 Chat API는 `local-main`이라는 논리 model ID를 유지하고, 실제 Main Model runtime은 선택된 profile에 따라 model, revision, runtime image, vLLM command를 적용한다.

이 장에서는 운영자가 Main Model을 확인하고 시작·중지·전환하는 방법을 설명한다. **6.1~6.8은 일반 운영 흐름**, 6.9 이후는 전환 상태와 runtime 세부 정보를 다룬다. Runtime 실행 구조는 [4. 실행 환경과 모드](./04_runtime_modes.md), profile과 GPU 설정은 [5. 설정 체계와 Source of Truth](./05_configuration.md), Admin API contract는 [API Reference](./reference/api_reference.md)에서 확인한다.

---

## 6.1 모델 운영 흐름

일반적인 Main Model 변경 작업은 다음 순서로 진행한다.

```text
현재 상태 확인
    ↓
전환할 Profile 선택
    ↓
GPU 자원 확인
    ↓
모델 전환 요청
    ↓
전환 상태 확인
    ↓
Gateway Ready 확인
    ↓
Chat Smoke Test
```

Main Model control path는 Gateway와 Admin / Control Sidecar를 사용한다.

```text
Operator
   │
   │ Admin API
   ▼
Gateway :9400
   │
   ▼
Admin / Control Sidecar :8080
   │
   ├─ Main Model state
   ├─ GPU admission
   ├─ request drain
   └─ Docker lifecycle
          │
          ▼
   main-llm-vllm :9401
```

| 계층 | 역할 |
|---|---|
| **Gateway** | Admin API 제공, 인증, control 요청 전달, Chat gate 적용 |
| **Admin / Control Sidecar** | Main Model 상태, 모델 전환, GPU admission, Docker lifecycle 관리 |
| **Main Model Runtime** | 선택된 profile의 vLLM inference 수행 |
| **Main Model State** | active profile, runtime state, gate, 최근 operation 기록 유지 |

모델 profile 변경은 `POST /admin/main-model/switch`, runtime 시작·중지는 `PATCH /admin/runtimes/main`을 사용한다.

---

## 6.2 현재 상태 확인

모델 운영을 시작할 때는 Main Model 상태와 전체 runtime 상태를 먼저 확인한다.

### Main Model 상태

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/main-model
```

주요 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `active_profile` | 마지막으로 검증을 통과해 control-plane에 기록된 Main Model profile |
| `runtime_state` | control-plane에 기록된 lifecycle 상태인 `active` / `stopped` |
| `gate` | 신규 Chat 요청의 `open` / `closed` 상태 |
| `last_known_good_profile` | 마지막으로 정상 검증을 통과한 profile |
| `profile_locked` | Admin API profile 변경 잠금 상태 |
| `last_operation` | 가장 최근 모델 전환 operation |
| `observed_runtime` | 조회 시점 Docker 관측값. 실제 컨테이너 상태·health·컨테이너 설정에서 식별한 profile을 포함 |

`active_profile`과 `runtime_state`는 Docker를 매번 조회해 계산한 값이 아니다. 실제 서비스
가능 여부는 `observed_runtime.status=ready`, `observed_runtime.health=healthy`, 그리고
`gate=open`을 함께 확인한다. Docker를 읽지 못한 경우에는 응답 전체를 성공처럼 보이게
유지하지 않고 `observed_runtime.status=unknown`과 `error`를 반환한다.

### Runtime 상태와 GPU budget

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/runtimes
```

이 API에서는 Main Model과 secondary runtime의 상태, GPU budget 사용량을 함께 확인할 수 있다.

---

## 6.3 모델 프로파일 선택

Main Model Profile은 `local-main` runtime을 어떤 model과 실행 조건으로 구성할지 정의한다.

```text
Main Model Profile
   ├─ model / revision
   ├─ runtime image
   ├─ vLLM command
   ├─ context / concurrency
   ├─ GPU utilization
   ├─ modality capability
   └─ compatibility status
```

Profile의 Source of Truth는 `configs/main_model_profiles.yaml`이다.

현재 사용 가능한 profile은 다음 API로 조회한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/main-model/profiles
```

Profile을 선택할 때는 다음 항목을 확인한다.

- profile ID와 display name
- upstream model과 pinned revision
- runtime image
- GPU VRAM fraction
- compatibility status
- input / output capability
- 현재 active 여부

### Compatibility Status

| 상태 | 운영 의미 |
|---|---|
| `verified` | 현재 정의된 검증 근거를 가진 profile |
| `likely` | 호환 가능성이 높고 추가 검증 근거가 필요한 profile |
| `unverified` | 운영자 확인 후 전환할 수 있는 미검증 profile |
| `unknown` | 호환성 근거가 충분하지 않은 profile |
| `incompatible` | 현재 deployment와 호환되지 않는 profile |

`unverified`와 `unknown` profile은 switch 요청에 `confirm_unverified=true`가 필요하다. `incompatible` profile은 전환 대상에서 제외된다.

### Profile Lock

`MAIN_LLM_PROFILE_LOCKED=true`이면 `MAIN_LLM_BOOT_PROFILE`을 기준으로 Main Model profile을 고정한다. 일반 운영에서는 persisted active profile이 다음 기동에도 이어진다.

관련 설정은 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 설명한다.

---

## 6.4 모델 전환

모델 전환은 **Target 준비 → 기존 요청 Drain → Runtime 교체 → 검증 → 전환 결과 확정** 순서로 진행된다.

```text
Switch 요청
    ↓
Target Profile 준비
    ↓
GPU Admission
    ↓
기존 요청 Drain
    ↓
Runtime 교체
    ↓
Runtime 검증
    ↓
전환 결과 확정
```

### 전환 요청

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile":"<profile-id>"}' \
  http://127.0.0.1:9400/admin/main-model/switch
```

정상적으로 접수되면 `202 Accepted`와 `operation_id`를 반환한다.

```json
{
  "operation_id": "<uuid>",
  "status": "pending",
  "reused": false,
  "message": "..."
}
```

### Target 준비

전환 작업은 target profile의 고정 `model_id + revision` snapshot을 공용 model cache에 준비한다. 이 구간에서는 기존 Main Model과 Chat 요청 처리가 유지된다.

필요하면 전환 전에 model snapshot을 미리 준비할 수 있다.

```bash
make main-model-prepare PROFILE=<profile-id>
```

`HF_CACHE_DIR`은 container의 `HF_HOME`에 mount되는 host root를 뜻한다.
준비 명령과 vLLM은 모두 그 아래 `hub/`를 실제 repository cache로 사용한다.

### 기존 요청 Drain

Target 준비가 끝나면 신규 Chat 요청을 잠시 제한하고, 현재 처리 중인 요청이 완료될 때까지 기다린다.

```text
기존 요청  ─────────────────────► 완료

신규 요청  ── 503 ── 503 ──────► 재개
                 모델 전환        Gate Open
```

이 구간의 신규 Chat 요청은 Gateway에서 `503`과 `Retry-After`로 응답한다.

### Runtime 교체

Drain이 완료되면 기존 `main-llm-vllm` container를 종료하고 target profile의 image와 command로 runtime을 다시 구성한다.

### Runtime 검증

새 runtime이 시작되면 서비스 재개 전에 다음 순서로 검증한다.

```text
Container Health
      ↓
/v1/models
      ↓
Text Inference Canary
      ↓
필요한 Media Canary
      ↓
전환 결과 확정
```

필수 검증이 통과하면 새 profile을 현재 활성 profile로 확정하고 Chat gate를 다시 연다.

---

## 6.5 전환 결과 확인

모델 전환은 비동기 operation이므로 `operation_id`로 결과를 확인한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/main-model/operations/<operation-id>
```

운영 관점에서 중요한 최종 상태는 다음 세 가지다.

| 상태 | 의미 |
|---|---|
| `completed` | target profile 전환 완료 |
| `failed` | target 전환 실패, 필요 시 이전 정상 profile로 복구 완료 |
| `rollback_failed` | target 전환과 이전 profile 복구가 모두 실패 |

전환이 완료되면 Gateway readiness와 실제 Chat 응답을 확인한다.

```bash
curl http://127.0.0.1:9400/ready
```

full-stack 검증에서는 다음 명령을 사용할 수 있다.

```bash
make ready-full
```

마지막으로 실제 Chat inference를 호출해 active Main Model의 응답을 확인한다.

### 재시도 가능한 Switch Request

자동화 환경에서는 `request_id`를 사용해 동일한 switch 요청의 재시도를 안전하게 처리할 수 있다.

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "profile":"<profile-id>",
    "request_id":"deploy-20260811-main-switch"
  }' \
  http://127.0.0.1:9400/admin/main-model/switch
```

유효 기간 안에서 동일 `request_id`와 동일 profile을 다시 요청하면 기존 operation을 반환하고 `reused=true`로 표시한다.

---

## 6.6 GPU 자원 확인

모델을 시작하거나 전환하기 전에 필요한 GPU 자원을 확보할 수 있는지 확인한다. 이 판단 과정이 **GPU Admission**이다.

Main Model과 secondary runtime은 같은 GPU VRAM budget을 사용한다.

```text
GPU Budget
├─ Main Model
├─ Embedding
├─ Korean Embedding
└─ Prompt Risk
```

GPU 상태는 다음 API에서 확인한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/runtimes
```

주요 budget 정보는 `ceiling`, `used`, `free`로 제공된다. 실제 값은 `configs/gpu_budgets.yaml`을 기준으로 한다.

### Admission 결과

| 결과 | 의미 | 운영 방향 |
|---|---|---|
| `fits` | 현재 자원으로 실행 가능 | 시작 또는 전환 진행 |
| `fits after eviction` | 일부 runtime 정리 후 실행 가능 | stop plan 확인 |
| `infeasible` | 현재 budget으로 실행 불가 | profile 또는 runtime 구성 조정 |

Profile switch에서 자원이 부족하면 필요한 runtime stop plan을 확인한 뒤 자원 구성을 조정하고 다시 전환한다.

Runtime start에서는 `force=true`를 사용해 admission planner가 선택한 낮은 priority runtime을 정지하고 공간을 확보할 수 있다.

```bash
curl -X PATCH \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"active","force":true}' \
  http://127.0.0.1:9400/admin/runtimes/main
```

GPU budget과 runtime별 reservation 설정은 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 설명한다.

---

## 6.7 Main Model 시작과 중지

Profile을 유지한 채 Main Model runtime만 시작하거나 중지할 수 있다.

### 중지

```bash
curl -X PATCH \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"stopped"}' \
  http://127.0.0.1:9400/admin/runtimes/main
```

중지는 다음 순서로 진행된다.

```text
Gate Close
   ↓
Request Drain
   ↓
Runtime Stop
   ↓
VRAM Release
   ↓
runtime_state = stopped
```

현재 active profile은 유지되므로 이후 시작 시 같은 profile을 다시 사용할 수 있다.

### 시작

```bash
curl -X PATCH \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"active","force":false}' \
  http://127.0.0.1:9400/admin/runtimes/main
```

시작 시 GPU admission과 runtime validation을 수행한 뒤 Chat gate를 연다.

---

## 6.8 실패와 복구

전환 실패 시 복구 방식은 실패 시점에 따라 달라진다.

### Target 준비 중 실패

Model snapshot 준비 단계에서 실패하면 기존 Main Model runtime을 그대로 유지한다.

### Runtime 교체 이후 실패

Runtime 교체 이후 target runtime 시작이나 검증이 실패하면 **직전에 정상 동작한 profile**로 복구(rollback)한다.

```text
Target Runtime Failure
        ↓
이전 정상 Profile 복구
        ↓
Runtime Validation
        ├─ 성공 → Gate Open → operation = failed
        └─ 실패 → Gate Closed → operation = rollback_failed
```

`failed`는 요청한 target 전환이 실패했다는 의미이며, 이전 profile로 서비스가 복구된 상태일 수 있다.

`rollback_failed`에서는 Chat gate가 닫힌 상태로 유지된다. 이 경우 Main Model 상태와 runtime log를 확인하고 정상 profile 복구 작업을 수행한다.

### 주요 실패 유형

| 상황 | 확인 지점 | 운영 방향 |
|---|---|---|
| Profile 확인 실패 | `/admin/main-model/profiles` | profile ID와 compatibility 확인 |
| Profile lock | `/admin/main-model` | deployment lock 정책 확인 |
| GPU admission 실패 | `/admin/runtimes` | stop plan 또는 runtime 구성 조정 |
| Model 준비 실패 | model cache / Sidecar log | snapshot과 revision 접근 상태 확인 |
| Drain 지연 | in-flight Chat request | 진행 중 요청과 drain 상태 확인 |
| Runtime 시작 실패 | `main-llm-vllm` log | image, command, GPU allocation 확인 |
| Validation 실패 | health, `/v1/models`, canary | runtime과 profile 일치 여부 확인 |
| Rollback 실패 | `last_operation`, runtime log | 정상 profile 복구 후 gate 상태 확인 |

---

## 6.9 상세 전환 상태

일반 운영에서는 [6.4 모델 전환](#64-모델-전환)의 기본 흐름을 기준으로 보면 된다. 전환 문제를 분석할 때는 다음 operation 상태를 사용한다.

| 상태 | 의미 |
|---|---|
| `pending` | 전환 작업 접수 |
| `preparing` | target model snapshot과 cache 준비 |
| `draining` | 기존 요청 완료 대기 |
| `stopping` | 기존 runtime 종료 진행 |
| `starting` | target runtime 생성·시작 |
| `validating` | health와 inference 검증 |
| `rolling_back` | 이전 정상 profile 복구 진행 |
| `completed` | target profile 전환 완료 |
| `failed` | target 전환 실패 또는 rollback 후 복구 완료 |
| `rollback_failed` | target 전환과 이전 profile 복구 모두 실패 |

Main Model의 `runtime_state`와 switch operation 상태는 서로 다른 상태 정보다.

```text
runtime_state
  └─ active / stopped

switch operation
  └─ pending → preparing → ... → completed / failed / rollback_failed
```

### 중단된 전환 복구

Admin Sidecar가 switch 중 재시작되면 저장된 operation과 실제 Main Model container를 비교해 상태를 복구한다.

- target profile이 실행 중이고 검증되면 operation을 `completed`로 정리한다.
- 이전 정상 profile이 실행 중이고 검증되면 해당 profile을 활성 상태로 복구하고 operation을 `failed`로 정리한다.
- runtime과 저장된 operation 상태를 일치시키기 어려운 경우 `rollback_failed`로 기록하고 gate를 닫은 상태로 유지한다.

---

## 6.10 Secondary Runtime 운영

Main Model 외 controllable runtime도 Admin API에서 시작·중지할 수 있다.

대표 service key는 다음과 같다.

- `embedding`
- `embedding_ko`
- `risk_prompt`

현재 상태는 다음 API에서 확인한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/runtimes
```

### Runtime 중지

```bash
curl -X PATCH \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"stopped"}' \
  http://127.0.0.1:9400/admin/runtimes/embedding
```

### Runtime 시작

```bash
curl -X PATCH \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"active","force":false}' \
  http://127.0.0.1:9400/admin/runtimes/embedding
```

Start 과정에서는 prerequisite와 GPU budget을 확인하고 필요한 runtime을 startup order에 따라 시작한다.

compose-up/full 배포 시 처음부터 활성화할 secondary runtime 조합은 `configs/deploy_profiles.yaml`에서 결정한다. 배포의 `DEPLOY_RUNTIME_PROFILE` 또는 로컬 `compose-up`의 `RUNTIME_PROFILE`을 생략하면 `retrieval_ready`가 적용되어 Prompt Risk 모델은 초기 중지 상태가 된다.

---

## 6.11 Runtime Artifact와 Capability

Main Model Profile은 model identity와 runtime artifact를 하나의 실행 단위로 관리한다.

| 항목 | 역할 |
|---|---|
| Model / Revision | 실행할 upstream model과 고정 revision |
| Runtime Image | vLLM 실행 환경 |
| vLLM Command | model runtime 실행 인자 |
| GPU Reservation | runtime GPU resource 요구량 |
| Capability | text / audio / video 등 배포 기능 |

Profile에 기록된 revision과 runtime image는 모델 전환 시 함께 적용된다. Gateway가 허용하는 Main Model modality는 active profile의 capability와 runtime validation 결과를 기준으로 한다.

세부 profile 정의는 `configs/main_model_profiles.yaml`, derived vLLM image 구성은 `configs/vllm_unified_build.yaml`에서 관리한다.

---

## 6.12 운영 확인 순서

Main Model 변경 작업은 다음 순서로 확인한다.

| 순서 | 확인 내용 | 방법 |
|---:|---|---|
| 1 | Runtime / GPU 상태 | `GET /admin/runtimes` |
| 2 | 현재 Main Model 상태 | `GET /admin/main-model` |
| 3 | 사용 가능한 Profile | `GET /admin/main-model/profiles` |
| 4 | 모델 전환 | `POST /admin/main-model/switch` |
| 5 | 전환 결과 | `GET /admin/main-model/operations/{id}` |
| 6 | Gateway readiness | `GET /ready` 또는 `make ready-full` |
| 7 | 실제 inference | Chat smoke test |

세부 request / response 형식과 Admin API error contract는 [API Reference](./reference/api_reference.md)를 참고한다.

---

## 6.13 주요 Source of Truth

| 영역 | 주요 파일 | 역할 |
|---|---|---|
| Main Model profile | `configs/main_model_profiles.yaml` | model, revision, image, vLLM command, capability, Gateway 요청 정책, compatibility 정의 |
| GPU budget | `configs/gpu_budgets.yaml` | GPU admission ceiling과 runtime resource policy 정의 |
| Runtime serving policy | `configs/model_serving.yaml` | Gateway runtime 연결, timeout, admission 정의 |
| Deploy Runtime Profile | `configs/deploy_profiles.yaml` | compose-up/full 배포 후 secondary runtime 활성 구성 정의 |
| Runtime topology | `ops/compose/full-stack.private-network.yaml` | Main / secondary runtime container 기본 topology 정의 |
| Main Model state | `.runtime/main-model/main-model-state.json` 또는 deployment state path | active profile, gate, runtime state, switch operation 기록 |
| Runtime control implementation | `src/ai_model_serving/main_model/`, `src/ai_model_serving/apps/admin_sidecar.py` | switch, validation, rollback, Docker lifecycle 구현 |
| Gateway Admin API | `src/ai_model_serving/api/routers/gateway_runtime_control.py` | Runtime / Main Model Admin API 제공 |

설정 구조와 적용 방식은 [5. 설정 체계와 Source of Truth](./05_configuration.md)에서 설명한다.
