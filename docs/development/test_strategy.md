# 테스트 전략

테스트는 세 층으로 나눈다.

## Unit test / 단위 테스트

`tests/unit/`은 settings, Gateway, Risk Adapter, upstream client, setup_env를 검증한다. 외부 모델 서버 없이 fake client를 사용한다.

## Contract test / 계약 테스트

`tests/contract/`은 OpenAPI ref, JSON Schema, release hygiene, runtime policy을 검증한다.

## Runtime validation / 런타임 검증

`scripts/validation/runtime_validation.py`는 실제 live service와 vLLM endpoint를 호출한다. Docker/GPU가 없는 환경에서는 `--config-only`로 설정 정합성만 확인한다.
