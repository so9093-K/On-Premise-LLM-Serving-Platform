# Gemma 4 MTP Speculative Decoding 운영 정책

## 개요

Gemma 4 26B-A4B (`RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic`)은 vLLM의 Multi-Token Prediction(MTP) speculative decoding을 지원한다.
이 문서는 이 플랫폼의 MTP 정책, 설정 근거, 운영 주의사항을 정리한다.

---

## 모델 구성

| 역할 | 모델 |
|---|---|
| **Target model** (추론 대상) | `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` |
| **MTP drafter/speculator** | `google/gemma-4-26B-A4B-it-assistant` |

> **중요**: `google/gemma-4-26B-A4B-it-assistant`의 `assistant`라는 이름은 채팅 assistant를 의미하지 않는다.
> 이 모델은 Gemma 4 아키텍처의 Multi-Token Prediction drafter/speculator 역할을 하는 lightweight 보조 모델이다.
> 약 839MB safetensors, 4-layer 구조로 target model과 별개로 VRAM을 추가로 사용한다.

---

## 기본 정책

| Profile | MTP 상태 | num_speculative_tokens | 비고 |
|---|---|---|---|
| `balanced` | **ON** | 2 | 기본 운영 profile |
| `latency` | **ON** | 2 | 저지연 우선 profile |
| `long_context` | **OFF** | — | KV cache/context headroom 보호 |
| `debug` | **OFF** | — | 장애 격리 용이성 |

### num_speculative_tokens=2 선택 근거

내부 실측 데이터 (Gemma 4 26B-A4B 계열, `vllm/vllm-openai:gemma4-0505-cu129`):

- **실패 요청 수**: 0
- **output token throughput 개선**: 약 35%
- **acceptance rate**: 약 70.78%
- **position 1 acceptance**: 약 62.49% (두 번째 speculative token이 충분히 채택됨)
- γ=5는 실측에서 불안정하고 평균 t/s가 오히려 낮았음

`long_context` profile에서 MTP를 OFF로 유지하는 이유: speculative decoding은 drafter 추론을 위한 KV cache headroom을 추가로 사용하므로, 긴 컨텍스트 처리 시 effective context가 줄어들 수 있다.

---

## vLLM 설정

Source of truth: `configs/model_serving.yaml` → `models.main_llm.runtime_features.speculative_decoding`

렌더링된 vLLM CLI 인자:
```
--speculative-config '{"method":"mtp","model":"google/gemma-4-26B-A4B-it-assistant","num_speculative_tokens":2}'
```

`--speculative-config` JSON 필드 규칙:
- `method`: 반드시 `"mtp"` (Gemma 4 assistant checkpoint는 MTP 전용)
- `model`: MTP drafter 모델 경로
- `num_speculative_tokens`: 양의 정수, 현재 기본값 2
- target runtime 전용 키(`tensor_parallel_size`, `max_model_len`, `gpu_memory_utilization` 등)는 이 JSON에 포함하지 않는다

---

## Side Effects (운영 주의사항)

### 1. 추가 VRAM 사용

MTP drafter(`google/gemma-4-26B-A4B-it-assistant`, 약 839MB)가 target model과 함께 GPU에 로드된다.
현재 `gpu_memory_utilization: 0.72` 설정은 drafter를 포함한 headroom을 고려한 보수적 값이다.
이 값을 올리기 전에 drafter 포함 실제 GPU 메모리 사용량을 확인해야 한다.

### 2. Effective KV cache / context headroom 감소 가능

Speculative decoding은 drafter 추론을 위한 KV cache를 추가로 사용할 수 있다.
이로 인해 실제 사용 가능한 최대 context가 설정값(`max_model_len: 16384`)보다 줄어들 수 있다.
이것이 `long_context` profile에서 MTP를 OFF로 유지하는 주된 이유다.

### 3. TTFT/ITL 지표 해석 변화

MTP가 활성화되면 TTFT(Time to First Token)와 ITL(Inter-Token Latency)의 분포가 변한다:
- acceptance가 발생하면 여러 token이 한 번에 생성되어 ITL이 불규칙하게 보일 수 있다
- Grafana/Prometheus 지표를 해석할 때 MTP ON/OFF 상태를 함께 확인해야 한다

---

## 품질 보증에 대한 주의

Speculative decoding은 target model이 drafter의 제안을 검증(verification)하는 방식으로 작동하며,
이론적으로는 autoregressive 생성과 동일한 token 분포를 목표로 한다.

그러나 실제 serving 환경에서는 다음 요인으로 인해 검증이 필요하다:

- floating point 비결정성
- batch 크기와 sampling 조건 차이
- runtime 이미지(`vllm/vllm-openai:gemma4-0505-cu129`) 버전 의존성

**"MTP는 품질 변화가 없다"고 단정하지 않는다.** 운영자는 새 vLLM 이미지로 업그레이드하거나
drafter 모델을 변경할 때마다 아래 GPU 런타임 검증 항목을 수행해야 한다.

---

## GPU 런타임 검증 항목

MTP 활성화 후 실제 GPU 서버에서 확인해야 할 항목:

1. **startup log 확인**: vLLM이 MTP drafter를 정상 로드했는지, `speculative_config` 적용 메시지 확인
2. **`/v1/models` 응답**: `local-main` 모델 존재 확인
3. **non-stream 기본 채팅**: 정상 응답 확인
4. **stream 채팅**: SSE relay 정상 동작 확인
5. **VLM (이미지 입력)**: image + text 입력에서 speculative decoding 동작 확인
6. **tool calling**: gemma4 tool_call_parser 정상 동작 확인
7. **reasoning parser**: `reasoning=true` 요청에서 gemma4 reasoning_parser 정상 동작 확인
8. **structured output canary**: json_schema response_format 정상 동작 확인
9. **GPU 메모리 사용량**: `nvidia-smi`로 drafter 포함 실제 VRAM 사용량 확인
10. **TTFT/ITL 지표**: Grafana에서 MTP 활성화 전후 지표 비교

### 실패로 판단해야 하는 경우

- vLLM startup log에 MTP drafter 로드 오류가 있는 경우
- `--speculative-config` 인자가 누락되어 generic draft_model 경로로 처리되는 경우
- `method: mtp`가 빠지고 다른 speculative method가 적용되는 경우
- acceptance rate가 0에 가깝거나 실패 요청이 증가하는 경우

---

## 설정 변경 절차

MTP 한도를 바꾸거나 OFF로 전환할 때:

1. `configs/model_serving.yaml`의 `speculative_decoding` 블록만 수정
2. `ops/compose/full-stack.private-network.yaml`의 `--speculative-config` 값 동기화
3. `make validate`로 config-compose 정합성 확인
4. `make test`로 전체 테스트 통과 확인
5. 실제 GPU 서버에서 위 검증 항목 수행

`configs/gpu_budgets.yaml`은 이미지 한도의 source of truth이며 speculative decoding 설정과는 별도 관리된다.

---

## 관련 파일

| 파일 | 역할 |
|---|---|
| `configs/model_serving.yaml` | 런타임 활성화 여부 source of truth |
| `configs/model_catalog.yaml` | MTP 호환성 metadata (source_facts) |
| `ops/compose/full-stack.private-network.yaml` | 실배포 compose command |
| `src/ai_model_serving/runtime_validation/speculative_validation.py` | 순수 검증 로직 |
| `src/ai_model_serving/runtime_validation/vllm_commands.py` | CLI 렌더링 |
| `scripts/compose/validate_vllm_compose.py` | config-compose 정합성 검증 |
| `docs/adr/0014-image-validation-policy.md` | 관련 ADR (이미지 정책) |

---

*이 문서의 runtime 설정값 source of truth는 `configs/model_serving.yaml`이다. 이 문서의 예시 값이 YAML과 다를 경우 YAML이 우선한다.*
