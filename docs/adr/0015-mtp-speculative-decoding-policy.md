# ADR-0015: Gemma 4 MTP Speculative Decoding 정책

## Status

Accepted

## Context

Gemma 4 26B-A4B(`RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic`)는 vLLM의 Multi-Token Prediction(MTP) speculative decoding을 지원한다.
이 아키텍처에서는 별도의 lightweight drafter 모델이 다음 토큰 후보를 먼저 제안하고, target model이 이를 한 번에 검증(verification)하는 방식으로 처리량을 높인다.

### 도입 동기

- 현재 구성(`max_model_len: 16384`, `max_num_seqs: 1`)에서 single-sequence throughput이 병목이다.
- speculative decoding은 target 모델 weight 변경 없이 throughput을 개선할 수 있는 몇 안 되는 방법 중 하나다.
- Gemma 4 아키텍처는 MTP-aware drafter(`google/gemma-4-26B-A4B-it-assistant`)를 공식 제공한다.

### drafter 모델 명칭 혼동

`google/gemma-4-26B-A4B-it-assistant`의 `assistant`라는 이름은 일반 채팅 assistant를 연상시키지만,
이 모델은 Gemma 4 아키텍처의 MTP speculative drafter/speculator 전용 체크포인트다.
약 839MB safetensors, 4-layer 구조로 단독 추론에는 적합하지 않다.

### 실측 데이터 (Gemma 4 26B-A4B, `vllm/vllm-openai:gemma4-0505-cu129`)

| 지표 | 결과 |
|---|---|
| γ=2, 실패 요청 | 0개 |
| output token throughput 개선 | 약 35% |
| acceptance rate | 약 70.78% |
| position 1 acceptance | 약 62.49% |

γ=5는 동일 실측에서 불안정하고 평균 t/s가 오히려 낮았다.

### Speculative Decoding이 적합하지 않은 경우

- **long_context 처리**: drafter 추론이 KV cache headroom을 추가 소비할 수 있어 effective context가 줄어들 수 있다.
- **장애 격리 중**: speculative decoding 활성화 여부가 디버깅 변수가 되어 원인 파악을 어렵게 만든다.

## Decision

### Method 선택: MTP

`google/gemma-4-26B-A4B-it-assistant`는 Gemma 4 MTP 전용 체크포인트이므로
`--speculative-config` JSON에 반드시 `"method": "mtp"`를 명시한다.
이를 generic `draft_model` 경로로 처리하면 vLLM이 잘못된 speculative 방식을 적용할 수 있다.

### num_speculative_tokens 기본값: 2

실측에서 γ=2가 acceptance rate 70.78%, throughput +35%로 가장 안정적이었다.
γ=5는 불안정했으므로, 기본값을 흔히 권장하는 1이 아닌 2로 시작한다.

### Profile 정책

| Profile | MTP | 이유 |
|---|---|---|
| `balanced` | ON, γ=2 | 기본 운영 profile |
| `latency` | ON, γ=2 | 저지연 우선 profile |
| `long_context` | **OFF** | KV cache/context headroom 감소 위험 |
| `debug` | **OFF** | 장애 격리 용이성 |

### 렌더링된 CLI 인자

```
--speculative-config '{"method":"mtp","model":"google/gemma-4-26B-A4B-it-assistant","num_speculative_tokens":2}'
```

`--speculative-config` JSON에 허용되는 키는 `method`, `model`, `num_speculative_tokens`뿐이다.
target runtime 전용 키(`tensor_parallel_size`, `max_model_len`, `gpu_memory_utilization` 등)는 이 JSON에 포함하지 않는다.

### Source of Truth 위치

| 데이터 | 위치 |
|---|---|
| 런타임 활성화 여부, method, drafter, γ | `configs/model_serving.yaml` |
| MTP 호환성 metadata (크기, 역할 등) | `configs/model_catalog.yaml` source_facts |
| Profile별 ON/OFF 정책 | `configs/model_serving.yaml` speculative_decoding.profiles |

`configs/model_catalog.yaml`에는 runtime enable/disable truth source를 두지 않는다.

### 품질 보증에 대한 입장

Speculative decoding은 target verification 구조상 autoregressive 생성과 동일한 token 분포를 목표로 하지만,
floating point 비결정성, batch 조건, runtime 이미지 버전 차이로 인해 결과가 완전히 동일하다고 단정하지 않는다.
새 vLLM 이미지로 업그레이드하거나 drafter를 변경할 때마다 GPU 런타임 검증이 필요하다.

## Consequences

| Positive | Negative |
|---|---|
| output token throughput 약 35% 개선 (γ=2 실측) | MTP drafter 약 839MB 추가 VRAM 소비 |
| target model weight 변경 없음 | effective KV cache/context headroom 감소 가능 |
| long_context/debug profile에서 선택적 비활성화 가능 | TTFT/ITL 지표 해석이 달라질 수 있음 |
| config-compose 정합성이 테스트로 보장됨 | 새 vLLM 이미지마다 acceptance rate 재검증 필요 |

## Operational impact

- `gpu_memory_utilization: 0.72`는 drafter 포함 headroom을 고려한 값이다. 올리기 전에 실 GPU 메모리 사용량을 확인해야 한다.
- Grafana/Prometheus TTFT·ITL 지표는 MTP acceptance 발생 시 여러 토큰이 한 번에 생성되어 불규칙하게 보일 수 있다. 지표 해석 시 MTP ON/OFF 상태를 함께 확인한다.
- vLLM startup log에서 MTP drafter 로드 성공 메시지를 반드시 확인한다.

## Migration notes

- `configs/model_serving.yaml`의 `speculative_decoding.enabled`를 `false`로 바꾸면 즉시 비활성화된다.
- 비활성화 시 `ops/compose/full-stack.private-network.yaml`에서 `--speculative-config` 줄을 제거하고 `validate_vllm_compose.py`를 통과시켜야 한다.
- drafter 모델 변경 시: YAML의 `mtp_drafter_model`, compose의 JSON, model_catalog.yaml source_facts를 동시에 업데이트한다.

## Related

- `configs/model_serving.yaml` — runtime source of truth
- `src/ai_model_serving/runtime_validation/speculative_validation.py` — 검증 로직
- `scripts/compose/validate_vllm_compose.py` — config-compose 정합성 검증
- `docs/operations/speculative_decoding_mtp.md` — 운영 정책 문서
- ADR-0014: Vision 이미지 검증 정책 (동일 세션에서 확립된 image limit 정책)
