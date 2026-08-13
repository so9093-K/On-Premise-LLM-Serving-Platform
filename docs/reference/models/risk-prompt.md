# risk-prompt: Prompt 위험 탐지 모델

`risk-prompt`는 [kakaocorp/kanana-safeguard-prompt-2.1b](https://huggingface.co/kakaocorp/kanana-safeguard-prompt-2.1b)를 사용해 Prompt Injection과 Prompt Leaking 신호를 탐지한다. 최종 허용·차단 정책을 내리는 모델은 아니다.

| 항목 | 내용 |
|---|---|
| 라이선스 | Apache-2.0 |
| 구조 | LlamaForCausalLM |
| 지원 언어 | Korean, English |
| 정상 label | `<SAFE>` |
| 위험 label | `<UNSAFE-A1>`, `<UNSAFE-A2>` |
| 평가 방식 | 첫 생성 token 기준 단일 label 분류 |

모델 설정에는 `hidden_size=1792`, `num_attention_heads=24`, `head_dim=128`이 함께 선언돼 있으며 hidden size가 attention head 수로 나누어떨어지지 않는다. 이 explicit `head_dim`을 허용하지 않는 Transformers/vLLM 조합에서는 시작 전에 설정 검증이 실패할 수 있다. 현재 이미지의 호환성 patch와 적용 조건은 [`ops/patches/README.md`](../../../ops/patches/README.md)를 따른다.

실제 생성 길이, timeout, GPU 사용량, Risk API 계약은 [`configs/model_serving.yaml`](../../../configs/model_serving.yaml)과 [API 인터페이스](../api_reference.md)를 기준으로 한다.

## 참고 자료

- [Kanana Safeguard Prompt 모델 카드](https://huggingface.co/kakaocorp/kanana-safeguard-prompt-2.1b)

