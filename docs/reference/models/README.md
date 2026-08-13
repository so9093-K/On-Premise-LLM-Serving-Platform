# 모델 참고 자료

이 디렉터리는 플랫폼이 사용하는 upstream 모델의 사양, 라이선스, 입력 특성, 알려진 제약을 정리한다. 실행값을 정하는 문서가 아니다.

현재 모델 ID·공개 capability는 [`configs/model_catalog.yaml`](../../../configs/model_catalog.yaml), 공통 Runtime 연결·운영 정책은 [`configs/model_serving.yaml`](../../../configs/model_serving.yaml), Main Model의 실제 vLLM 실행값과 Gateway 요청 정책은 [`configs/main_model_profiles.yaml`](../../../configs/main_model_profiles.yaml)을 기준으로 한다.

| 논리 모델 ID | 참고 문서 | 역할 |
|---|---|---|
| `local-main` | [메인 모델](local-main.md) | Chat·멀티모달 모델. 활성 프로필을 전환할 수 있다. |
| `local-embed` | [일반 임베딩](local-embed.md) | 일반 텍스트 임베딩 |
| `local-embed-ko` | [한국어 임베딩](local-embed-ko.md) | 한국어 Retrieval 임베딩 |
| `risk-prompt` | [Prompt 위험 탐지 모델](risk-prompt.md) | Prompt Injection·Leaking 신호 탐지 |
