# local-embed: 일반 임베딩 모델

`local-embed`은 [google/embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m)을 사용하는 일반 텍스트 임베딩 논리 모델이다.

| 항목 | 내용 |
|---|---|
| 라이선스 | Gemma |
| 규모 | 300M parameters |
| 입력 | 텍스트 |
| 공식 최대 입력 | 2,048 tokens |
| 기본 embedding 차원 | 768 |
| 모델 카드상 Matryoshka 차원 | 768, 512, 256, 128 |
| 현재 Gateway·vLLM 공개 차원 | 768 |
| upstream 권장 Runtime | Sentence Transformers |

모델 파일과 콘텐츠를 사용하려면 upstream의 Gemma 사용 약관 동의가 필요하다. 모델 카드에는
Matryoshka 차원이 안내되어 있지만, 현재 vLLM pooling runtime은 `dimensions` 축소 요청을
지원하지 않는다. 따라서 플랫폼의 차원 허용 범위, Retrieval prefix 정책, request parameter
지원 범위는 이 문서가 아니라 [`configs/model_serving.yaml`](../../../configs/model_serving.yaml)을 따른다.

## 참고 자료

- [EmbeddingGemma 모델 카드](https://huggingface.co/google/embeddinggemma-300m)
- [vLLM OpenAI 호환 서버](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)
