# Gemma4 31B AWQ 모델 검토

이 문서는 `local-main` 모델 후보의 운영 제약을 설명한다. 모델명과 vLLM option은 원문을 유지한다.

## 사용 목적

`local-main`은 Gateway의 `/v1/chat/completions`를 담당하는 주 LLM이다. 프로젝트는 vLLM OpenAI-compatible API를 upstream으로 사용한다.

## 기본 정책

- served model name: `local-main`
- context cap: `8192`
- output cap: `1024`
- concurrency: 보수적으로 `1`
- GPU memory utilization reference: `0.58`
- quantization: `awq`
- prefix caching: 활성화
- tool calling: Gemma 계열 parser 사용

## 검증 필요 항목

- 실제 GPU host에서 cold start 시간
- peak VRAM
- chat latency p50/p95
- queue timeout 발생률
- OOM/restart 여부
- vLLM image와 parser option 호환성

문서의 숫자는 운영 기본값이며, 최종 성능값은 `scripts/validation/runtime_validation.py` 결과로 갱신한다.
