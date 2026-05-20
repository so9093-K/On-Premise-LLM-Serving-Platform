# ADR-0010: ColBERT 제거와 Dense Korean Retrieval 전환

## Status

Accepted

## Context

프로젝트 초기에는 `local-colbert-ko`(ColBERT-based late-interaction reranking)를 한국어 retrieval 모델로 사용했다. 이 모델은 `embedding-ko-vllm`과 구별되는 별도 vLLM runtime과 custom ColBERT plugin(`colbert_ko_vllm_native`)에 의존했다.

운영 과정에서 다음 문제가 식별됐다.

- late-interaction MaxSim 계산을 위한 `/v1/retrieval/token-embeddings` endpoint가 응답 크기를 크게 키워 네트워크 부담이 컸다.
- ColBERT plugin과 커스텀 Dockerfile(`Dockerfile.colbert-ko-vllm`)이 별도 빌드·패치 lifecycle을 요구했다.
- `dragonkue/snowflake-arctic-embed-l-v2.0-ko`가 Korean retrieval benchmark에서 충분한 dense embedding 품질을 제공하는 것으로 평가됐다.

## Decision

`local-colbert-ko`를 제거하고 `local-embed-ko`(`dragonkue/snowflake-arctic-embed-l-v2.0-ko`, dense cosine 기반)를 retrieval 기본 모델로 채택한다.

구체적으로:

- `local-colbert-ko` model card, vLLM runtime, compose service 제거
- `late_interaction_maxsim` score mode 제거
- `/v1/retrieval/token-embeddings` endpoint 제거
- `local-embed-ko`를 `/v1/retrieval/*` 기본 모델로 설정
- retrieval은 `dense_cosine` score mode를 표준으로 사용
- `local-embed`(EmbeddingGemma 300m)는 범용 임베딩 모델로 유지

## Consequences

| Positive | Negative |
|---|---|
| 응답 크기 감소 (token embedding 불필요) | ColBERT late-interaction ranking 이점 포기 |
| 운영 복잡도 감소 (custom plugin 제거) | Siren-class precision reranking은 별도 detector 필요 시 재검토 |
| 별도 Dockerfile build lifecycle 불필요 | — |
| embedding-ko-vllm이 표준 vLLM image 사용으로 단순화 | — |
| Prometheus/Grafana monitoring 일관성 향상 | — |

## Operational impact

- `embedding-ko-vllm` runtime은 `EMBEDDING_KO_VLLM_IMAGE` 환경 변수로 지정한 표준 vLLM image를 사용한다. 별도 derived Dockerfile 빌드가 없다.
- `ops/compose/full-stack.private-network.yaml`에서 `embedding-ko-vllm` service가 `embedding-vllm` 다음 순서로 기동한다(serial GPU vLLM startup 정책).
- Grafana `$model` variable에 `local-embed-ko`를 포함한다.

## Migration notes

- `Dockerfile.colbert-ko-vllm`, `scripts/build/build_colbert_ko_vllm_image.sh`, `scripts/models/prepare_colbert_ko_vllm_artifact.py`, `scripts/validation/colbert_parity_smoke.py`를 프로젝트에서 제거했다.
- `model_cards/local-colbert-ko.json`을 제거했다.
- `/v1/retrieval/token-embeddings` endpoint를 제거했다.
- 기존 클라이언트가 `/v1/retrieval/token-embeddings`를 사용했다면 `/v1/retrieval/score` 또는 `/v1/retrieval/rerank`로 전환해야 한다.

## Related

- ADR-0003: All Major Models as vLLM Runtime
- ADR-0011: 문서 Source-of-Truth와 Generated Block 정책
- `configs/model_serving.yaml` `embedding_ko` 항목
- `model_cards/local-embed-ko.json`
