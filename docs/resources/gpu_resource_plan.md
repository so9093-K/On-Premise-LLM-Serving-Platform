# GPU Resource Plan

## 1. 범위

본 문서는 RTX 6000 Ada 48GB 단일 GPU에서 enabled vLLM 모델 4개를 동시에 상주시킬 때의 보수적 리소스 배분 기준을 정의한다. `risk-siren`은 retired 상태이며 기본 runtime budget 합계에서 제외한다.

## 2. 모델별 Budget

| 모델 | 역할 | 권장 Budget |
|---|---|---:|
| `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` | Main LLM | 34~35GiB |
| `google/embeddinggemma-300m` | Embedding | 1.5~2GiB |
| `sigridjineth/colbert-ko-embeddinggemma-300m` | ColBERT-ko retrieval | 1.5~2GiB |
| `kakaocorp/kanana-safeguard-prompt-2.1b` | Prompt detector | 2.5~3.5GiB |
| Reserve | CUDA, fragmentation, peak | 3~5GiB |

## 3. Starting Utilization

Main LLM checkpoint는 RedHatAI의 preliminary FP8 Dynamic checkpoint이며, model config의 `compressed-tensors` quantization metadata를 사용한다. vLLM command에는 `--quantization fp8`을 넣지 않는다. upstream 예시는 B200/vLLM main에서 96K context를 사용하지만, 본 플랫폼은 RTX 6000 Ada 48GB 기준 16K context, seq 1, tokenizer canary 통과를 시작 조건으로 둔다.

| Runtime | Port | `gpu_memory_utilization` |
|---|---:|---:|
| Main LLM | 9401 | 0.72 |
| Embedding | 9402 | 0.04 |
| ColBERT-ko | 9404 | 0.06 |
| Prompt | 9403 | 0.065 |
| 합계 (3모델 구성) | - | 0.825 |
| 합계 (4모델 구성, ColBERT-ko 포함) | - | 0.885 |

## 4. 제한 조건

| 항목 | 기준 |
|---|---|
| Main LLM context | 16384부터 시작 |
| Main LLM concurrency | `max_num_seqs=1` |
| Prompt context | 2048 |
| Prompt output | 단일 토큰 label, `max_output_tokens=1` |
| 32K context | Ada 실측 전 비권장 |
| RAG bulk indexing | 초기 검증 단계에서는 제한 |

## 5. 검증 기준

| 검증 항목 | 기준 |
|---|---|
| 모델 로드 | enabled vLLM 인스턴스 모두 기동 |
| Idle VRAM | 초기 사용량과 reserve 기록 |
| Prompt detector 실행 | runtime peak가 과도하게 증가하지 않음 |
| LLM generation | 4K/8K 요청에서 OOM 없이 수행 |
| Embedding | 소규모 batch에서 응답 성공 |
| 반복 호출 | 50회 이상 반복 후 VRAM 누수 확인 |

## 6. 운영 문구 기준

피해야 할 표현:

```text
OOM이 발생하지 않는다.
운영 검증이 완료되었다.
높은 동시성에서도 안정적이다.
```

권장 표현:

```text
본 구성은 보수적 리소스 배분 기준이다.
운영 전 실제 GPU memory peak와 latency를 측정해야 한다.
```

## 7. 검증 항목

본 문서는 리소스 계획 기준이다. 실제 vLLM startup, RTX 6000 Ada 48GB 동시 상주, soak, Prometheus scrape, Grafana dashboard 확인 결과는 대상 host에서 생성한 runtime validation report에만 기록한다.

## 8. 조정 순서

리소스 조정은 다음 순서를 따른다.

1. `local-embed` 배치 또는 배치 전용 host 분리 검토
2. `risk-prompt` budget 검토
3. risk aggregate detector 추가 시 sequential/parallel peak 비교
4. `local-main` batched token 조정
5. `local-main` context 또는 sequence 동시성 조정

Detector 출력 토큰 수, 모델 fallback 금지, 독립 vLLM process/port 원칙은 조정 대상이 아니다.

## 리소스 할당 요약

budget id: `single_a6000_conservative` / 설정된 enabled `gpu_memory_utilization` 합계: `0.885`

| 모델 | 포트 | 역할 | `gpu_memory_utilization` | 기본 concurrency |
|---|---:|---|---:|---:|
| `local-main` | 9401 | chat completion | 0.72 | 1 |
| `local-embed` | 9402 | embedding | 0.04 | 2 |
| `local-colbert-ko` | 9404 | ColBERT retrieval | 0.06 | 1 |
| `risk-prompt` | 9403 | prompt risk signal | 0.065 | 1 |

이전 3모델 구성 (ColBERT-ko 제외) 합계: `0.825`

Tuning order: concurrency 축소 → max tokens/batch token 조정 순서를 따른다.

Fixed constraints: risk detector max_output_tokens은 1로 고정, model fallback은 허용하지 않는다, 각 모델은 독립 vLLM process와 port를 유지한다.
