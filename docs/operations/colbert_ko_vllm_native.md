# ColBERT-ko vLLM runtime 운영 노트

`local-colbert-ko`는 retrieval 전용 active model이다. `/v1/embeddings`를 대체하지 않고 `/v1/retrieval/rerank`, `/v1/retrieval/score`, `/v1/retrieval/token-embeddings`에서만 사용한다.

이 문서의 `vLLM native` 표현은 운영상 `colbert-ko-vllm` 서비스가 vLLM plugin runtime으로 `/score`와 `/pooling`을 서빙한다는 뜻이다. ColBERT-ko encoder 자체를 vLLM kernel/native model implementation으로 재구현했다는 뜻이 아니다. 실제 모델 의미는 `text -> tokenizer -> 2D input_ids + attention_mask -> encoder -> proj.pt -> L2 normalize -> token-level embeddings -> MaxSim scoring`이다.

## Runtime contract

| 항목 | 값 |
|---|---|
| logical id | `local-colbert-ko` |
| serving key | `colbert_ko` |
| compose service | `colbert-ko-vllm` |
| port | `9404` |
| backend | `vllm_native` |
| runner | `pooling` |
| score mode | `late_interaction_maxsim` |
| capabilities | `retrieval_rerank`, `retrieval_score`, `retrieval_token_embeddings` |
| dtype | `float32` fixed in the v0.0.1 production compose profile; `bfloat16` allowed by policy after parity validation; `float16` forbidden |
| env var | `COLBERT_KO_GPU_MEMORY_UTILIZATION` (default: `0.06`) |

## dtype 정책

EmbeddingGemma backbone은 float16 activation을 사용하면 안 된다.

| dtype | 정책 |
|---|---|
| `float32` | 기본값. 레퍼런스 구현 parity, 재현성, 디버깅 용이성 |
| `bfloat16` | 허용. 처리량·메모리 제약 환경에서 ranking parity 검증 후 사용 가능 |
| `float16` | **금지.** EmbeddingGemma backbone 비호환 |

v0.0.1 production compose는 `--dtype float32`로 고정한다. bfloat16 전환은 별도 runtime profile에서 `colbert-parity-smoke` ranking parity를 통과한 뒤 활성화한다. dtype을 바꾸면 score 소수점 끝 자리가 달라질 수 있다.

## Core semantics and vLLM adapter boundary

ColBERT-ko core는 tokenizer가 만든 2D `input_ids`와 `attention_mask`를 입력으로 받는다. `attention_mask` 없이 2D `input_ids`만 들어오는 경로는 원본 HF/ColBERT inference 의미와 맞지 않으므로 runtime error로 취급한다.

vLLM pooling executor는 내부 스케줄링 표현으로 여러 시퀀스를 하나의 연속된 1D tensor로 넘길 수 있다. 이 경우 `model.py`의 vLLM flattened adapter path가 `positions` tensor의 0 재시작 지점을 시퀀스 시작점으로 삼아 2D `input_ids`와 `attention_mask`를 복원한 뒤 core를 호출한다. 이 1D 처리는 vLLM executor compatibility adapter이며 ColBERT-ko의 핵심 기능이나 일반 inference 의미가 아니다.

이 경계 복원이 없으면 여러 시퀀스가 encoder self-attention을 공유해 batch size와 vLLM 스케줄러 packing 상태에 따라 token embedding과 MaxSim score가 달라지는 재현성 문제가 발생한다. `max_num_seqs=1`은 이 문제의 임시 안전장치이지, 경계 복원의 대체재가 아니다.

forward shape trace가 필요할 때만 `COLBERT_KO_TRACE_FORWARD_SHAPES=1`을 `colbert-ko-vllm` runtime 환경에 설정한다. 로그에는 tensor shape, `positions`/`attention_mask` 존재 여부, 선택된 path(`vllm_flattened_adapter` 또는 `direct_2d_core`), 1D adapter 복원 lengths만 남기며 입력 텍스트나 token 값은 남기지 않는다. 기본값에서는 이 로그가 비활성화되어 운영 로그 노이즈를 만들지 않는다.

Production compose는 HF repo root나 raw Hugging Face cache를 `--model`로 넘기지 않는다. The ColBERT-ko source repository is not mounted directly as the vLLM model directory. Run `prepare_colbert_ko_vllm_artifact.py` first. `COLBERT_KO_MODEL_DIR` must point to the prepared artifact directory whose root contains `config.json`.

Compose는 `--model /models/colbert-ko-vllm`, `--tokenizer /models/colbert-ko-vllm/tokenizer`, `--runner pooling`, `--convert embed`, `--trust-remote-code`, `--pooler-config.task token_embed`를 사용한다. `--model-impl transformers`는 사용하지 않는다. ColBERT-ko는 Transformers `auto_map` model이 아니라 dedicated image의 `colbert_ko_vllm` plugin이 vLLM `ModelRegistry`에 등록하는 custom pooling model이기 때문이다. 따라서 host의 `$COLBERT_KO_MODEL_DIR/config.json`이 container 안의 `/models/colbert-ko-vllm/config.json`으로 보여야 한다.

GitLab full deploy에서 `PREPARE_COLBERT_KO_ARTIFACT=1`을 설정하면 target host의 Python 환경을 사용하지 않고 platform image container 안에서 prepare script를 실행한다. Platform image는 `huggingface_hub`만 포함한 lightweight ops helper 역할을 겸하며, torch/vLLM/transformers 같은 model conversion dependency를 추가하지 않는다.

## Artifact packaging

원본 repository layout은 `encoder/`, `tokenizer/`, `proj.pt`, `inference.py`로 분리되어 있다. vLLM-hosted ColBERT-ko wrapper artifact는 이 구조를 재현 가능한 local directory로 준비하고, root `config.json`에 custom architecture와 projection metadata를 둔다.

```bash
/usr/bin/python3.12 scripts/models/prepare_colbert_ko_vllm_artifact.py \
  --output-dir ./models/colbert-ko-vllm
```

준비된 artifact는 다음 조건을 만족해야 한다.

| 파일 | 목적 |
|---|---|
| `config.json` | vLLM custom architecture entrypoint |
| `encoder/config.json`, `encoder/model.safetensors` | EmbeddingGemma encoder |
| `tokenizer/tokenizer.json`, `tokenizer/tokenizer_config.json` | tokenizer |
| `proj.pt` | hidden state to 128-d ColBERT projection |
| `artifact_manifest.json` | source repo, revision, projection policy 기록 |

`proj.pt`가 없거나 128-d projection shape와 맞지 않으면 native runtime은 production-ready가 아니다.

## 사용자 조정 가능 request parameter

`/v1/retrieval/rerank`와 `/v1/retrieval/score`에서 `local-colbert-ko`를 사용할 때 아래 파라미터를 조정할 수 있다. `/v1/models`의 `request_parameters`가 source of truth다.

| 파라미터 | 타입 | 기본값 | 범위 | 설명 |
|---|---|---|---|---|
| `score_mode` | string | `late_interaction_maxsim` | `late_interaction_maxsim` | ColBERT MaxSim. 변경 불가(enum 고정) |
| `top_n` | integer | — | 1–32 | rerank 결과 상위 n개만 반환. **score endpoint에서는 422** |
| `max_tokens_per_query` | integer | 128 | 1–128 | query 최대 토큰 수 |
| `max_tokens_per_doc` | integer | 192 | 32–1024 | document당 최대 토큰 수 |
| `truncate_prompt_tokens` | integer | — | -1 또는 1–2048 | -1은 모델 최대 길이 사용 |
| `truncation_side` | string | `right` | `left`, `right` | 잘릴 때 어느 쪽을 자를지 |

`/v1/retrieval/token-embeddings`는 `truncate_prompt_tokens`와 deprecated alias `truncate_to_tokens`(최대 192)를 지원한다. 두 값이 모두 있고 다르면 422를 반환한다. Gateway는 이 endpoint를 ColBERT vLLM runtime의 root `/pooling` endpoint로 전달한다. `/v1/pooling`은 vLLM에서 노출하지 않으므로 사용하지 않는다.

runtime 고정값(`fixed_parameters`): `dtype=float32`, `pooler_task=token_embed`, `score_function=maxsim`, `backend=vllm_native_late_interaction`, `max_num_seqs=1`.

## Reference adapter

Reference adapter는 production substitute가 아니다. 역할은 `encoder -> proj.pt -> L2 normalize -> MaxSim` 기준선을 제공해서 vLLM plugin runtime 결과의 ranking sanity를 검증하는 것이다. 최소 fixture에서는 reference top-1과 vLLM-served top-1이 일치해야 하며, token embedding shape와 special token masking 정책 차이를 함께 확인한다.

일반 CI는 Docker/GPU 없이 `maxsim_score` fixture와 prepared artifact/config를 검증한다. GPU host에서는 prepared artifact와 `colbert-ko-vllm`을 띄운 뒤 live parity smoke를 별도로 실행한다.

```bash
make colbert-parity-smoke
```

이 smoke는 reference adapter 점수, vLLM `/score` 점수, top-1 ranking, `/pooling` token embedding shape, `proj.pt` 128-d 적용 여부를 확인한다. 기본 vLLM base URL은 `COLBERT_KO_VLLM_BASE_URL` 또는 `http://localhost:9404`다.

실제 vLLM forward path 증거를 같이 남기려면 runtime을 `COLBERT_KO_TRACE_FORWARD_SHAPES=1`로 띄우고 로그를 파일로 캡처한 뒤 다음처럼 실행한다.

```bash
python scripts/validation/colbert_parity_smoke.py \
  --require-full-order \
  --forward-shape-log /path/to/colbert-ko-vllm.log \
  --require-forward-trace
```

결과 JSON의 `observed_forward_paths`에는 `vllm_flattened_adapter` 또는 `direct_2d_core`가 기록된다.

## Monitoring

Active runtime labels는 `local-main`, `local-embed`, `local-colbert-ko`, `risk-prompt` 네 개다. vLLM runtime labels는 `main-llm-vllm`, `embedding-vllm`, `colbert-ko-vllm`, `risk-prompt-vllm` 네 개다. API Experience dashboard는 retrieval request rate, p50/p95 latency, error rate, items/request, token embedding response bytes, `local-colbert-ko` upstream latency를 `route`, `model`, `backend`, `score_mode` label 기준으로 보여준다.

GPU budget은 4-runtime 기준 `0.885`(main_llm 0.72 + embedding 0.04 + colbert_ko 0.06 + risk_prompt 0.065)이고 `avoid_above`는 `0.93`이다. 8GiB reserve는 conservative target이며 hard minimum은 아니다.
