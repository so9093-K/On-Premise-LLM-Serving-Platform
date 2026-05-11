# 버전 정책

현재 운영 전 기준선 후보는 `0.1.0-rc.1`이다.
Python 패키징 버전은 PEP 440 규칙 때문에 `0.1.0rc1`로 표기한다.
문서, Docker image tag, ZIP 이름, OpenAPI version, `.env`의 `PROJECT_VERSION`은 `0.1.0-rc.1`을 사용한다.

## 핵심 원칙

- 운영 전 내부 수정만으로 patch version 남발 금지 원칙을 적용한다.
- 버전은 “파일을 수정했다”가 아니라 “검증 가능한 기준선을 새로 만들었다”는 의미로만 올린다.
- `0.1.8`~`0.1.16`은 운영 전 내부 안정화 이력이며 외부 운영 기준선이 아니다.
- `0.1.0-rc.1`은 그 내부 안정화 이력을 통합한 첫 운영 전 release-candidate 기준선이다.
- GPU/vLLM full-stack 검증 전에는 정식 `0.1.0`으로 승격하지 않는다.

## 버전별 의미

| 버전 | 의미 | 사용 기준 |
|---|---|---|
| `0.1.0-dev` | 내부 개발/정리 상태 | 공유 기준선 아님 |
| `0.1.0-rc.1` | 운영 전 기준선 후보 | 로컬 테스트, 계약 검증, 패키징 검증 완료 |
| `0.1.0-rc.2` | 두 번째 운영 전 후보 | GPU/vLLM 검증 중 기준선 수준의 수정 발생 |
| `0.1.0` | 최초 운영 후보 기준선 | GPU/vLLM full-stack 검증 통과 |
| `0.2.0` | 실사용 피드백 반영 기준선 | 실제 운영 후 의미 있는 기능/운영 정책 반영 |
| `1.0.0` | 안정 운영 계약 | API/운영 정책 호환성을 외부에 약속 가능한 시점 |

## When not to bump VERSION

다음 변경은 `VERSION`을 올리지 않는다.

- 문서 오타, 설명 문구, 예시 보강
- 테스트 추가 또는 테스트 내부 구조 개선
- 내부 report 정리
- 아직 배포되지 않은 `.env.example` 설명 조정
- 운영 전 UX 문구 개선
- 코드 동작이 바뀌지 않는 리팩터링

이런 변경은 현재 기준선 안에서 누적하고, 다음 기준선 후보를 자를 때 한 번에 반영한다.

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
| `VERSION` | `0.1.0-rc.1` |
| `version_manifest.json.version` | `0.1.0-rc.1` |
| `version_manifest.json.python_package_version` | `0.1.0rc1` |
| `pyproject.toml` | `0.1.0rc1` |
| Docker image tag | `ai-model-serving-platform:0.1.0-rc.1` |
| OpenAPI `info.version` | `0.1.0-rc.1` |
| ZIP 이름 | `ai_model_serving_platform_0.1.0-rc.1.zip` |

## 변경 명령

```bash
make reset-version NEW_VERSION=0.1.0-rc.1
```

`reset_version.py`는 프로젝트 버전(`0.1.0-rc.1`)과 Python 패키징 버전(`0.1.0rc1`)을 구분해 갱신한다.
