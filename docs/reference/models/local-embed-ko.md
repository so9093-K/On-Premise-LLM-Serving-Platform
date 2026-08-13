# local-embed-ko: 한국어 임베딩 모델

`local-embed-ko`는 [dragonkue/snowflake-arctic-embed-l-v2.0-ko](https://huggingface.co/dragonkue/snowflake-arctic-embed-l-v2.0-ko)를 사용하는 한국어 Retrieval 임베딩 논리 모델이다.

| 항목 | 내용 |
|---|---|
| 라이선스 | Apache-2.0 |
| 기반 모델 | Snowflake/snowflake-arctic-embed-l-v2.0 |
| 모델 형식 | SentenceTransformer |
| 지원 언어 | Korean, English |
| 출력 차원 | 1,024 |
| 유사도 | cosine |
| upstream 최대 sequence length | 8,192 |

upstream 모델 카드는 query 입력에 `query` prompt를 사용하는 방식을 제시한다. 이 플랫폼의 Retrieval 경로에서 적용하는 prefix와 문서 입력 정책은 [`configs/model_serving.yaml`](../../../configs/model_serving.yaml)을 따른다. 모델 카드의 8,192 token 한도는 upstream 사양이며, 플랫폼 Runtime 한도는 별도로 설정한다.

upstream에 한국어 retrieval benchmark 개선 결과가 기록돼 있지만, 프로젝트 데이터셋에서의 성능 동등성이나 품질을 보장하는 근거는 아니다. 실제 corpus로 검증한다.

## 참고 자료

- [Snowflake Arctic 한국어 임베딩 모델 카드](https://huggingface.co/dragonkue/snowflake-arctic-embed-l-v2.0-ko)

