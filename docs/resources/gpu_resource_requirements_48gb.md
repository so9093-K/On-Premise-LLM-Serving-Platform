# 인프라 리소스 요구사항 분석서

## 48GB GPU 단일 환경에서의 enabled vLLM 모델 서빙 리소스 검토

| 문서 항목 | 내용 |
|---|---|
| 산출물명 | 인프라 리소스 요구사항 분석서 |
| 프로젝트 구분 | AI 모델 서빙 인프라 검토 |
| 검토 대상 | 48GB VRAM 단일 GPU 환경 |
| 검토 범위 | Main LLM, Embedding, Prompt Risk 모델의 vLLM 기반 동시 상주 및 운영 제약 |
| 작성 목적 | enabled runtime 기준 VRAM budget, 설정 조건, 운영 가능 범위, 리스크를 정리한다. |
| 기준일 | 2026-05-12 |

## 1. 문서 목적

본 문서는 48GB VRAM 단일 GPU 환경에서 현재 기본 enabled vLLM runtime 4개를 동시에 상주시킬 때 필요한 GPU 리소스 요구사항을 정의한다.

이전 기준의 `QuantTrio/gemma-4-31B-it-AWQ`, 4개 runtime, `risk-siren` 상주, 총 utilization `0.83~0.87` 운영 reference는 폐기한다. `risk-siren`은 retired 상태이며 기본 compose, readiness, `/v1/models`, aggregate execution, runtime validation에서 제외된다.

## 2. 검토 대상 모델 구성

| 구분 | 모델 | 주요 역할 | 실행 방식 |
|---|---|---|---|
| Main LLM | `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` | 채팅, vision 입력, tool calling 응답 생성 | vLLM generation |
| Embedding | `google/embeddinggemma-300m` | RAG embedding 및 검색 벡터화 | vLLM pooling / embedding |
| ColBERT-ko | `sigridjineth/colbert-ko-embeddinggemma-300m` | ColBERT late-interaction retrieval 및 재순위 정렬 | vLLM pooling / score |
| Prompt Risk | `kakaocorp/kanana-safeguard-prompt-2.1b` | 프롬프트 공격 탐지 | vLLM generation / signal classifier |

## 3. 분석 전제

| 전제 항목 | 내용 |
|---|---|
| GPU 구성 | 48GB VRAM 단일 GPU |
| 모델 상주 방식 | enabled 모델만 vLLM 기반으로 GPU에 상주시킨다. |
| 인스턴스 구성 | Main LLM, Embedding, Prompt Risk를 각각 독립된 vLLM 인스턴스로 운용한다. |
| Main LLM 시작 context | `max_model_len=16384`, `max_num_seqs=1`, `max_num_batched_tokens=16384` |
| Risk Adapter 구성 | enabled detector registry 기준 prompt-only aggregate |
| 운영 기준 | 모델 weight, KV cache, CUDA context, executor overhead, allocator fragmentation, runtime peak reserve를 포함한다. |

## 4. 모델 사실과 리소스 해석

| 구분 | 사실 | 운영 해석 |
|---|---|---|
| Main LLM | `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic`은 `google/gemma-4-26B-A4B-it` 기반 compressed-tensors FP8 Dynamic checkpoint다. repo에는 `tokenizer.json`이 포함되어 있다. | upstream은 preliminary이며 B200/vLLM main + 96K context 예시를 제공한다. RTX 6000 Ada에서는 16K context, seq 1부터 tokenizer canary, boot, latency, quality를 검증한다. |
| Gemma 4 26B-A4B | 전체 26B급 모델이나 active parameter는 더 작다. | 계산량은 줄어도 상주 weight와 KV cache 관점에서는 26B급 모델로 취급한다. |
| Embedding | 300M급 경량 모델이다. | 모델은 작지만 별도 vLLM process의 CUDA context와 executor overhead를 포함한다. |
| Prompt Risk | 출력은 `<SAFE>`, `<UNSAFE-A1>` 같은 단일 토큰 signal이다. | `max_num_seqs=1`, `max_output_tokens=1`, `--enforce-eager` 기본값으로 운영한다. |

## 5. 권장 GPU budget

| runtime | service | `gpu_memory_utilization` 시작값 | 비고 |
|---|---|---:|---|
| Main LLM | `main-llm-vllm` | `0.72` | 16K context, seq 1 기준; 0.99 GiB KV cache 부족으로 0.66→0.72 상향 (RTX 6000 Ada) |
| Embedding | `embedding-vllm` | `0.04` | pooling runtime |
| ColBERT-ko | `colbert-ko-vllm` | `0.04` | pooling / score runtime; 포트 9404 |
| Prompt Risk | `risk-prompt-vllm` | `0.065` | 단일 토큰 signal classifier |
| 합계 (3모델 구성, ColBERT-ko 제외) | enabled vLLM total | `0.825` | 48GB 기준 약 39.6GiB 예약 |
| 합계 (4모델 구성) | enabled vLLM total | `0.865` | 48GB 기준 약 41.5GiB 예약 |
| reserve | system/runtime headroom | 5GiB 이상 권장 | 다운로드, warmup, allocator fragmentation, monitoring overhead 포함 |

`runtime peak`는 각 요청 순간의 activation/workspace까지 포함한 최대 사용량이다. 단순 reserved budget이 낮아도 긴 prompt, vision input, warmup, CUDA allocator fragmentation이 겹치면 OOM이 날 수 있으므로, target GPU에서 boot smoke와 30분 soak를 별도로 통과해야 한다.

## 6. 권장 vLLM 시작값

```bash
vllm serve RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic \
  --served-model-name local-main \
  --host 0.0.0.0 \
  --port 9401 \
  --max-model-len 16384 \
  --max-num-seqs 1 \
  --max-num-batched-tokens 16384 \
  --gpu-memory-utilization 0.72 \
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

이 checkpoint는 model config의 `compressed-tensors` metadata로 FP8 quantization을 선언하므로 `--quantization fp8`을 추가하지 않는다. 추가하면 vLLM이 model config의 `compressed-tensors`와 CLI `fp8`을 서로 다른 quantization method로 보고 기동을 거부할 수 있다. `--kv-cache-dtype fp8`은 target GPU에서 boot, quality, latency, long-context soak를 확인한 뒤 고정한다.

## 7. 운영 검증 체크리스트

| 단계 | 확인 |
|---|---|
| Config | `make validate`, `pytest`, generated schema/contract/runtime matrix drift 없음 |
| Compose | 기본 compose에 `risk-siren-vllm` service dependency가 없고, enabled runtime 4개만 scrape 대상인지 확인 |
| Boot | main 단독, main+embedding, main+embedding+colbert-ko, main+embedding+colbert-ko+risk-prompt 순서로 기동 |
| Functional smoke | `/health`, `/ready`, `/v1/models`, chat, streaming chat, image input, embeddings, retrieval rerank/score, prompt risk, aggregate |
| Risk retired policy | `/v1/risk/detectors/siren/assessments`는 410 Gone 또는 제거 정책과 일치 |
| Soak | 16K context, seq 1, mixed text/vision/risk workload 30분, restart/OOM 0 |
| Monitoring | Prometheus scrape 정상, Grafana No Data 패널 없음, GPU headroom 8GiB 이상 |

## 8. 결론

48GB 단일 GPU 기본 목표는 `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic`, `google/embeddinggemma-300m`, `sigridjineth/colbert-ko-embeddinggemma-300m`, `kakaocorp/kanana-safeguard-prompt-2.1b`의 4개 enabled runtime 구성이다. ColBERT-ko(`local-colbert-ko`, 포트 9404)는 late-interaction retrieval rerank/score 엔드포인트를 담당한다.

이 구성은 registry-driven runtime, detector registry, prompt-only aggregate, retired `risk-siren` policy를 전제로 한다. 운영 확정 전에는 RTX 6000 Ada 환경에서 boot log, idle VRAM, p95 TTFT, decode tok/s, restart/OOM 0을 기록해야 한다.
