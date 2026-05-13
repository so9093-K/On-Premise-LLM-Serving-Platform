# 버전 정책

현재 package version은 [아래](#1-current-package-version)를 참고한다.
`make reset-version NEW_VERSION=x.y.z`로 갱신한다.

## Image tag 정책

**Docker image tag는 package version과 일치하는 것을 기본 정책으로 한다.**

```
Package version:     0.0.1
Platform image:      ai-model-serving-platform:0.0.1
Risk vLLM image:     ai-model-serving-risk-vllm-kanana:0.0.1
Config schema version: 0.1.0  ← package version과 독립
```

- `VERSION` 파일이 package version의 authoritative source다.
- `PLATFORM_IMAGE`와 `RISK_VLLM_IMAGE`는 기본적으로 package version aligned다.
- `make reset-version`은 platform image와 risk_vllm image를 함께 갱신한다.
- config schema version (`configs/model_catalog.yaml`, `configs/monitoring.yaml`, `configs/storage_paths.yaml`의 `version` 필드)은 package version과 독립적이다.
- historical report나 changelog의 과거 버전은 reset-version 대상이 아니다.

## 버전 의미론 — 각 버전의 역할

이 프로젝트에는 용도가 다른 여러 버전 체계가 공존한다. 혼동을 막기 위해 아래 구분을 따른다.

| 버전 종류 | Source of truth | `reset_version.py` 변경 여부 |
|---|---|:---:|
| Package version | `VERSION` 파일 | ✅ |
| Python package version | `VERSION` 파생 (PEP 440) | ✅ |
| API contract version | `VERSION` 파생 | ✅ |
| Platform image tag | `VERSION` 파생 (package-aligned) | ✅ |
| Risk vLLM image tag | `VERSION` 파생 (package-aligned) | ✅ |
| Config schema version | `configs/model_catalog.yaml`, `configs/monitoring.yaml`, `configs/storage_paths.yaml` 내 `version` 필드 | ❌ |
| Report format version | 생성 script | ❌ |
| Artifact schema version (날짜) | `version_manifest.json` | ❌ |

> **config schema version** (`configs/model_catalog.yaml`, `configs/monitoring.yaml`, `configs/storage_paths.yaml`의 `version` 필드)은
> 각 config 파일의 schema 버전이며 package version과 다를 수 있다. 해당 config의 필드 구조가 바뀔 때만 올린다.
>
> **예시:** Package version `0.0.1`, Config schema version `0.1.0`은 정상적인 조합이다.
> **runtime compatibility baseline** (`configs/runtime_compatibility.yaml`의 `version` 필드)은 package-aligned 기준선이다.
> `make reset-version` 실행 시 package version과 함께 갱신된다.

## 핵심 원칙

- Package version은 "파일을 수정했다"가 아니라 "검증 가능한 기준선을 새로 만들었다"는 의미로만 올린다.
- 운영 전 내부 수정만으로 patch version 남발 금지.

## When not to bump VERSION

다음 변경은 `VERSION`을 올리지 않는다.

- 문서 오타, 설명 문구, 예시 보강
- 테스트 추가 또는 테스트 내부 구조 개선
- 내부 report 정리
- 아직 배포되지 않은 `.env.example` 설명 조정
- 운영 전 UX 문구 개선
- 코드 동작이 바뀌지 않는 리팩터링
- config schema version만 바뀌는 경우

## VERSION을 올리는 기준

다음 중 하나에 해당할 때만 새 버전을 만든다.

- API 계약 또는 OpenAPI schema가 바뀐다.
- 환경변수 이름, 의미, 기본 동작이 바뀐다.
- Docker image tag나 배포 artifact를 운영자가 구분해야 한다.
- 이미 공유한 기준선에서 실제 버그 수정이 필요하다.
- GPU/vLLM 검증 결과를 반영해 새로운 기준선을 만들어야 한다.

## `reset_version.py`가 변경하는 대상

`make reset-version NEW_VERSION=x.y.z`는 **package version 및 package-aligned image tag를** 변경한다.

변경되는 파일:
- `VERSION`
- `version_manifest.json` (version, python_package_version, api_contract_version, image_tags.platform, image_tags.risk_vllm)
- `pyproject.toml`
- `specs/openapi.gateway.yaml`, `specs/openapi.risk-adapter.yaml` (info.version)
- `README.md` (패키지 버전 표 항목)
- `.env.example`, `.env.local.example`, `.env.compose.example` (PROJECT_VERSION, PLATFORM_IMAGE, RISK_VLLM_IMAGE)
- `configs/recommended_images.yaml` (platform image tag, risk_vllm image tag)
- `docs/release/versioning_policy.md` (`## 1. Current package version` 블록)

변경되지 않는 대상 (의도적 제외):
- `configs/model_catalog.yaml`, `configs/monitoring.yaml`, `configs/storage_paths.yaml` — config schema version
- `CHANGELOG.md` 과거 항목 — historical changelog
- `reports/refactor/` historical audit notes — 역사적 기록
- `version_manifest.json`의 `config_schema_versions` 필드

## 표기 형식

| 대상 | 형식 |
|---|---|
| `VERSION` | `x.y.z` 또는 `x.y.z-rc.n` |
| `version_manifest.json.version` | 동일 |
| `version_manifest.json.python_package_version` | PEP 440 (`x.y.zrcN`) |
| `version_manifest.json.api_contract_version` | 동일 |
| `version_manifest.json.image_tags.platform` | `ai-model-serving-platform:x.y.z` |
| `version_manifest.json.image_tags.risk_vllm` | `ai-model-serving-risk-vllm-kanana:x.y.z` |
| `pyproject.toml` | PEP 440 |
| Docker image tag (platform) | `ai-model-serving-platform:x.y.z` |
| Docker image tag (risk_vllm) | `ai-model-serving-risk-vllm-kanana:x.y.z` |
| OpenAPI `info.version` | 동일 |
| ZIP 이름 | `ai_model_serving_platform_x.y.z.zip` |

## 변경 명령

```bash
make reset-version NEW_VERSION=<version>
```

`reset_version.py`는 package version, Python 패키징 버전, package-aligned image tag를 함께 갱신한다.
config schema version과 historical changelog는 변경하지 않는다.

## 1. Current package version

```text
0.0.1
```
