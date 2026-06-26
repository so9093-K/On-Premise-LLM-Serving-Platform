# 모델 런타임 제어

Gateway는 모델 서버를 직접 생성하지 않는다. vLLM process/container는 compose 또는 운영 플랫폼이 관리하고, Gateway는 HTTP upstream으로 호출한다.

## 메인 모델 선택

`local-main` 외부 API alias는 유지하면서 내부 메인 모델 프로필을 선택할 수 있다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/main-model

curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/main-model/profiles

curl -X POST \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile":"gemma4-12b-unified-fp8"}' \
  http://127.0.0.1:9400/admin/main-model/switch
```

전환 요청은 `202 Accepted`와 operation ID를 반환한다. 진행 상태는
`GET /admin/main-model/operations/{operation_id}`로 확인한다. API에는 profile
ID만 전달할 수 있고 model ID, image, command, environment는 지정할 수 없다.

전환 작업은 먼저 `preparing` 단계에서 선택 profile의 고정
`model_id + revision` 전체 snapshot을 공용 Hugging Face cache에 준비하고
local-only로 다시 확인한다. 이 단계에서는 현재 main-model gate를 열어 둔다.
다운로드나 cache 검증이 실패하면 실행 중인 모델을 중지하지 않고 작업만
실패한다. 운영자가 전환 전에 명시적으로 준비하려면 다음 명령을 사용할 수 있다.

```bash
make main-model-prepare PROFILE=gemma4-12b-unified-fp8
```

이 명령은 active profile이나 실행 중인 컨테이너를 변경하지 않는다.

- `gemma4-26b-a4b-fp8` — Gemma 4 26B A4B FP8
- `gemma4-12b-unified-fp8` — Gemma 4 12B Unified FP8

전환 중 신규 chat 요청은 `503`과 `Retry-After: 5`를 받는다. health,
`/v1/models`, 실제 text canary 중 하나라도 실패하면 last-known-good
profile로 rollback한다. rollback도 실패하면 gate를 닫은 fail-closed 상태를
유지한다. 이 과정은 무중단 전환이 아니다.

부팅 우선순위는 locked profile, 마지막 성공 active profile, 설치 기본
profile 순서다. 기본값은 기존 26B를 보존한다.

```dotenv
MAIN_LLM_BOOT_PROFILE=gemma4-26b-a4b-fp8
MAIN_LLM_PROFILE_LOCKED=false
```

`MAIN_LLM_PROFILE_LOCKED=true`이면 API 전환은 거절된다. 상태는
`.runtime/main-model/main-model-state.json`에 atomic write로 저장된다.

Full Compose 기동과 Full CI 배포는 이 상태를 시작 전에 읽어 임시 Compose boot
projection을 원자적으로 생성한다. projection은 해당 Compose 명령이 끝나면
삭제되며 영구 active-state나 운영자 편집 대상이 아니다. Compose는 projection의
profile command로 main model을 처음부터 부팅하고, Admin Sidecar는 실제 container
command를 확인해 상태를 검증한다.
상태 JSON이 손상됐거나 저장 profile이 현재 catalog에 없으면 기본 26B로 조용히
fallback하지 않고 기동을 중단한다.

12B compatibility는 현재 고정 revision과 pinned derived runtime image 기준으로
`verified`다. 이 검증은 현재 호스트의 1차 검증 결과이며, 별도 exact-system evidence
없이 24 GiB 호환이나 장기 production-ready를 일반화하지 않는다. Gateway는 Scalar/OpenAPI에 text,
`image_url`, `input_audio`, `video_url` content part를 표현하되 실제 허용은
active profile의 `deployed_input`과 boot canary 결과로 gate한다. 26B active
상태에서는 audio/video가 422로 거부되고, 12B 전환은 image/audio/video가 고정
runtime image에서 canary를 통과해야 open된다. audio/video 런타임 이미지는
`ops/images/vllm-gemma4-audio/README.md`에 문서화돼 있다.

## 메인 모델 정지/시작 (VRAM 회수)

메인 모델도 보조 런타임과 **동일한 동사**로 정지/시작한다 —
`PATCH /admin/runtimes/main`에 `desired_state`를 지정한다. 별도 엔드포인트는 없다.
정지는 gate를 닫고 진행 중 요청을 drain한 뒤 컨테이너를 내린다(같은 프로필 재시작이
빠르도록 제거하지 않는다). 정지 상태(`runtime_state=stopped`)는 영속되어 재시작 시에도
자동으로 다시 올리지 않는다. 시작은 GPU 예산 admission과 canary 검증을 거친다.
프로필 교체만 메인 고유의 `POST /admin/main-model/switch`를 쓴다.

```bash
# 정지 (드레인 후 VRAM 회수, 신규 chat은 503으로 fail-closed)
curl -X PATCH -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" -d '{"desired_state": "stopped"}' \
  http://127.0.0.1:9400/admin/runtimes/main

# 시작 (GPU 예산 admission 후 기동·검증; 예산 부족 시 force=true로 보조 축출)
curl -X PATCH -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" -d '{"desired_state": "active", "force": false}' \
  http://127.0.0.1:9400/admin/runtimes/main
```

## 공유 GPU 예산과 admission

모든 vLLM 런타임(보조 + 메인)은 하나의 GPU VRAM 예산을 공유한다. 각 런타임은
`gpu_memory_utilization`만큼 VRAM을 예약하며, 활성 런타임 점유율의 합은 천장을
넘을 수 없다. 천장과 모델별 점유율의 단일 원천은 `configs/gpu_budgets.yaml`이며
(`gpu.total_gpu_memory_utilization.avoid_above`, 현재 0.93), 현재 4개 모델 합은
0.925다. `GET /admin/runtimes`는 보조와 메인을 한 번에 보여주고
`budget {ceiling, used, free}`를 함께 반환한다.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://127.0.0.1:9400/admin/runtimes
```

런타임을 올릴 때 예산을 초과하면 자동으로 다른 모델을 내리지 않고 `409`와 정지
계획을 반환한다.

```json
{"detail": {"code": "GPU_BUDGET_EXCEEDED", "feasible": true,
  "required": 0.85, "available": 0.765, "ceiling": 0.93,
  "plan": {"stop": ["risk-prompt-vllm", "embedding-ko-vllm"]}}}
```

계획대로 직접 정지한 뒤 다시 시도하거나, 보조 시작/메인 시작은 `force=true`로
우선순위가 낮은 런타임을 자동 축출할 수 있다. 메인은 다른 모델을 위해 절대 자동
축출되지 않는다(최고 우선순위).

단일 GPU에서 26B(0.76)와 12B(0.76)를 동시에 올릴 수 없으므로, 12B를 검증하려면
먼저 메인을 정지(또는 교체)해 자리를 비워야 한다 — 위 절차가 이를 안전하게 수행한다.

### vLLM 런타임 의존성과 독립성

런타임 사이의 의존은 **시작 순서**에 한정된다(런타임 결합이 아니다). compose는
`main-llm → embedding → embedding-ko → risk-prompt → risk-adapter → gateway` 순으로
`service_healthy`를 기다리는데, 이유는 vLLM이 init 중 free VRAM을 프로파일하므로
**동시 기동 시 steady-state 예산이 맞아도 프로파일이 실패**할 수 있기 때문이다(직렬
로딩). sidecar의 보조 재기동 prerequisite(embedding-ko←embedding,
risk-prompt←embedding+embedding-ko)도 같은 직렬화 정책이다.

steady-state에서는 각 vLLM이 자기 포트에서 독립 서빙한다. 기능 의존만 남는다:
- **채팅 경로는 main만** 사용한다 → 보조를 모두 내려도 채팅은 동작한다(축출이
  안전한 근거).
- risk-adapter의 vllm prompt detector → risk-prompt에 의존.
- 임베딩/retrieval → embedding(+ko)에 의존.

**축출 순서**는 `resource_control.criticality` 기준으로 결정된다(서비스명 하드코딩
아님): `retrieval_support_path`(embedding, embedding-ko)를 먼저 내리고,
`risk_signal_path`(risk-prompt)는 더 오래 유지하며, `primary_user_path`(main)은
다른 모델을 위해 절대 자동 축출되지 않는다. 즉 force 축출 시 **retrieval이 risk보다
먼저 희생**된다. `GET /admin/runtimes`의 각 런타임 `criticality` 필드로 무엇이
희생될지 미리 확인할 수 있다.

> ⚠️ 알려진 엣지: 메인을 의도적으로 정지(`runtime_state=stopped`)한 상태에서 full
> `compose up`이 일어나면 compose가 main-llm-vllm을 무조건 기동하므로, sidecar는
> gate를 닫은 채(정지 의도 존중) 컨테이너만 떠서 VRAM을 점유하는 상태가 될 수 있다.
> full compose 기동 후에는 `GET /admin/runtimes`로 main 상태를 확인하고 필요하면
> 다시 정지한다. (per-service 롤링 배포에는 해당하지 않는다.)

## 제어 지점

- endpoint URL은 enabled runtime 기준 `MAIN_LLM_BASE_URL`, `EMBEDDING_BASE_URL`, `RISK_PROMPT_BASE_URL`로 관리한다.
- served model name은 enabled runtime 기준 `MAIN_LLM_MODEL`, `EMBEDDING_MODEL`, `RISK_PROMPT_MODEL`로 관리한다.
- timeout은 `*_TIMEOUT_SECONDS`, `REQUEST_TIMEOUT_SECONDS`, `RISK_ADAPTER_TIMEOUT_SECONDS`로 관리한다.
- admission control은 `*_MAX_CONCURRENCY`, `*_QUEUE_TIMEOUT_SECONDS`로 관리한다.
- circuit breaker는 `*_CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `*_CIRCUIT_BREAKER_RESET_SECONDS`로 관리한다.

## readiness

Gateway `/ready`는 main LLM, embedding, Risk Adapter를 확인한다. Risk Adapter `/ready`가 admin auth를 요구하면 Gateway는 내부 admin bearer token을 전달한다. Gateway는 Risk Adapter의 HTTP 성공만으로 ready 처리하지 않고, 응답 body의 `status`가 `ready`일 때만 Risk Adapter dependency를 ready로 기록한다.

모델이 실제로 로딩 중이면 `/ready`는 HTTP 503을 유지하되 body에 `phase: waiting_for_dependencies`, `not_ready_dependencies`, `required_not_ready_dependencies`, `optional_not_ready_dependencies`, dependency별 `endpoint`와 `message`를 포함한다. `required_not_ready_dependencies`는 overall status에 영향을 주는 필수 dependency 목록이고, `optional_not_ready_dependencies`는 degraded 상태만 유발하는 선택적 dependency 목록이다. 따라서 UI나 운영 스크립트는 HTTP status로 traffic gate를 막고, body로 어떤 vLLM runtime을 기다리는지 표시한다.

## 모델 lifecycle과 읽기 전용 제어 플레인

운영자는 모델 상태를 YAML 파일을 직접 뒤지지 않고 먼저 `modelctl` projection으로 확인한다.

```bash
python scripts/models/modelctl.py list
python scripts/models/modelctl.py status
python scripts/models/modelctl.py validate
python scripts/models/modelctl.py diff
python scripts/models/modelctl.py propose-add --id new-main --role main_llm --upstream-model-id org/model --port 9499 --endpoint /v1/new-main
python scripts/models/modelctl.py propose-remove local-main
python scripts/models/modelctl.py propose-add --id new-main --role main_llm --upstream-model-id org/model --port 9499 --endpoint /v1/new-main --write-plan --write-patch
python scripts/models/modelctl.py propose-remove local-main --write-plan --write-patch
make model-status
make model-validate
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm WRITE_PLAN=1 WRITE_PATCH=1
make model-propose-remove ID=local-main WRITE_PLAN=1 WRITE_PATCH=1
```

- Add a model / 모델 추가: 먼저 `propose-add`로 id/port/endpoint/runtime service 충돌, GPU budget 경고, 영향 파일 목록을 확인한다. 실제 반영은 `configs/model_catalog.yaml`, `configs/model_serving.yaml`, OpenAPI/schema, tests를 함께 수정하는 리뷰 절차로 진행한다.
- Remove a model / 모델 제거: 먼저 `propose-remove`로 단계적 deprecation plan을 확인한다. 곧바로 삭제하지 않고 `lifecycle.state=deprecated|disabled`와 `exposure=hidden|internal`로 축소한 뒤, public listing, route, config, model card, tests를 함께 제거한다.
- Model independence: 한 모델의 timeout/concurrency 조정이 다른 모델 id나 API contract를 바꾸면 안 된다.
- Input and output contracts: request/response schema는 Gateway에서 검증한다.
- lifecycle state는 `experimental`, `active`, `deprecated`, `disabled`, `retired`, `removed`를 사용한다.
- exposure 값은 `public`, `internal`, `hidden`을 사용한다.

쓰기형 add/remove 명령은 아직 source 파일을 직접 수정하지 않는다. 현재 단계에서는 `modelctl`의 `list/status/validate/diff`가 registry/projection drift를 읽고 검증하고, `propose-add/propose-remove`는 기본적으로 파일 쓰기 없는 plan-only control plane으로 동작한다. `--write-plan`을 주면 `reports/model_changes/*.plan.json`과 `*.plan.md`를 남기고, `--write-patch`를 주면 사람이 리뷰할 수 있는 `*.patch-scaffold.md`까지 생성한다. 이 artifact는 review/checklist 용도이며 `configs/`, `contracts/`, `specs/` 원본 파일은 수정하지 않는다.


## 로컬 readiness와 full readiness 구분

`make start`는 Gateway와 Risk Adapter만 시작한다. 모델 runtime은 시작하지 않는다. 따라서 app-only health gate에서는 `make ready-local`을 사용하고, 실제 vLLM dependency까지 포함한 운영 gate에서는 `make ready-full`을 사용한다. `make ready-local`은 app process가 내려가 있으면 실패하지만 vLLM `/ready` 상태는 요구하지 않는다.
