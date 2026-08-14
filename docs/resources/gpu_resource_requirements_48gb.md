# 48GB GPU 리소스 검증 이력

## 48GB GPU 단일 환경에서의 enabled vLLM 모델 서빙 리소스 검토

| 문서 항목 | 내용 |
|---|---|
| 문서 성격 | 과거 26B 단일 runtime 및 이후 selectable profile 검증 이력 |
| 프로젝트 구분 | AI 모델 서빙 인프라 검토 |
| 검토 대상 | 48GB VRAM 단일 GPU 환경 |
| 검토 범위 | Main LLM, Embedding, Prompt Risk 모델의 vLLM 기반 동시 상주 및 운영 제약 |
| 작성 목적 | GPU budget 판단에 사용한 가정·실측·실패 사례를 보존한다. 현재 운영값을 선언하지 않는다. |
| 기준일 | 2026-05-12 |

> **읽는 방법**: 1~8절은 26B 단일 모델을 전제로 한 당시 검증 기록이다. 현재 기본 profile,
> GPU budget, request limit의 source-of-truth는 각각 `configs/main_model_profiles.yaml`,
> `configs/gpu_budgets.yaml`, `configs/model_serving.yaml`이다. 12B를 포함한 프로필별 실측은
> 9절에 보존돼 있으며, 배포 중인 실제 값은 boot log·`nvidia-smi`·vLLM `/metrics`로 확인한다.

## 1. 문서 목적

본 문서는 48GB VRAM 단일 GPU 환경에서 26B 단일 Main Model을 기준으로 했던 resource 판단과, 이후 selectable profile 전환 뒤의 실측을 기록한다. 현재 운영 요구사항을 정의하지 않는다.

이전 기준의 `QuantTrio/gemma-4-31B-it-AWQ`, 4개 runtime, `risk-siren` 상주, 총 utilization `0.83~0.87` 운영 reference는 폐기한다. `risk-siren`은 retired 상태이며 기본 compose, readiness, `/v1/models`, aggregate execution, runtime validation에서 제외된다.

## 2. 검토 대상 모델 구성

| 구분 | 모델 | 주요 역할 | 실행 방식 |
|---|---|---|---|
| Main LLM | `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` | 채팅, vision 입력, tool calling 응답 생성 | vLLM generation |
| Embedding | `google/embeddinggemma-300m` | RAG embedding 및 검색 벡터화 | vLLM pooling / embedding |
| Prompt Risk | `kakaocorp/kanana-safeguard-prompt-2.1b` | 프롬프트 공격 탐지 | vLLM generation / signal classifier |

## 3. 분석 전제

| 전제 항목 | 내용 |
|---|---|
| GPU 구성 | 48GB VRAM 단일 GPU |
| 모델 상주 방식 | enabled 모델만 vLLM 기반으로 GPU에 상주시킨다. |
| 인스턴스 구성 | Main LLM, Embedding, Prompt Risk를 각각 독립된 vLLM 인스턴스로 운용한다. |
| Main LLM canary context | `max_model_len=20000`, `max_num_seqs=1`, `max_num_batched_tokens=20000`, `optimization_level=3`, `gpu_memory_utilization=0.76` |
| Risk Adapter 구성 | enabled detector registry 기준 prompt-only aggregate |
| 운영 기준 | 모델 weight, KV cache, CUDA context, executor overhead, allocator fragmentation, runtime peak reserve를 포함한다. |

## 4. 모델 사실과 리소스 해석

| 구분 | 사실 | 운영 해석 |
|---|---|---|
| Main LLM | `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic`은 `google/gemma-4-26B-A4B-it` 기반 compressed-tensors FP8 Dynamic checkpoint다. repo에는 `tokenizer.json`이 포함되어 있다. | upstream은 preliminary이며 B200/vLLM main + 96K context 예시를 제공한다. RTX 6000 Ada에서는 20K context, seq 1, optimization level 3 runtime target으로 tokenizer, boot, latency, quality를 검증한다. |
| Gemma 4 26B-A4B | 전체 26B급 모델이나 active parameter는 더 작다. | 계산량은 줄어도 상주 weight와 KV cache 관점에서는 26B급 모델로 취급한다. |
| Embedding | 300M급 경량 모델이다. | 모델은 작지만 별도 vLLM process의 CUDA context와 executor overhead를 포함한다. |
| Prompt Risk | 출력은 `<SAFE>`, `<UNSAFE-A1>` 같은 단일 토큰 signal이다. | `max_num_seqs=1`, `max_output_tokens=1`, `--enforce-eager` 기본값으로 운영한다. |

## 5. 권장 GPU budget

| runtime | service | `gpu_memory_utilization` 시작값 | 비고 |
|---|---|---:|---|
| Main LLM | `main-llm-vllm` | `0.76` | 20K context + O3 runtime target, seq 1 기준; boot/latency/quality/soak 통과 전 production 확정 아님 |
| Embedding | `embedding-vllm` | `0.04` | pooling runtime |
| Dense retrieval-ko | `embedding-ko-vllm` | `0.06` | pooling / score runtime; 포트 9406 |
| Prompt Risk | `risk-prompt-vllm` | `0.065` | 단일 토큰 signal classifier |
| 합계 (3모델 구성, Dense retrieval-ko 제외) | enabled vLLM total | `0.825` | 48GB 기준 약 39.6GiB 예약 |
| 합계 (4모델 구성) | enabled vLLM total | `0.925` | 48GB 기준 약 44.4GiB 예약 |
| reserve | system/runtime headroom | 3.5GiB hard minimum | 다운로드, warmup, allocator fragmentation, monitoring overhead 포함 |

`runtime peak`는 각 요청 순간의 activation/workspace까지 포함한 최대 사용량이다. 단순 reserved budget이 낮아도 긴 prompt, vision input, warmup, CUDA allocator fragmentation이 겹치면 OOM이 날 수 있으므로, target GPU에서 boot smoke와 30분 soak를 별도로 통과해야 한다.

## 6. 권장 vLLM 시작값

```bash
vllm serve RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic \
  --served-model-name local-main \
  --host 0.0.0.0 \
  --port 9401 \
  --max-model-len 20000 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 20000 \
  --optimization-level 3 \
  --gpu-memory-utilization 0.76 \
  --tensor-parallel-size 1 \
  --dtype auto \
  --trust-remote-code \
  --enable-prefix-caching \
  --prefix-caching-hash-algo sha256_cbor \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  --chat-template /app/configs/gemma4_chat_template.jinja
```

이 checkpoint는 model config의 `compressed-tensors` metadata로 FP8 quantization을 선언하므로 `--quantization fp8`을 추가하지 않는다. 추가하면 vLLM이 model config의 `compressed-tensors`와 CLI `fp8`을 서로 다른 quantization method로 보고 기동을 거부할 수 있다. 당시 runtime target은 `--optimization-level 3`을 포함한 20K context 검증이었으며, target GPU에서 boot, quality, latency, long-context soak를 확인한 뒤 production claim 여부를 판단했다.

Prefix caching은 반복 prefix가 있는 multi-turn, tool, RAG prompt에서 prefill 재사용 가능성이 있을 때 주로 효과가 나며, prefix가 매번 달라지는 요청에서는 hit/reuse 지표로 실효성을 확인해야 한다.

## 7. 운영 검증 체크리스트

| 단계 | 확인 |
|---|---|
| Config | `make validate`, `pytest`, generated schema/contract/runtime matrix drift 없음 |
| Compose | 기본 compose에 `risk-siren-vllm` service dependency가 없고, enabled runtime 4개만 scrape 대상인지 확인 |
| Functional smoke | `/health`, `/ready`, `/v1/models`, chat, streaming chat, image input, embeddings, retrieval rerank/score, prompt risk, aggregate |
| Risk retired policy | `/v1/risk/detectors/siren/assessments`는 410 Gone 또는 제거 정책과 일치 |
| Soak | 20K context, seq 1, mixed text/vision/risk workload 30분, restart/OOM 0 |
| Monitoring | Prometheus scrape 정상, Grafana No Data 패널 없음, GPU reserve가 `hard_minimum` 3.5GiB 이상 |

## 8. 결론


이 구성은 registry-driven runtime, detector registry, prompt-only aggregate, retired `risk-siren` policy를 전제로 한다. 운영 확정 전에는 RTX 6000 Ada 환경에서 boot log, idle VRAM, p95 TTFT, decode tok/s, restart/OOM 0을 기록해야 한다.

## 9. Selectable Main LLM 프로필 반영 (2026-07-16 갱신)

[ADR-0017](../adr/0017-selectable-main-model-runtime.md)/[ADR-0018](../adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md) 이후 "Main LLM"은 `configs/main_model_profiles.yaml`의 profile 중 하나로 전환되는 identity다. 이 절의 표는 초기 Gemma 26B/12B 두 profile을 실제 배포 서버에서 측정한 이력일 뿐이며, 현재 선택 가능한 전체 profile 목록과 각각의 실행값·검증 근거는 catalog가 권위다. 1~8절은 26B 단일 모델을 전제로 한 원본이며, 그 전제가 더 이상 유효하지 않다.

실제 배포 서버에서 두 프로필을 각각 활성화해 실측한 결과([ADR-0015](../adr/0015-main-llm-20k-o3-runtime-target.md) Update 참고):

| 프로필 | context/concurrency | local-main 자체 VRAM | 전체 GPU 사용량 | KV cache pool (`num_gpu_blocks`) |
|---|---|---:|---:|---|
| `gemma4-26b-a4b-fp8` (대안) | 20K, seq=1 | 35.1 GiB | 41.5 GiB | 10812 blocks (172,992 tokens) |
| `gemma4-12b-unified-fp8` (기본) | 50K, seq=3 | 31.9 GiB | 38.3 GiB | kv_cache_size_tokens 53,722 |

즉 12B가 26B보다 context가 2.5배 크고 audio/video까지 지원하는데도 실제 VRAM은 오히려 더 적게 씁니다 — 26B는 weight 자체가 더 크기 때문입니다. 두 경우 다 `gpu_memory_utilization=0.76`으로 boot에 필요한 최소량 대비 여유가 있었다.

12B 행은 2026-07-24에 `nvidia-smi`/`vllm:cache_config_info`로 재측정한 50K/seq=3 수치다. 58,192 context 시도와 자동 rollback, KV cache 수치 해석의 상세 근거는 `configs/main_model_profiles.yaml` 하단 `description`에 보관한다. 앞으로는 `num_gpu_blocks x block_size`가 아니라 `vllm:cache_config_info`의 `kv_cache_size_tokens` 또는 부팅 로그의 "Available KV cache memory" GiB를 기준으로 삼는다.

**실무 시사점**: 1~8절의 "Main LLM canary context는 20K, seq 1"이라는 서술은 활성 프로필이 26B일 때만 맞다. 어느 프로필이 실제로 얼마나 VRAM을 쓰는지는 이 문서의 고정 표가 아니라, 부팅 후 `nvidia-smi`와 vLLM `/metrics`(`vllm:cache_config_info`의 `num_gpu_blocks`)로 확인하는 게 원칙이다 — 이론 계산이 실측과 크게 어긋난 전례([ADR-0015](../adr/0015-main-llm-20k-o3-runtime-target.md) Context의 32K 실패 사례)가 있다.
