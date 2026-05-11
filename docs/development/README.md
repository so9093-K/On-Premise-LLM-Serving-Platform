# 개발 가이드

개발자는 먼저 계약 검증과 테스트를 통과시켜야 한다.

```bash
python -m pip install --requirement requirements.lock
python -m pip install --no-deps -e ".[contract]"
make validate
make test
```

주요 원칙은 다음과 같다.

1. 설명 문서는 한국어를 기본으로 한다.
2. API path, JSON field, env key, command, 제품명은 원문을 유지한다.
3. Gateway와 Risk Adapter의 public contract를 바꾸면 `specs/`, `tests/contract/`, `README.md`를 함께 수정한다.
4. fake runtime은 `tests/` 안의 test double로만 허용한다.
5. 실제 운영 동작은 `scripts/validation/runtime_validation.py`로 검증 결과를 남긴다.

## 이 디렉터리 문서

| 문서 | 내용 |
|---|---|
| [build_ux.md](build_ux.md) | make 명령 의미론·빌드/기동/재빌드/제거 흐름 |
| [python_compatibility.md](python_compatibility.md) | Python 버전 호환성·런타임 제약 |
| [test_strategy.md](test_strategy.md) | 테스트 계층·계약 검증·CI 전략 |
| [final_checklist.md](final_checklist.md) | 릴리스 전 점검 체크리스트 |
| [logging_policy.md](logging_policy.md) | 로깅 정책·민감 정보 처리 규칙 |
