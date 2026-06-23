# ADR-0018: 통합 GPU VRAM Admission과 Per-profile 런타임 이미지

날짜: 2026-06-22

## Status

Accepted

[ADR-0017](0017-selectable-main-model-runtime.md)을 확장한다. 0017은 단일 고정
런타임 이미지·`gpu_memory_utilization=0.76` 고정·오디오 inert를 전제했는데, 이 ADR이
그 세 전제를 각각 진화시킨다.

## 현재 상태 분석

- 단일 RTX 6000 Ada(약 48 GiB)에서 메인 LLM·임베딩·임베딩-ko·risk-prompt가 VRAM을
  공유한다. 그런데 컨트롤 플레인이 둘로 쪼개져 있었다: 메인 핫스왑(프로필 교체)과 보조
  on/off가 서로의 VRAM을 모른 채 동작했고, "모델 Y를 올리려면 누구를 내릴지" 결정하는
  주체가 없었다. 실측으로도 26B(0.76)+12B(0.76)=1.52 > 1.0 이라 12B 검증조차 26B를
  내려야 가능한데 그 조율이 수동·암묵이었다.
- 0017은 호스트의 런타임 이미지를 단일 고정 digest로 본다. 그러나 12B의 오디오 입력을
  쓰려면 디코드 라이브러리(`libsndfile`/`soundfile`/`librosa`)가 포함된 **다른** 런타임
  이미지가 필요하다. 능력이 이미지에 묶여 있는데 이미지가 프로필을 따라오지 않으면, 12B로
  전환해도 표준 이미지로 떠서 오디오가 닿지 않는다.
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

### 4. 오디오 활성화 경로

오디오는 0017과 동일하게 기본 inert다. 활성화는 게이트된 운영 절차다:

1. `vllm-gemma4-audio` 이미지를 `build-vllm-derived` CI 잡으로 빌드·push하고 immutable
   digest를 산출한다(`build/audio-image.env`). base는 메인 런타임 digest + 디코드 3종뿐.
2. 그 digest를 `gemma4-12b-unified-fp8` 프로필 `image`에 핀하고 caps를 flip한다
   (`deployed_input`에 audio 추가, `audio_enabled: true`, block_reason·강제 chat-template
   제거).
3. 12B로 switch하면 `validate()`가 오디오 boot canary를 실행한다. 디코드 실패 시 26B로
   rollback되어 오디오가 반쪽 활성되지 않는다.

## 호환성 및 기능 정책

- 메인은 admission에서 non-evictable이다. 다른 모델을 위해 메인이 자동으로 내려가는 일은
  없다.
- 오디오는 이미지·프로필 flip·canary가 모두 충족되기 전까지 inert다(0017 정책 유지).
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

- `build-vllm-derived` CI 잡이 risk-vllm-kanana와 `vllm-gemma4-audio`를 한 잡에서 빌드한다
  (~25 GiB vLLM base를 한 번만 pull). 오디오 digest는 `build/audio-image.env` 아티팩트로
  산출되어 12B 프로필 핀에 사용된다.
- admission·메인 상태·전환 결과는 기존 Gateway Prometheus metric(`main_model_operation_state`,
  request gate, switch/rollback totals, 마지막 전환 시간)으로 관측한다.
