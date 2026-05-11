# Python 호환성

이 프로젝트의 app/control-plane 코드는 Python `>=3.12,<3.15`를 지원한다. 즉 CPython 3.12, 3.13, 3.14를 지원 범위로 둔다.

운영 권장 기준은 여전히 Python `3.12.13`이다. vLLM, PyTorch, CUDA, container base image는 Python minor별 wheel/ABI/드라이버 조합 영향을 받으므로 full-stack GPU/vLLM 운영 지원은 minor별 runtime validation 결과로 확정한다.

## 정책

| 구분 | 정책 |
|---|---|
| App/control-plane | Python `>=3.12,<3.15` |
| 권장 production runtime | Python `3.12.x`, 기준 `3.12.13` |
| Python 3.13/3.14 | 앱/제어면 지원. GPU/vLLM full-stack은 minor별 검증 필요 |
| CI/검증 | 3.12, 3.13, 3.14 matrix 권장 |

## Fail-fast UX

`make validate`, `make test`, `make start`, `make ready-local`, `make ready-full`, `make doctor`는 `scripts/build/check_python.py`로 현재 인터프리터가 `>=3.12,<3.15`인지 먼저 확인한다.

권장 production minor를 강제해야 하는 배포 파이프라인에서는 다음처럼 실행한다.

```bash
python scripts/build/check_python.py --strict-recommended --context production-release
```

3.13/3.14에서 full-stack 검증을 의도적으로 진행할 때는 다음 환경변수로 권장 minor 경고를 명시적으로 우회한다.

```bash
ALLOW_NON_RECOMMENDED_PYTHON=1 python scripts/build/check_python.py --strict-recommended --context full-stack-validation
```

## 사이드 이펙트

- `pyproject.toml`은 설치 범위를 `>=3.12,<3.15`로 연다.
- `.python-version`은 개발자 기본값으로 `3.12.13`을 유지한다.
- Dockerfile은 기본 production image로 `python:3.12.13-slim`을 유지한다. Python 3.13/3.14 이미지는 별도 validation matrix에서 다룬다.
