# Retired Gemma4 31B AWQ 모델 검토

이 문서는 이전 `local-main` 후보였던 `QuantTrio/gemma-4-31B-it-AWQ`의 retired 검토 기록이다. 현재 기본 `local-main`은 `LargitData/gemma-4-26b-a4b-it-fp8`이며, 운영 설정은 `configs/model_catalog.yaml`, `configs/model_serving.yaml`, [GPU Resource Plan](../resources/gpu_resource_plan.md)을 기준으로 한다.

## 사용 목적

이 문서는 모델 교체 의사결정의 과거 배경만 보존한다. 현재 Gateway의 `/v1/chat/completions`는 `local-main`이라는 served model name을 유지하되, upstream checkpoint는 LargitData FP8 Gemma 4 26B-A4B로 교체되었다.

## 기본 정책

- status: retired candidate review
- previous served model name: `local-main`
- previous context cap: `8192`
- previous GPU memory utilization reference: `0.58`
- previous quantization: `awq`
- replacement: `LargitData/gemma-4-26b-a4b-it-fp8`

## 검증 필요 항목

- 실제 GPU host에서 cold start 시간
- peak VRAM
- chat latency p50/p95
- queue timeout 발생률
- OOM/restart 여부
- vLLM image와 parser option 호환성

위 숫자는 현재 운영 기본값이 아니다. 최신 성능값은 `scripts/validation/runtime_validation.py` 결과와 runtime validation report로 갱신한다.
