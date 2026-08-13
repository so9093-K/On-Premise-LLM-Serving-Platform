# local-main: 메인 모델

`local-main`은 외부 API에 노출되는 논리 모델 ID다. 하나의 고정 체크포인트를 뜻하지 않으며, 운영자는 Main Model 프로필을 전환할 수 있다. 현재 기본 프로필과 대안 프로필, 실제 revision·이미지·vLLM 인자는 [`configs/main_model_profiles.yaml`](../../../configs/main_model_profiles.yaml)을 확인한다.

## 현재 기본 프로필의 upstream 모델

현재 기본 프로필은 [RedHatAI/gemma-4-12B-it-FP8-Dynamic](https://huggingface.co/RedHatAI/gemma-4-12B-it-FP8-Dynamic)를 사용한다.

| 항목 | 내용 |
|---|---|
| 기반 모델 | [google/gemma-4-12B-it](https://huggingface.co/google/gemma-4-12B-it) |
| 라이선스 | Gemma |
| 구조 | 약 11.95B parameter의 dense unified multimodal 모델 |
| 지원 modality | text, image, audio, video |
| 공식 context 사양 | 262,144 tokens (Gemma 4 12B Unified) |
| 체크포인트 | llm-compressor 기반 FP8 Dynamic + iMatrix 양자화. vision/audio embedding 계층은 양자화 대상에서 제외됨 |

RedHatAI 카드가 이 체크포인트를 특정 안정 vLLM 버전이 아닌 nightly vLLM에서 검증했다고 명시하므로, upstream 사양만으로 이 플랫폼의 GPU·이미지·모달리티 동작을 보장할 수 없다. 실제 적용 전에는 프로필의 호환성 기록과 boot canary, Runtime 검증 결과를 확인한다.

## 참고 자료

- [RedHatAI FP8 Dynamic 모델 카드](https://huggingface.co/RedHatAI/gemma-4-12B-it-FP8-Dynamic)
- [Gemma 4 12B IT 모델](https://huggingface.co/google/gemma-4-12B-it)
- [Gemma 4 공식 모델 카드](https://ai.google.dev/gemma/docs/core/model_card_4)
- [vLLM Gemma 4 안내](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)

