# ColBERT-ko vLLM native 운영 노트

`local-colbert-ko`는 retrieval 전용 active model이다. `/v1/embeddings`를 대체하지 않고 `/v1/retrieval/rerank`, `/v1/retrieval/score`, `/v1/retrieval/token-embeddings`에서만 사용한다.

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

Production compose는 HF repo root나 raw Hugging Face cache를 `--model`로 넘기지 않는다. The ColBERT-ko source repository is not mounted directly as the vLLM model directory. Run `prepare_colbert_ko_vllm_artifact.py` first. `COLBERT_KO_MODEL_DIR` must point to the prepared artifact directory whose root contains `config.json`.

Compose는 `--model /models/colbert-ko-vllm`, `--tokenizer /models/colbert-ko-vllm/tokenizer`, `--runner pooling`, `--convert embed`, `--model-impl transformers`, `--pooler-config.task token_embed`를 사용한다. 따라서 host의 `$COLBERT_KO_MODEL_DIR/config.json`이 container 안의 `/models/colbert-ko-vllm/config.json`으로 보여야 한다.

## Artifact packaging

원본 repository layout은 `encoder/`, `tokenizer/`, `proj.pt`, `inference.py`로 분리되어 있다. vLLM native artifact는 이 구조를 재현 가능한 local directory로 준비하고, root `config.json`에 custom architecture와 projection metadata를 둔다.

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

## Reference adapter

Reference adapter는 production substitute가 아니다. 역할은 `encoder -> proj.pt -> L2 normalize -> MaxSim` 기준선을 제공해서 vLLM native 결과의 ranking sanity를 검증하는 것이다. 최소 fixture에서는 reference top-1과 vLLM native top-1이 일치해야 하며, token embedding shape와 special token masking 정책 차이를 함께 확인한다.

일반 CI는 Docker/GPU 없이 `maxsim_score` fixture와 prepared artifact/config를 검증한다. GPU host에서는 prepared artifact와 `colbert-ko-vllm`을 띄운 뒤 live parity smoke를 별도로 실행한다.

```bash
make colbert-parity-smoke
```

이 smoke는 reference adapter 점수, vLLM `/score` 점수, top-1 ranking, `/pooling` token embedding shape, `proj.pt` 128-d 적용 여부를 확인한다. 기본 vLLM base URL은 `COLBERT_KO_VLLM_BASE_URL` 또는 `http://localhost:9404`다.

## Monitoring

Active runtime labels는 `local-main`, `local-embed`, `local-colbert-ko`, `risk-prompt` 네 개다. vLLM runtime labels는 `main-llm-vllm`, `embedding-vllm`, `colbert-ko-vllm`, `risk-prompt-vllm` 네 개다. API Experience dashboard는 retrieval request rate, p50/p95 latency, error rate, items/request, token embedding response bytes, `local-colbert-ko` upstream latency를 `route`, `model`, `backend`, `score_mode` label 기준으로 보여준다.

GPU budget은 4-runtime 기준 `0.865`이고 `avoid_above`는 `0.93`이다. 8GiB reserve는 conservative target이며 hard minimum은 아니다.
