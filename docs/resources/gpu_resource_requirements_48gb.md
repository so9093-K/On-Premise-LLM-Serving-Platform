# 인프라 리소스 요구사항 분석서

## 48GB GPU 단일 환경에서의 다중 vLLM 모델 서빙 리소스 검토

| 문서 항목 | 내용 |
|---|---|
| 산출물명 | 인프라 리소스 요구사항 분석서 |
| 프로젝트 구분 | AI 모델 서빙 인프라 검토 |
| 검토 대상 | 48GB VRAM 단일 GPU 환경 |
| 검토 범위 | Main LLM, Embedding, Prompt Risk 모델의 vLLM 기반 동시 상주 및 운영 제약 |
| 작성 목적 | 다중 AI 모델을 단일 GPU에 상주시킬 때 필요한 VRAM budget, 설정 조건, 운영 가능 범위, 리스크를 정리한다. |
| 문서 성격 | 프로젝트 기술 산출물 / 인프라 리소스 산정 자료 |
| 기준일 | 2026-05-04 |

---

## 1. 문서 목적

본 문서는 48GB VRAM 단일 GPU 환경에서 복수의 AI 모델을 vLLM 기반으로 상주시켜 운영할 경우 필요한 GPU 리소스 요구사항을 정리하기 위한 프로젝트 산출물이다.

본 분석은 다음 사항을 명확히 하는 것을 목적으로 한다.

| 검토 항목 | 목적 |
|---|---|
| 모델별 VRAM 요구량 | 각 모델을 vLLM으로 상주시킬 때 필요한 메모리 budget을 산정한다. |
| 전체 합산 가능성 | 48GB 단일 GPU 내에서 전체 모델 구성이 성립하는지 검토한다. |
| 운영 제약 조건 | context length, 동시성, KV cache, reserve 조건을 정의한다. |
| Prompt Risk 모델 직렬화 영향 | Prompt Risk 모델 2개가 동시에 실행되지 않는 경우 runtime peak와 resident memory를 구분한다. |
| 적용 가능 범위 | controlled-runtime, 제한적 운영, 안정 운영 관점의 적용 가능성을 구분한다. |

---

## 2. 검토 대상 모델 구성

| 구분 | 모델 | 주요 역할 | 실행 방식 |
|---|---|---|---|
| Main LLM | `QuantTrio/gemma-4-31B-it-AWQ` | 채팅 및 응답 생성 | vLLM generation |
| Embedding | `google/embeddinggemma-300m` | RAG embedding 및 검색 벡터화 | vLLM pooling / embedding |
| Prompt Risk 1 | `kakaocorp/kanana-safeguard-prompt-2.1b` | 프롬프트 공격 탐지 | vLLM generation / classification-style |
| Prompt Risk 2 | `kakaocorp/kanana-safeguard-siren-8b` | 법적·정책적 위험 탐지 | vLLM generation / classification-style |

---

## 3. 분석 전제

| 전제 항목 | 내용 |
|---|---|
| GPU 구성 | 48GB VRAM 단일 GPU |
| 모델 상주 방식 | 모든 모델을 vLLM 기반으로 GPU에 상주시킨다. |
| 인스턴스 구성 | Main LLM, Embedding, Prompt Risk 모델은 각각 독립된 vLLM 인스턴스로 운용한다. |
| Prompt Risk 실행 방식 | Prompt Risk 1과 Prompt Risk 2는 동시에 실행하지 않고 요청 흐름상 직렬로 사용한다. |
| Prompt Risk 상주 방식 | 두 Prompt Risk 모델이 모두 vLLM 서버로 awake 상태라면 weight와 executor 메모리는 모두 GPU에 상주한다. |
| 운영 기준 | 모델 weight뿐 아니라 KV cache, CUDA context, executor overhead, allocator fragmentation, runtime peak reserve를 포함한다. |

---

## 4. 공식 자료 기반 모델 사실 요약

| 구분 | 모델 | 기반 / 모델 성격 | 주요 스펙 | vLLM 관련 사항 | 리소스 해석상 주의점 |
|---|---|---|---|---|---|
| Main LLM | `QuantTrio/gemma-4-31B-it-AWQ` | `google/gemma-4-31B-it` 기반 AWQ 4-bit quantized 모델 | 모델 파일 크기 약 20GiB | 모델 카드 예시에서 `max-model-len 32768`, `max-num-seqs 32`, `gpu-memory-utilization 0.9`, `tensor-parallel-size 2` 사용 | 20GiB는 모델 파일 크기이며, vLLM 실행 시 전체 VRAM 요구량은 아니다. 단일 GPU에서는 `tensor-parallel-size 1` 운용이 필요하다. |
| Embedding | `google/embeddinggemma-300m` | Google 경량 embedding 모델 | 308M parameters, 2K context window, output embedding dimension 768 | vLLM pooling / embedding 모델로 사용 가능 | 모델 자체는 작지만 별도 vLLM 인스턴스로 올리면 CUDA context와 executor overhead가 추가된다. |
| Prompt Risk 1 | `kakaocorp/kanana-safeguard-prompt-2.1b` | Kanana 2.1B 기반 프롬프트 공격 탐지 모델 | Prompt Injection, Prompt Leaking 등 위험 분류 | vLLM generation 또는 classification-style 호출 가능 | 출력이 `<SAFE>`, `<UNSAFE-A1>` 같은 단일 토큰 형식이므로 generation 비용은 낮다. |
| Prompt Risk 2 | `kakaocorp/kanana-safeguard-siren-8b` | Kanana 8B 기반 법적·정책적 위험 탐지 모델 | 사용자 발화의 법적·정책적 주의 필요 여부 분류 | vLLM generation 또는 classification-style 호출 가능 | 4bit라도 8B 모델이므로 보조 모델 중 VRAM 부담이 가장 크다. |

---

## 5. vLLM 기반 GPU 메모리 구성

vLLM 기반 모델 서빙 시 GPU VRAM 사용량은 단순히 모델 weight 크기만으로 결정되지 않는다.

| 구성 항목 | 설명 | 주요 영향 요인 | 비고 |
|---|---|---|---|
| Model weight | 모델 파라미터 자체가 차지하는 메모리 | 모델 크기, precision, quantization 방식 | 가장 기본적인 VRAM 사용량 |
| Quantization metadata / scale / zero-point | 4bit, AWQ, GPTQ, BNB 등 양자화 모델에서 필요한 부가 메타데이터 | quantization 방식, group size, scale 저장 방식 | weight가 4bit로 줄어도 부가 메타데이터는 추가로 필요 |
| vLLM executor | vLLM이 모델 실행을 관리하기 위해 사용하는 런타임 메모리 | vLLM 인스턴스 수, 모델 구조, 실행 옵션 | 모델별 vLLM 서버를 따로 띄우면 인스턴스별로 발생 |
| CUDA context / kernel / graph | CUDA 실행 환경, 커널, CUDA graph 등이 사용하는 메모리 | GPU 프로세스 수, CUDA graph 사용 여부, PyTorch/vLLM runtime | 여러 vLLM 프로세스를 띄우면 중복 overhead 발생 가능 |
| KV cache reservation | Attention 연산을 위해 key/value cache를 저장하는 영역 | `max_model_len`, `max_num_seqs`, batch size, 동시 요청 수 | LLM context length와 동시성에 가장 크게 영향받음 |
| Activation / temporary workspace | 추론 중간 activation, prefill/decode 과정의 임시 작업 공간 | 입력 길이, batch 크기, prefill token 수, 모델 크기 | 실행 순간에 증가하는 peak 메모리 |
| Allocator fragmentation | CUDA/PyTorch 메모리 allocator에서 발생하는 단편화 및 미사용 예약 영역 | 프로세스 수, 반복 할당/해제, 모델 여러 개 상주 여부 | 이론 계산보다 실제 사용량이 커지는 주요 원인 |
| Runtime peak reserve | 순간적인 peak, 요청 burst, 스케줄링 변동에 대비한 여유 공간 | 동시 요청, 긴 prompt, embedding/RAG 동시 실행 여부 | OOM 방지를 위해 반드시 확보 필요 |

---

## 6. Prompt Risk 모델 직렬화 영향

본 프로젝트에서는 Prompt Risk 1과 Prompt Risk 2가 동시에 추론되지 않고, 요청 흐름상 직렬로 실행되는 구조를 전제로 한다.

### 6.1 직렬화로 감소하는 항목

| 항목 | 영향 |
|---|---|
| 두 Prompt Risk 모델의 동시 activation peak | 감소 |
| 두 Prompt Risk 모델의 동시 temporary workspace | 감소 |
| 두 Prompt Risk 모델의 동시 compute contention | 감소 |
| runtime peak | `Prompt Risk 1 + Prompt Risk 2`가 아니라 `max(Prompt Risk 1, Prompt Risk 2)`에 가까움 |

### 6.2 직렬화로 감소하지 않는 항목

| 항목 | 영향 |
|---|---|
| Prompt Risk 1 model weight | 감소하지 않음 |
| Prompt Risk 2 model weight | 감소하지 않음 |
| 각 vLLM executor | 감소하지 않음 |
| 각 CUDA context | 감소하지 않음 |
| 각 모델의 KV cache 예약분 | 대부분 감소하지 않음 |
| allocator fragmentation | 감소하지 않음 |

Prompt Risk 모델 2개를 모두 vLLM 서버로 awake 상태에 둘 경우 resident VRAM은 두 모델 모두 포함하여 산정해야 한다. 다만 실행 순간의 runtime peak는 두 모델이 동시에 실행되는 경우보다 낮게 볼 수 있다.

---

## 7. 모델별 리소스 요구사항

### 7.1 Main LLM: `QuantTrio/gemma-4-31B-it-AWQ`

| 항목 | 값 |
|---|---:|
| 모델 파일 크기 | 약 20GiB |
| Quant 방식 | AWQ 4bit |
| vLLM 상주 최소권 | 약 24~26GiB |
| 4개 모델 공존 시 권장 운영 budget | 약 27~29GiB |
| 리소스 성격 | 전체 구성의 지배 항목 |

| 설정 항목 | 권장 시작값 |
|---|---:|
| `max_model_len` | 8192 |
| `max_num_seqs` | 1 |
| `max_num_batched_tokens` | 4096~8192 |
| 동시 요청 | 낮은 수준으로 제한 |
| 32K context | 비권장 |

### 7.2 Embedding: `google/embeddinggemma-300m`

| 항목 | 값 |
|---|---:|
| 모델 규모 | 308M parameters |
| 입력 context | 2048 tokens |
| output embedding dimension | 768 |
| raw BF16 weight 하한 | 약 0.57GiB |
| vLLM pooling 운영 budget | 약 1.5~2GiB |
| 리소스 성격 | 상시 공존 가능 |

### 7.3 Prompt Risk 1: `kakaocorp/kanana-safeguard-prompt-2.1b`

| 항목 | 값 |
|---|---:|
| 모델 규모 | 2.1B |
| Quant 방식 | BNB 4bit 권장 |
| raw 4bit weight 하한 | 약 0.98GiB |
| vLLM 4bit 운영 budget | 약 2.5~3.5GiB |
| 출력 형태 | 단일 토큰 |
| 리소스 성격 | 비교적 낮은 부담 |

### 7.4 Prompt Risk 2: `kakaocorp/kanana-safeguard-siren-8b`

| 항목 | 값 |
|---|---:|
| 모델 규모 | 8B |
| Quant 방식 | BNB 4bit 권장 |
| raw 4bit weight 하한 | 약 3.73GiB |
| vLLM 4bit 운영 budget | 약 6.5~8GiB |
| 출력 형태 | 단일 토큰 |
| 리소스 성격 | 보조 모델 중 가장 큰 부담 |

---

## 8. 전체 리소스 종합

| 구분 | 모델 | 실행 방식 | Quant | 권장 VRAM Budget | 운영 판단 |
|---|---|---|---|---:|---|
| Main LLM | `QuantTrio/gemma-4-31B-it-AWQ` | vLLM generation | AWQ 4bit | 27~29GiB | 가능하나 전체 리소스 대부분 점유 |
| Embedding | `google/embeddinggemma-300m` | vLLM pooling | BF16 / 저정밀 | 1.5~2GiB | 상시 공존 가능 |
| Prompt Risk 1 | `kakaocorp/kanana-safeguard-prompt-2.1b` | vLLM generation | BNB 4bit 권장 | 2.5~3.5GiB | 상시 공존 가능 |
| Prompt Risk 2 | `kakaocorp/kanana-safeguard-siren-8b` | vLLM generation | BNB 4bit 권장 | 6.5~8GiB | 가능하나 타이트 |
| Reserve | CUDA / allocator / fragmentation / peak | - | - | 4~6GiB | 반드시 필요 |
| **총합** | - | - | - | **41.5~48.5GiB** | 48GiB 내 성립 가능하나 상단값은 OOM 경계 |

---

## 9. 총합 산정

| 산정 기준 | Main LLM | Embedding | Prompt Risk 1 | Prompt Risk 2 | Reserve | 총합 | 판단 |
|---|---:|---:|---:|---:|---:|---:|---|
| 최소권 | 27GiB | 1.5GiB | 2.5GiB | 6.5GiB | 4GiB | 41.5GiB | 성립 |
| 중간권 | 28GiB | 2GiB | 3GiB | 7.2GiB | 5GiB | 45.2GiB | 성립하나 여유 작음 |
| 상단권 | 29GiB | 2GiB | 3.5GiB | 8GiB | 6GiB | 48.5GiB | 48GiB 초과 / OOM 경계 |

---

## 10. 48GiB 기준 잔여 VRAM

| 산정 기준 | 총합 | 48GiB 기준 잔여 | 판단 |
|---|---:|---:|---|
| 최소권 | 41.5GiB | 6.5GiB | 안정권에 가까움 |
| 중간권 | 45.2GiB | 2.8GiB | 가능하나 타이트 |
| 상단권 | 48.5GiB | -0.5GiB | 초과 |

---

## 11. 권장 `gpu-memory-utilization` 분배

| 구분 | Main LLM | Embedding | Prompt Risk 1 | Prompt Risk 2 | 합계 | 잔여 |
|---|---:|---:|---:|---:|---:|---:|
| 권장 운영 분배 | 0.58 | 0.04 | 0.065 | 0.18 | 0.865 | 0.135 |
| 48GiB 환산 | 27.84GiB | 1.92GiB | 3.12GiB | 8.64GiB | 41.52GiB | 6.48GiB |
| 상한 허용 분배 | 0.58 | 0.04 | 0.065 | 0.20 | 0.885 | 0.115 |
| 48GiB 환산 | 27.84GiB | 1.92GiB | 3.12GiB | 9.60GiB | 42.48GiB | 5.52GiB |
| 비권장 분배 | 0.62 | 0.05 | 0.08 | 0.17 | 0.92 | 0.08 |
| 48GiB 환산 | 29.76GiB | 2.40GiB | 3.84GiB | 8.16GiB | 44.16GiB | 3.84GiB |

총 utilization 0.90 이상은 4개 vLLM 인스턴스 구조에서 권장하지 않는다. 잔여 3~4GiB 수준은 CUDA context, fragmentation, runtime peak를 고려할 때 부족할 수 있다.

### 11.1 `--enforce-eager` 적용 (risk 모델)

risk-prompt-vllm 및 risk-siren-vllm 에는 `--enforce-eager` 를 적용한다. 이 플래그는 CUDA graph pre-capture 를 비활성화한다.

| 적용 이유 | 설명 |
|---|---|
| CUDA graph 메모리 절약 | vLLM 기본값은 여러 batch size 에 대해 CUDA graph 를 사전 캡처한다. 모델당 300~500MiB 추가 VRAM 을 소비하며, 4개 인스턴스가 동시에 기동하면 합산 overhead 가 1~2GiB 에 달한다. |
| risk 모델 특성 | `max_num_seqs=1`, `max_output_tokens=1` 로 고정된 단일 토큰 분류기에서 CUDA graph 가 주는 latency 이점은 없다. |
| 기동 순서 안전성 | CUDA graph capture 는 기동 중 메모리 spike 를 발생시킨다. enforce-eager 는 이 spike 를 제거하여 4개 모델 순차 기동 중 OOM 위험을 낮춘다. |

### 11.2 vLLM 기동 순서 직렬화 (compose)

4개 vLLM 인스턴스가 동시에 기동하면 weight loading peak(BF16 경유 bitsandbytes 양자화) 가 겹쳐 OOM 가능성이 높아진다. compose 에서 `depends_on: condition: service_healthy` 로 순차 기동을 강제한다.

```
main-llm-vllm (healthy) → embedding-vllm (healthy) → risk-siren-vllm (healthy) → risk-prompt-vllm (healthy) → risk-adapter, gateway
```

risk-siren-vllm 을 risk-prompt-vllm 보다 먼저 기동하는 이유: 8B 모델이 더 크므로 다른 모델이 이미 상주한 상태에서 기동하면 peak 가 줄어든다.

---

## 12. 운영 제한 조건

| 항목 | 제한 조건 |
|---|---|
| Main LLM context | 8K부터 시작, 16K 이상은 실측 후 판단 |
| Main LLM 동시성 | `max_num_seqs=1`부터 시작 |
| Prompt Risk 모델 context | 1K~2K 수준 권장 |
| Prompt Risk 모델 출력 | 단일 토큰 제한 |
| 전체 utilization | 0.83~0.87 권장, `risk-siren` KV cache 실패 시 검증된 예외로 0.885까지 허용 |
| reserve | 최소 4GiB, 권장 6GiB 이상 |
| 32K context | 단일 GPU 4모델 상주 구조에서는 비권장 |
| sleep/wake | 매 요청마다 호출되는 모델에는 비권장 |

---

## 13. 권장 요청 처리 구조

조건부 2차 검사가 없는 경우, 매 요청마다 사용하는 Prompt Risk 모델은 sleep/wake 방식으로 운용하지 않는 것이 적절하다.

```text
사용자 요청
→ Prompt Risk 1 또는 Prompt Risk 2
→ 정책상 통과
→ Main LLM 생성
→ 필요한 경우 Embedding / RAG 사용
```

Prompt Risk 1과 Prompt Risk 2가 동시에 실행되지 않는다면 runtime peak는 다음처럼 산정할 수 있다.

```text
동시 실행인 경우:
Prompt Risk 1 activation + Prompt Risk 2 activation

직렬 실행인 경우:
max(Prompt Risk 1 activation, Prompt Risk 2 activation)
```

다만 두 모델이 모두 vLLM 서버로 떠 있다면 resident memory는 다음처럼 유지된다.

```text
Prompt Risk 1 resident memory
+ Prompt Risk 2 resident memory
```

---

## 14. 리스크 및 대응 방안

| 리스크 | 원인 | 영향 | 대응 방안 |
|---|---|---|---|
| OOM 발생 | 전체 utilization 과다, reserve 부족 또는 모델별 KV cache budget 부족 | 모델 로드 실패 또는 추론 중단 | utilization 합계를 0.83~0.87 수준에서 시작하고, `risk-siren` KV cache 실패 시 해당 runtime budget을 0.20까지 올린다. |
| KV cache 부족 | LLM context 또는 동시성 증가 | preemption / recompute / latency 증가 | `max_model_len`, `max_num_seqs`, `max_num_batched_tokens`를 제한한다. |
| fragmentation 증가 | 여러 vLLM 인스턴스 동시 상주 | 이론치보다 VRAM 사용량 증가 | reserve를 최소 4GiB, 권장 6GiB 이상 확보한다. |
| latency 증가 | Prompt Risk 모델 직렬 실행 | 요청 처리 시간이 증가 | Prompt Risk 출력 토큰 수와 context를 최소화한다. |
| 확장성 부족 | 단일 GPU에 모든 모델 집중 | 동시 요청 증가 시 처리 한계 | 안정 운영 단계에서는 보조 모델 분리 또는 추가 GPU 구성을 검토한다. |

---

## 15. 검증 기준

| 검증 항목 | 기준 |
|---|---|
| 모델 로드 성공 여부 | 4개 vLLM 인스턴스가 지정된 utilization 내에서 정상 기동해야 한다. |
| 초기 VRAM 사용량 | 기동 직후 총 사용량이 40~42GiB 수준을 초과하지 않는 것이 바람직하다. |
| 요청 처리 안정성 | Prompt Risk 실행 후 Main LLM 생성까지 OOM 없이 수행되어야 한다. |
| KV cache 안정성 | LLM 요청 처리 중 preemption 또는 recompute 로그가 과도하게 발생하지 않아야 한다. |
| context 확장성 | 8K 기준 안정화 후 12K, 16K 확장 가능 여부를 실측한다. |
| 동시성 한계 | `max_num_seqs=1` 기준 안정화 후 제한적으로 상향 가능성을 검토한다. |

---

## 16. 최종 판단

48GB 단일 GPU에서 `QuantTrio/gemma-4-31B-it-AWQ`, `google/embeddinggemma-300m`, `kakaocorp/kanana-safeguard-prompt-2.1b`, `kakaocorp/kanana-safeguard-siren-8b`를 모두 vLLM으로 상주시켜 사용하는 구성은 리소스 budget상 성립 가능하다.

다만 성립 조건은 명확하다.

```text
1. risk-prompt-vllm, risk-siren-vllm 에 --enforce-eager 를 적용하여 CUDA graph 기동 spike 를 제거한다.
2. 4개 vLLM 인스턴스를 순차 기동한다 (compose depends_on: service_healthy 체인).
3. 4개 vLLM 인스턴스의 gpu-memory-utilization 합계를 0.865 수준에서 시작한다. `risk-siren` KV cache 초기화 실패 재현 시 0.20 으로 복원(총합 0.885)한다.
4. Main LLM은 8K context, max_num_seqs 1부터 시작한다.
5. Prompt Risk 모델은 1K~2K context, max_num_seqs 1로 제한한다.
6. Prompt Risk 1과 Prompt Risk 2는 직렬 실행하여 runtime peak를 분리한다.
7. 최소 4GiB, 권장 6GiB 이상의 reserve를 확보한다.
```

권장 최종 budget은 다음과 같다.

```text
Main LLM        27.84GiB  (util=0.58, --enable-prefix-caching)
Embedding        1.92GiB  (util=0.04)
Prompt Risk 1    3.12GiB  (util=0.065, --enforce-eager)
Prompt Risk 2    8.64GiB  (util=0.18,  --enforce-eager)
Reserve          6.48GiB
----------------------------
총합             약 41.5GiB + runtime peak reserve 포함
```

본 구성은 제한된 context와 낮은 concurrency 조건에서 48GB 단일 GPU 리소스 budget상 성립하는 구성으로 판단한다.

반대로 다음 조건을 요구하는 경우 48GB 단일 GPU 구성은 성립하기 어렵다.

```text
1. Main LLM 32K context
2. 높은 동시 요청 처리
3. 전체 gpu-memory-utilization 0.90 이상
4. 4개 vLLM 인스턴스 기본값 사용
5. reserve 4GiB 미만
```

최종 결론은 다음과 같다.

> 48GB 단일 GPU에서 31B AWQ LLM, 300M Embedding, 2.1B Prompt Risk, 8B Siren을 모두 vLLM으로 상주시킬 수 있다. 단, Main LLM의 context와 concurrency를 강하게 제한하고, 전체 vLLM memory utilization은 0.83~0.87에서 시작하되 `risk-siren` KV cache 초기화 실패가 재현되면 0.885 reference로 조정한다. Prompt Risk 모델 직렬화는 runtime peak에는 유리하지만, resident VRAM budget은 두 모델 모두 포함해서 산정해야 한다.

---

## 17. 프로젝트 적용 범위

| 적용 단계 | 판단 |
|---|---|
| controlled-runtime | 적용 가능 |
| 내부 제한 운영 | 적용 가능 |
| 낮은 동시성 서비스 | 조건부 적용 가능 |
| 높은 동시성 서비스 | 단일 48GB GPU로는 부적합 |
| 장기 안정 운영 | 보조 모델 분리 또는 추가 GPU 검토 필요 |

---

## 18. 부록: 권장 실행 설정 예시

### 18.1 Main LLM

```bash
vllm serve QuantTrio/gemma-4-31B-it-AWQ \
  --host 0.0.0.0 \
  --port 8000 \
  --tensor-parallel-size 1 \
  --quantization awq \
  --dtype half \
  --max-model-len 8192 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 4096 \
  --gpu-memory-utilization 0.58 \
  --trust-remote-code
```

### 18.2 Embedding

```bash
vllm serve google/embeddinggemma-300m \
  --host 0.0.0.0 \
  --port 8001 \
  --runner pooling \
  --max-model-len 2048 \
  --max-num-seqs 4 \
  --gpu-memory-utilization 0.04
```

### 18.3 Prompt Risk 1

```bash
vllm serve kakaocorp/kanana-safeguard-prompt-2.1b \
  --host 0.0.0.0 \
  --port 8002 \
  --quantization bitsandbytes \
  --load-format bitsandbytes \
  --dtype bfloat16 \
  --max-model-len 2048 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 2048 \
  --gpu-memory-utilization 0.065 \
  --enforce-eager \
  --trust-remote-code
```

### 18.4 Prompt Risk 2 / Siren

```bash
vllm serve kakaocorp/kanana-safeguard-siren-8b \
  --host 0.0.0.0 \
  --port 8003 \
  --quantization bitsandbytes \
  --load-format bitsandbytes \
  --dtype bfloat16 \
  --max-model-len 2048 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 2048 \
  --gpu-memory-utilization 0.18 \
  --enforce-eager
```
