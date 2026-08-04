# Contract 테스트

이 디렉터리는 API 계약, 문서 정책, release hygiene, runtime policy를 검증한다. 목적은 구현이 의도한 운영 계약에서 벗어나지 않도록 막는 것이다.

## 실행

```bash
make test
# 또는
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python scripts/validation/run_tests.py
```

## 포함 범위

- OpenAPI `$ref` 정합성
- Risk signal schema와 금지 field
- monitoring 기본 활성화 정책
- `.env`와 runtime secret 동기화 UX
