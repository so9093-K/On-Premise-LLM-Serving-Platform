# 모델 카드 요약

이 프로젝트의 public model id는 Gateway API에서 그대로 노출되므로 영어 원문을 유지한다.

모델 목록과 Gateway 노출 정책의 원천은 `configs/model_catalog.yaml`과
`configs/model_serving.yaml`이다. 아래 표는 그 운영 설정을 설명하는 현재 active
모델 카드를 요약한다.

| model id | 역할 | Gateway capability |
|---|---|---|
| `local-main` | 주 LLM | `chat.completions` |
| `local-embed` | 범용 임베딩 | `embeddings` |
| `local-embed-ko` | 한국어 retrieval 임베딩 (retrieval 기본 모델) | `embeddings`, `retrieval` |
| `risk-prompt` | prompt attack signal | `risk.prompt_attack_signal` |

`risk.aggregate_signal`은 개별 모델 capability가 아니라 Risk Adapter aggregate route의 동작으로 문서화한다.

모델 카드는 upstream 사실과 운영 배경을 위한 설명 문서다. 실제 제한·포트·노출 정책은
항상 `configs/model_catalog.yaml`과 `configs/model_serving.yaml`을 기준으로 하며, 카드의
서술 변경은 런타임 검증의 입력이 아니다.

## 운영 원칙

- 모델 서버는 vLLM-compatible OpenAI API로 호출한다.
- Gateway는 model id와 response schema를 검증한다.
- Risk Adapter는 policy decision을 하지 않고 signal만 반환한다.
- 모델별 timeout, concurrency, queue timeout은 config와 env로 조정한다.
