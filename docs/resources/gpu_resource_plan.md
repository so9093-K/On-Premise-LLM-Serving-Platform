# GPU Resource Plan

## 1. 범위

본 문서는 RTX 6000 Ada 48GB 단일 GPU에서 enabled vLLM 모델 4개를 동시에 상주시킬 때의 보수적 리소스 배분 기준을 정의한다. `risk-siren`은 retired 상태이며 기본 runtime budget 합계에서 제외한다.

> **2026-07-16 갱신**: 아래 2~8절은 Main LLM을 26B 하나로 고정된 모델로 전제하고 작성됐다.
> [ADR-0017](../adr/0017-selectable-main-model-runtime.md) 이후 Main LLM은 26B/12B 중
> 선택 가능한 프로필이며, 각 프로필의 실제 VRAM 사용량은 서로 다르다. 최신 실측치는 **9절**
> 참고.
>
> **2026-07-28 갱신**: `default_profile`이 `gemma4-12b-unified-fp8`로 바뀌었다([ADR-0017](../adr/0017-selectable-main-model-runtime.md)
> Update 참고). 아래 2~8절의 "Main LLM = 20K, seq=1"은 이제 **기본이 아니라 admin API로
> 전환 가능한 대안 프로필(26B)**을 설명한다 -- 현재 기본으로 서빙되는 12B 수치는 9절을 본다.

## 2. 모델별 Budget

| 모델 | 역할 | 권장 Budget |
|---|---|---:|
| `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` | Main LLM | 34~35GiB |
| `google/embeddinggemma-300m` | Embedding | 1.5~2GiB |
| `kakaocorp/kanana-safeguard-prompt-2.1b` | Prompt detector | 2.5~3.5GiB |
| Reserve | CUDA, fragmentation, peak | 3~5GiB |

## 3. Starting Utilization

Main LLM checkpoint는 RedHatAI의 preliminary FP8 Dynamic checkpoint이며, model config의 `compressed-tensors` quantization metadata를 사용한다. vLLM command에는 `--quantization fp8`을 넣지 않는다. upstream 예시는 B200/vLLM main에서 96K context를 사용하지만, 본 플랫폼은 RTX 6000 Ada 48GB 기준 20K context, seq 1, optimization level 3 runtime target을 검증 조건으로 둔다.

Prefix cache는 동일하거나 긴 공통 prefix가 반복되는 요청에서 prefill 재사용을 기대하는 기능이며, 운영 검증에서는 hit/reuse 관련 지표를 별도로 확인한다.

| Runtime | Port | `gpu_memory_utilization` |
|---|---:|---:|
| Main LLM | 9401 | 0.76 |
| Embedding | 9402 | 0.04 |
| Dense retrieval-ko | 9406 | 0.06 |
| Prompt | 9403 | 0.065 |
| 합계 (3모델 구성) | - | 0.825 |
| 합계 (4모델 구성, Dense retrieval-ko 포함) | - | 0.925 |

## 4. 제한 조건

| 항목 | 기준 |
|---|---|
| Main LLM context | 20000 runtime target |
| Main LLM concurrency | `max_num_seqs=1` |
| Prompt context | 2048 |
| Prompt output | 단일 토큰 label, `max_output_tokens=1` |
| 20K context | boot/latency/quality/soak 검증 전 production 확정 아님 |
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

설정된 enabled `gpu_memory_utilization` 합계: `0.925`

| 모델 | 포트 | 역할 | `gpu_memory_utilization` | 기본 concurrency |
|---|---:|---|---:|---:|
| `local-main` | 9401 | chat completion | 0.76 | 1 |
| `local-embed` | 9402 | embedding | 0.04 | 2 |
| `local-embed-ko` | 9406 | Dense retrieval retrieval | 0.06 | 1 |
| `risk-prompt` | 9403 | prompt risk signal | 0.065 | 1 |

이전 3모델 구성 (Dense retrieval-ko 제외) 합계: `0.825`

Tuning order: concurrency 축소 → max tokens/batch token 조정 순서를 따른다.

Fixed constraints: risk detector max_output_tokens은 1로 고정, model fallback은 허용하지 않는다, 각 모델은 독립 vLLM process와 port를 유지한다.

## 9. Selectable Main LLM 프로필 반영 (2026-07-16 갱신)

[ADR-0017](../adr/0017-selectable-main-model-runtime.md) 이후 `local-main`은 `configs/main_model_profiles.yaml`의 `gemma4-12b-unified-fp8`(2026-07-28부터 기본)/`gemma4-26b-a4b-fp8`(admin API로 전환 가능한 대안) 중 하나로 전환된다. 위 2~8절의 "Main LLM = 20K, seq=1" 서술은 활성 프로필이 26B일 때만 유효하다.

실제 배포 서버에서 두 프로필을 각각 활성화해 실측한 결과([ADR-0015](../adr/0015-main-llm-20k-o3-runtime-target.md) Update 참고):

| 프로필 | context/concurrency | 실제 VRAM (local-main) | KV cache pool |
|---|---|---:|---|
| `gemma4-26b-a4b-fp8` | 20K, seq=1 | 35.1 GiB | 172,992 tokens (필요량 대비 약 8.6배 여유) |
| `gemma4-12b-unified-fp8` | 50K, seq=2 | 30.2 GiB | 266,208 tokens (필요량 대비 약 2.7배 여유) |

12B는 context가 더 크고 audio/video까지 지원하지만, weight 자체가 26B보다 작아 실제 VRAM은 오히려 더 적게 쓴다. `gpu_memory_utilization`(0.76)이 아니라 `--max-num-batched-tokens`가 부팅 시 KV cache pool 크기를 좌우하는 실제 요인이라는 점도 확인됐다 — 자세한 메커니즘은 ADR-0015 Update 참고.

> 위 12B 행은 `seq=2` 시점 실측이다. 이후 `--max-num-seqs`가 2 -> 3으로 조정됐고(현재
> 실제 배포 커맨드), 3에서도 여유가 확인됐다. 자세한 실측과 변경 판단은
> `configs/main_model_profiles.yaml` 하단 `description`을 참고한다.
> `num_gpu_blocks x block_size`로 계산한 토큰 수 자체도 이 model(Gemma4 heterogeneous head
> dim, TRITON_ATTN)에서는 실제 KV cache 용량과 안 맞는 것으로 확인됐다 -- 정확한 값은
> `kv_cache_size_tokens` 또는 부팅 로그의 "Available KV cache memory" GiB를 봐야 한다.

**조정 순서(8절) 관련 실무 노트**: "이론상 여유가 있어 보인다"와 "실제로 확인됐다"는 다르다. 어느 프로필이 실제로 얼마나 쓰는지는 `nvidia-smi`와 vLLM `/metrics`(`vllm:cache_config_info`)로 부팅 후 직접 확인하는 게 원칙이며, 이 문서의 고정 budget 표(2절)를 근거로 삼지 않는다.
