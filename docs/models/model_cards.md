# 모델 카드 요약

이 프로젝트의 public model id는 Gateway API에서 그대로 노출되므로 영어 원문을 유지한다.

모델 목록은 `model_cards/*.json`을 source-of-truth로 한다. 아래 표는 현재 active 카드를 요약한다.

| model id | 역할 | Gateway capability |
|---|---|---|
| `local-main` | 주 LLM | `chat.completions` |
| `local-embed` | 범용 임베딩 | `embeddings` |
| `local-embed-ko` | 한국어 retrieval 임베딩 (retrieval 기본 모델) | `embeddings`, `retrieval` |
| `risk-prompt` | prompt attack signal | `risk.prompt_attack_signal` |

`risk.aggregate_signal`은 개별 모델 capability가 아니라 Risk Adapter aggregate route의 동작으로 문서화한다.

상세 값은 `model_cards/*.json`을 기준으로 한다. 운영 문서에서는 모델 카드의 사실과 `configs/model_serving.yaml`의 프로젝트 운영 제한을 구분한다.

## 운영 원칙

- 모델 서버는 vLLM-compatible OpenAI API로 호출한다.
- Gateway는 model id와 response schema를 검증한다.
- Risk Adapter는 policy decision을 하지 않고 signal만 반환한다.
- 모델별 timeout, concurrency, queue timeout은 config와 env로 조정한다.
