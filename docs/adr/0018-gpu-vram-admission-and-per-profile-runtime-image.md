# ADR-0018: 통합 GPU VRAM Admission과 Per-profile 런타임 이미지

날짜: 2026-06-22

## Status

Accepted

> **현재 운영 기준 (2026-08-12)**: GPU admission ceiling은 `configs/gpu_budgets.yaml`,
> profile별 image·command·capability는 `configs/main_model_profiles.yaml`, Gateway 요청
> 정책은 `configs/model_serving.yaml`을 기준으로 한다. 다음 상태 분석은 이 ADR이 해결하려던
> 당시의 분리된 제어 구조와 제약을 보존하는 기록이다.

[ADR-0017](0017-selectable-main-model-runtime.md)을 확장한다. 0017은 단일 고정
런타임 이미지·`gpu_memory_utilization=0.76` 고정·오디오 inert를 전제했는데, 이 ADR이
그 세 전제를 각각 진화시킨다.

## 당시 상태 분석 (결정 시점)

- 단일 RTX 6000 Ada(약 48 GiB)에서 메인 LLM·임베딩·임베딩-ko·risk-prompt가 VRAM을
  공유한다. 그런데 컨트롤 플레인이 둘로 쪼개져 있었다: 메인 핫스왑(프로필 교체)과 보조
  on/off가 서로의 VRAM을 모른 채 동작했고, "모델 Y를 올리려면 누구를 내릴지" 결정하는
  주체가 없었다. 실측으로도 26B(0.76)+12B(0.76)=1.52 > 1.0 이라 12B 검증조차 26B를
  내려야 가능한데 그 조율이 수동·암묵이었다.
- 0017은 호스트의 런타임 이미지를 단일 고정 digest로 본다. 그러나 12B의 audio/video
  입력을 쓰려면 디코드 라이브러리(`libsndfile`/`soundfile`/`librosa`/`PyAV`)가 포함된
  **다른** 런타임 이미지가 필요하다. 능력이 이미지에 묶여 있는데 이미지가 프로필을
  따라오지 않으면, 12B로 전환해도 표준 이미지로 떠서 media 입력이 닿지 않는다.
- `gpu_memory_utilization`은 그 GPU 총량의 **비율**이다. 12B FP8 가중치(~13 GiB)는 0.76
  (~34 GiB 예약)을 필요로 하지 않으며, 더 작은 GPU에 올리려면 같은 모델이라도 다른 비율이
  필요하다. 하나의 정적 값으로 서로 다른 크기의 GPU를 동시에 만족시킬 수 없다.

## 결정

### 1. 통합 GPU VRAM admission

VRAM을 단일 예산으로 보고 모든 모델 로드를 그 예산에 대한 admission으로 통합한다.

- 비용 = 정적 `gpu_memory_utilization`(vLLM이 실제 예약하는 값)의 합. 보조는
  `model_serving.yaml`, 메인은 활성 프로필 command에서 파싱한 `vram_fraction`이 출처다.
- 천장 = `configs/gpu_budgets.yaml`의 `gpu.total_gpu_memory_utilization.avoid_above`
  (단일 진실원, 현재 0.93). admission은 합 ≤ 천장을 보장한다.
- 메인 모델도 참가자다. 단 최고 우선순위·**non-evictable**(다른 모델을 위해 자동 축출되지
  않음). 메인은 자기 슬롯 교체(switch)나 명시적 정지(stop)로만 VRAM을 비운다.
- 예산 초과 시 **거부 + 계획 반환**(409 + 어떤 victim을 내려야 하는지). `force`로 자동 축출.
- 축출 순서는 `resource_control.criticality` 기반: `retrieval_support_path`(임베딩)를
  `risk_signal_path`(risk)보다 먼저 내린다. 안전 신호 경로를 마지막까지 보존한다.
- 권한(authority)은 사이드카에 둔다. docker·메인 상태·컨테이너 제어를 모두 가진 유일
  컴포넌트이기 때문이다. 게이트웨이는 예산 스냅샷과 거부 계획을 표면화만 한다.

순수 admission planner(`gpu_budget.plan_activation`)는 부수효과 없는 함수로 분리해 단위
테스트로 feasible/infeasible/victim 최소성/criticality 순서/메인 non-evictable을 고정한다.

### 2. Per-profile 런타임 이미지

각 메인 프로필은 자기 런타임 이미지를 핀할 수 있다(`profile.image`). 지정이 없으면 공용
`runtime.image`를 상속한다. 어느 쪽이든 resolved 이미지는 digest-pin 필수다.

- 능력(오디오 디코드 등)이 **프로필을 따라온다**: 12B만 오디오 이미지를 핀하고 26B는 공용
  base 그대로 → 프로필 전환이 능력을 함께 끌고 다닌다. 26B 경로는 영향받지 않는다.
- 로더가 유일한 생성자이고 항상 digest-pin된 값으로 resolve하므로, 빈 이미지가 Docker
  경계에 도달할 수 없다(필수 필드, fail-fast).

### 3. Per-host gpu-memory-utilization override

`MAIN_LLM_GPU_MEMORY_UTILIZATION`(optional, (0,1]) env로 메인 프로필의 util을 호스트별로
오버라이드한다. 카탈로그 값은 기준 호스트 기본값이다.

- override는 런타임 command와 파싱된 `vram_fraction`(admission 비용)에 **동시 반영**되어
  둘이 어긋나지 않는다.
- fraction은 그 호스트 VRAM의 비율이므로, 더 작은 GPU는 더 큰 값을 설정한다. 하드웨어
  이름은 어디에도 박지 않는다.

### 4. 멀티모달 활성화 경로

Audio/video는 0017과 동일하게 기본 inert다. 활성화는 게이트된 운영 절차다:

1. `vllm-unified` 이미지를 `build-vllm-derived` CI 잡으로 빌드·push하고 immutable
   digest를 산출한다(`build/vllm-unified-image.env`). base는 메인 런타임 digest + 디코드 스택뿐.
2. 그 digest를 `gemma4-12b-unified-fp8` 프로필 `image`에 핀하고 caps를 flip한다
   (`deployed_input`에 audio/video 추가, `audio_enabled: true`, `video_enabled: true`).
3. 12B로 switch하면 `validate()`가 media boot canaries를 실행한다. 디코드 실패 시 26B로
   rollback되어 advertised modality가 반쪽 활성되지 않는다.

## 호환성 및 기능 정책

- 메인은 admission에서 non-evictable이다. 다른 모델을 위해 메인이 자동으로 내려가는 일은
  없다.
- Audio/video는 이미지·프로필 flip·canary가 모두 충족되기 전까지 inert다(0017 정책 유지).
  게이트웨이는 활성 프로필의 `deployed_input`에 audio가 포함될 때만 오디오 입력을 받는다.

## 테스트 계획

- `gpu_budget.plan_activation` 순수 단위: feasibility, victim 최소성, criticality 축출
  순서, 메인 non-evictable, 천장 경계.
- 사이드카 admission: 보조 start 거부(목 docker), 메인 switch/start admission.
- per-profile image: 상속/오버라이드/digest 강제/스냅샷 반영.
- util override: command·`vram_fraction` 동시 반영, 미설정 시 카탈로그 값, 범위 검증.
- 실제 GPU 검증(오디오 canary 포함)은 운영 창에서 별도 보고하며 단위 테스트로 추론하지
  않는다.

## 위험 요소

- 비용은 예약값(정적 근사)이지 실측이 아니다. 단편화·오버헤드는 천장 0.93의 여유로 흡수한다.
  live nvidia-smi 보정은 후속 범위다.
- util override는 호스트 상대값이라, 한 값이 서로 다른 크기의 GPU를 동시에 최적화하지
  못한다. 호스트별 env로 해소한다.
- 더 작은 VRAM 봉투(예: 24 GiB)에서 긴 컨텍스트(20K)의 KV 캐시가 빠듯할 수 있다. 이는
  활성화 시 운영 검증으로 확인하며, 부족하면 `max_model_len` 하향 또는 util 상향으로
  트레이드오프를 결정한다.

## 배포 통합

- `build-vllm-derived` CI 잡이 공용 `vllm-unified` 이미지를 한 번 빌드한다
  (~25 GiB vLLM base를 한 번만 pull). unified digest는 `build/vllm-unified-image.env`
  아티팩트로 산출되어 risk-prompt와 12B 프로필 핀에 함께 사용된다.
- admission·메인 상태·전환 결과는 기존 Gateway Prometheus metric(`main_model_operation_state`,
  request gate, switch/rollback totals, 마지막 전환 시간)으로 관측한다.

## Update (2026-07-20)

- 배포 서버 로그에서, 메인 모델 전환/재부팅 직후 `response_format: json_schema`를 쓰는 실제
  요청마다 다음 경고가 관측됐다: `jit_monitor: Triton kernel JIT compilation during inference:
  apply_token_bitmask_inplace_kernel`(xgrammar 기반 constrained decoding에 쓰이는 커널).
  `DockerMainModelBackend.validate()`의 text canary(`max_tokens=8`, `response_format` 없음)는
  이 경로를 태우지 않으므로, 관측된 로그상으로는 이 커널이 웜업 중에 예열되지 않고 있었다.
  vLLM 공식 문서상 최신 `warmup_kernels()`는 합성 `GrammarOutput`으로 이 bitmask 커널까지
  부팅 웜업에 포함시킨다고 설명하는데([vLLM warmup 문서](https://docs.vllm.ai/en/latest/api/vllm/v1/worker/gpu/warmup/)),
  이건 **Model Runner V2 한정** 설명이다. 현재 배포 버전(`0.1.dev17235+gf52870f26.d20260603`)은
  V1 model runner로 뜨고 있어서(아래 "근본 원인 확정" 참고) 이 경로를 안 타고, 그래서
  이 커널이 실제 요청에서 JIT되는 게 로그로 확인됐다.
- text canary 직후에 `response_format: json_schema`(strict, minimal schema)를 쓰는 best-effort
  웜업 호출을 추가했다. 이 호출은 실패해도 `validate()` 전체를 실패시키지 않는다(순수 JIT
  예열이 목적이라, 느리거나 실패해도 이미 정상 동작하는 프로필을 롤백할 이유가 아니다).
  `validate()`는 명시적 전환·`initialize()`의 boot reconcile·`_recover_interrupted()` 세
  경로에서 모두 호출되므로 이 한 곳의 수정으로 세 경로가 함께 커버된다.
- 알려진 잔여 gap: `validate()`는 **admin-sidecar 프로세스가 (재)시작할 때**만
  `initialize()`를 거쳐 실행된다. main-llm-vllm 컨테이너만 단독으로(`docker restart`) 재시작하는
  운영 조치는 admin-sidecar를 건드리지 않으므로 이 웜업을 타지 않는다.
  **main-llm-vllm 재시작은 반드시 admin-sidecar 제어 API(`PATCH /admin/runtimes/main`,
  `MainModelManager.start_main()`)로만 수행한다** — 이 경로는 매번 `validate()`를 호출해
  웜업이 정확히 돈다. raw `docker`/`compose restart`로 직접 건드리는 건 금지 대상이다.

## Update (2026-07-21)

- 위 gap 중 "compose가 main-llm-vllm만 recreate하는 배포 경로"(예: `configs/main_model_profiles.yaml`
  변경으로 인한 rolling→full 자동 승격, `main-llm-vllm`만 `compute_recreate_set`에 걸리고
  admin-sidecar 이미지는 안 바뀌는 경우)는 admin-sidecar 제어 API를 거치지 않으므로 여전히
  구멍이었다. `scripts/ops/ready_full.sh`의 `warm_inference_paths_best_effort()`에
  `response_format: json_schema` 웜업 호출을 추가해 이 경로를 커버했다 — `DockerMainModelBackend`가
  쓰는 것과 동일한 스키마(`{"type":"object","properties":{"ok":{"type":"boolean"}},...,"strict":true}`)이며
  best-effort(실패해도 `ready-full`을 abort시키지 않음)다.
- 이전 버전이 대안으로 제시했던 "`make ready-full`을 수동 실행해도 된다"는 **틀린 정보였다** —
  `ready-full`은 이 업데이트 전까지 risk/embedding/embedding_ko만 데웠고 structured output은
  전혀 건드리지 않았다. 이제는 사실이 됐지만, admin-sidecar를 건드리지 않는 완전 수동
  `docker restart` 자체를 막는 유일한 방법은 여전히 위 운영 규율(제어 API만 사용)이다 —
  `ready-full`은 CI 배포 경로의 방어선일 뿐, 운영자가 배포 파이프라인 밖에서 수동으로
  재시작한 뒤 `ready-full`을 안 돌리면 여전히 못 잡는다.
- **근본 해법도 같은 날 구현했다**: admin-sidecar에 10초 간격 reconciliation 루프
  (`_run_reconciliation_loop` → `MainModelManager.reconcile_if_restarted()`)를 추가했다.
  매 tick마다 `DockerMainModelBackend.observed_started_at()`으로 컨테이너의 실제
  Docker `State.StartedAt`을 관측해, 마지막으로 `validate()`가 성공했을 때 기록해둔
  값(`last_validated_container_started_at`, state store에 영속화)과 다르면 admin-sidecar가
  전혀 모르는 사이에 컨테이너가 재시작된 것으로 간주하고 `validate()`를 다시 돈다.
  **gate는 재웜업 중에 닫지 않기로 결정했다** — 재시작~재웜업 완료까지의 전체 노출 창
  자체는 poll 간격(최대 10초) + validate() 소요 시간으로, 이 전체가 짧다는 뜻은 아니다.
  gate를 닫는다고 줄어드는 건 이 중 validate() 소요 시간(수 초)뿐이라, 그 몇 초 동안
  요청이 깨끗한 503을 받는 것과 느리거나 잘린 200을 받는 것의 차이일 뿐이고, 지금까지는
  전체 노출 창(무한대)조차 전혀 못 잡던 상태에서 "최대 10초"로 줄인 것 자체가 순개선이라는
  판단이다. 진행 중인 전환·복구 작업과는 기존 `self._lock`을
  그대로 재사용해 경합하지 않는다. poll 간격은 tick당 비용이 lock 획득 + inspect
  하나뿐이라 부담이 없어 짧게(10초) 잡았다 — 이제 raw `docker`/`compose restart`도
  최대 10초 이내에 자동으로 재검증된다. 위의 "제어 API만 사용" 운영 규율은 여전히
  권장이지만, 더 이상 유일한 방어선은 아니다.
- **tool-calling 웜업도 추가했다**: main-llm 커맨드의 `--enable-auto-tool-choice
  --tool-call-parser gemma4`도 xgrammar 기반 제약 디코딩을 거치므로, 위
  `response_format:json_schema`와 같은 Triton JIT 이슈를 별도로 겪을 수 있다는
  가설로 `validate()`에 `tool_choice`를 특정 함수로 강제하는 best-effort 웜업 호출을
  하나 더 추가했다(`_TOOL_CALL_WARMUP_TOOLS`). 다만 Triton JIT 캐시는 보통 스키마
  내용이 아니라 커널 단위로 캐싱되므로, 두 경로가 같은 `apply_token_bitmask_inplace_kernel`을
  공유한다면 이미 json_schema 웜업만으로 예열됐을 가능성도 있다 — 실제로 별개의 JIT
  이벤트인지는 로그로 미확인이며, 이 웜업 호출 자체가 가장 싼 검증 수단이다(배포
  로그에 새 JIT 이벤트가 뜨면 별개, 안 뜨면 이미 커버되고 있었다는 뜻).
- **범위 한정**: 이 웜업은 로그에서 직접 관측된 JIT 이벤트에 대한 예방 조치이며, 별도로
  신고된 CSO classifier 클라이언트의 타임아웃 사고와는 무관하다. 그 사고는 실제 재현
  테스트(`finish_reason: length`, 응답 content 직접 확인)로 원인이 확정됐다 — 클라이언트
  프롬프트가 스키마에 없는 필드(`matched_keywords`, `confidence`)를 요구해 그 의도가
  `evidence` 배열로 흘러들어갔고, `temperature=0.1` + 배열 길이 제한(`maxItems`) 부재로
  동일 항목을 무한 반복하며 끝내지 못했다. JIT 컴파일 지연과는 별개의 문제이므로, 이 웜업
  fix가 그 사고를 예방하지는 않는다.
- **근본 원인 확정** (위 "미확인" 정정): 배포 중인 `compose-main-llm-vllm-1` 컨테이너에 직접
  접속해 설치된 vLLM 소스를 읽고 부팅 로그로 실행 조건을 대조했다. `GPUWorker.compile_or_warm_up_model()`
  (`vllm/v1/worker/gpu_worker.py:688`)은 `self.use_v2_model_runner`가 참일 때만 합성
  `GrammarOutput`으로 bitmask 커널을 실제로 태우는 `warmup_kernels()`
  (`vllm/v1/worker/gpu/warmup.py`)를 부른다; 거짓이면 `elif` 분기로 빠져 샘플러만 데우는
  `_dummy_sampler_run()`을 부르고 `GrammarOutput`은 아예 만들지 않는다. `use_v2_model_runner`는
  `VllmConfig._is_default_v2_model_runner_model()` (`vllm/config/vllm.py:544`)에서
  `not model_config.is_quantized`를 요구하는데, 부팅 로그에 찍힌 엔진 설정이
  `quantization=compressed-tensors`(gemma-4-12B-it FP8-Dynamic)임을 확인했다 — 즉 이 모델은
  양자화돼 있어 구조적으로 V2 model runner 후보에서 제외되고, V1 경로로 강제되며, V1의 웜업
  루틴에는 애초에 구조화 출력 커널 예열이 없다. 워밍업 함수 맨 끝에서 `activate_triton_jit_monitor()`가
  호출되는데(V1/V2 분기 직후), 이게 "이 시점 이후의 JIT 컴파일은 비정상"이라고 감시를 시작하는
  지점이라 — bitmask 커널이 웜업에서 빠진 채로 감시가 시작되고, 첫 실제 구조화 출력 요청이
  그 커널을 처음 건드리는 순간 정확히 위에서 관측된 `jit_monitor` 경고가 뜨는 구조까지 소스
  레벨로 확인됐다. vLLM 버그가 아니라 "Model Runner V2가 아직 양자화 모델을 지원하지 않는다"는
  알려진 아키텍처 제약이며, 애플리케이션 레벨 웜업(json_schema/tool-calling)은 이 제약이 있는
  한 계속 필요하다 — vLLM이 V2에서 양자화 모델을 지원하거나 V1 경로에 GrammarOutput 웜업을
  추가하기 전까지는 저절로 사라질 gap이 아니다.
- **위 근본 원인을 boot 로그로 직접 재확인했다** (소스 추론에서 확정으로): vLLM은
  `use_v2_model_runner`가 참일 때만 `logger.info_once("Using V2 Model Runner")`를 찍고
  V1일 때는 대응 로그가 없다(`gpu_worker.py:291`). `docker logs compose-main-llm-vllm-1`
  전체에 "Using V2 Model Runner"가 0건 — V1으로 떴다는 직접 증거다. 같은 로그에서
  `jit_monitor` 활성화(07:55:59) 이후 실제 추론 중 `apply_token_bitmask_inplace_kernel`
  JIT(07:56:31)를 포함해 `_compute_slot_mapping_kernel`, `kernel_unified_attention`도
  함께 JIT된 것을 확인했다 — 뒤 두 개는 이 웜업 fix의 범위 밖(다른 shape/커널이라 별도
  조사가 필요하며, 여기서는 기록만 해둔다).
- **JIT와 "응답 짤림"의 인과관계를 정정한다**: JIT 커널 자체가 토큰을 자르지 않는다. 실제
  경로는 JIT → 지연(첫 토큰 및 전체 응답 시간 증가) → 2차 효과다. `gateway_service.py`의
  비스트리밍 경로(195-203행)는 `gateway_timeout_seconds` 초과 시 깔끔한 504를 반환할 뿐
  "짤림"은 아니다. 스트리밍 경로(236-282행)는 이미 청크를 여러 개 내보낸 상태에서
  `streaming_max_duration_seconds` 타임아웃이 나면 그 시점까지의 부분 응답 뒤에 SSE
  error event가 붙어 나가는데, 이게 클라이언트 입장에서 "잘린 것처럼" 보이는 현상의 실체다
  — 즉 "지연/짤림"은 JIT의 두 가지 직접 결과가 아니라, JIT가 만든 지연이 스트리밍 타임아웃
  경로를 통해 짤림처럼 보이는 형태로 이어지는 것이다.
- **웜업 호출의 4xx/5xx 미탐지 버그를 고쳤다**: `docker_main_model_backend.py`의
  json_schema/tool-calling 웜업 호출이 `response.raise_for_status()`를 부르지 않고 있었다
  — httpx는 4xx/5xx에서 예외를 던지지 않으므로(명시적으로 `raise_for_status()`를 불러야
  던진다), 웜업이 400/500을 받아도 `except httpx.HTTPError`가 안 걸려 조용히 "성공"으로
  넘어가고 있었다. 특히 4xx는 보통 요청 검증 단계에서 막혀 샘플링 코드까지 못 가므로,
  정작 예열하려던 bitmask 커널을 태우지도 못한 채 웜업이 됐다고 착각하는 상황이 가능했다.
  두 호출 모두에 `raise_for_status()`를 추가했고(non-fatal 정책은 유지, `httpx.HTTPError`로
  잡혀 경고 로그만 남긴다), 회귀 테스트에 실패 시 경고 로그가 실제로 찍히는지 검증하는
  assertion을 추가했다(`test_docker_main_model_backend.py`).
- **재시작~재웜업 노출 창 설명을 정정했다**: 기존 문구가 "gate를 닫았을 때 줄어드는 창"(수 초)과
  "실제 전체 노출 창"(poll 간격 최대 10초 + validate() 소요 시간)을 섞어 써서, 마치 전체
  노출 창이 수 초인 것처럼 읽힐 수 있었다. `reconcile_if_restarted()` docstring과 위
  단락을 정정했다 — 이 루프가 하는 일은 노출 창을 "무한대"에서 "최대 10초+α"로 줄이는
  것이지, 그 자체를 수 초로 줄이는 게 아니다. 곁들여 `main_model_control.py:533-540`의
  "fingerprint가 None이면 baseline만 기록"하는 경로에, `_validate_and_record()`의
  fingerprint 기록 단계만 간헐적으로 계속 실패하면 이 필드가 영구히 None으로 남아 재웜업이
  트리거되지 않는 잠재 리스크가 있다는 코드 주석을 남겨뒀다 — 두 실패가 겹쳐야 하는 좁은
  경우라 지금은 문서화만 하고 별도 상태 머신 변경은 하지 않았다.

## Update — 2026-08-11: ready-full 중복 warmup 제거

`ready-full`의 실패를 무시하는 순차 warmup(risk/embedding/embedding-ko 및 별도
structured-output 호출)을 제거한다. 뒤따르는 smoke가 같은 경로를 다시 엄격하게
확인하고 있었으므로, warmup은 최대 수 분을 소비하면서도 실패를 배포 실패로 만들지 않는
중복 계층이었다. Structured output은 일반 chat smoke에 `response_format: json_schema`를
포함시키고 응답 JSON까지 검사하는 단일 strict gate로 승격한다. 당시에는 main-model
전환·재시작 시 admin-sidecar backend의 수명주기 웜업을 별도 방어선으로 유지했다.

## Update — 2026-08-13: non-fatal backend warmup 제거

이후 `DockerMainModelBackend`에 남아 있던 json_schema/tool-calling 사전 요청도 제거한다.
두 호출은 실패를 전환 실패로 만들지 않아 runtime 호환성을 보장하지 못했고, 실제 요청의
schema·토큰 크기별 JIT까지 대표할 수도 없었다. 전환과 재시작 재검증은 Docker health,
`/v1/models`의 모델 식별, 일반 text 요청, 활성 프로필이 선언한 audio/video canary만
strict하게 확인한다. 즉 실패를 무시하는 성능 예열은 없고, 실패가 전환 안전성에 의미 있는
검증만 남긴다.
