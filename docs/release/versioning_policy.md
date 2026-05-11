# 버전 정책

현재 기준선은 `0.0.1`이다.
Python 패키징 버전은 PEP 440 규칙에 따라 동일하게 `0.0.1`로 표기한다.
문서, Docker image tag, ZIP 이름, OpenAPI version, `.env`의 `PROJECT_VERSION`은 `0.0.1`을 사용한다.

## 핵심 원칙

- 버전은 "파일을 수정했다"가 아니라 "검증 가능한 기준선을 새로 만들었다"는 의미로만 올린다.
- 운영 전 내부 수정만으로 patch version 남발 금지.

## When not to bump VERSION

다음 변경은 `VERSION`을 올리지 않는다.

- 문서 오타, 설명 문구, 예시 보강
- 테스트 추가 또는 테스트 내부 구조 개선
- 내부 report 정리
- 아직 배포되지 않은 `.env.example` 설명 조정
- 운영 전 UX 문구 개선
- 코드 동작이 바뀌지 않는 리팩터링

## VERSION을 올리는 기준

다음 중 하나에 해당할 때만 새 버전을 만든다.

- API 계약 또는 OpenAPI schema가 바뀐다.
- 환경변수 이름, 의미, 기본 동작이 바뀐다.
- Docker image tag나 배포 artifact를 운영자가 구분해야 한다.
- 이미 공유한 기준선에서 실제 버그 수정이 필요하다.
- GPU/vLLM 검증 결과를 반영해 새로운 기준선을 만들어야 한다.

## 표기 규칙

| 대상 | 표기 |
|---|---|
| `VERSION` | `0.0.1` |
| `version_manifest.json.version` | `0.0.1` |
| `version_manifest.json.python_package_version` | `0.0.1` |
| `pyproject.toml` | `0.0.1` |
| Docker image tag | `ai-model-serving-platform:0.0.1` |
| OpenAPI `info.version` | `0.0.1` |
| ZIP 이름 | `ai_model_serving_platform_0.0.1.zip` |

## 변경 명령

```bash
make reset-version NEW_VERSION=0.0.1
```

`reset_version.py`는 프로젝트 버전과 Python 패키징 버전을 구분해 갱신한다.
