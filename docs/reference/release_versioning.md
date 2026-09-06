# Release와 Version Reference

이 문서는 package version, API version, container image tag, config schema version의 역할을 구분하고 release artifact를 만들 때 확인할 기준을 정리한다. 실제 값은 `VERSION`, `version_manifest.json`, `pyproject.toml`, OpenAPI와 image 설정을 기준으로 한다.

---

## Version의 역할

| 구분 | 기준 | `make reset-version` 반영 |
|---|---|:---:|
| Package version | `VERSION` | 예 |
| Python package version | `pyproject.toml` | 예. prerelease는 PEP 440 표기 사용 |
| API contract version | `specs/openapi.*.yaml` | 예 |
| Platform / Unified vLLM 기본 image tag | `version_manifest.json`, `.env.*.example`, `configs/recommended_images.yaml` | 예 |
| Config schema version | 각 `configs/*.yaml`의 `version` | 아니오 |
| Runtime image digest | CI artifact와 배포 대상 `.env` | 아니오 |

`VERSION`은 package와 사람이 읽는 기본 image tag의 기준이다. 실제 배포에서 사용하는 container image의 재현성 기준은 tag가 아니라 immutable digest다. config schema version은 package version과 독립적이며 해당 config의 구조가 바뀔 때만 변경한다.

---

## VERSION을 올리는 시점

다음 중 하나에 해당할 때 새 package version을 만든다.

- 공개 API 계약 또는 OpenAPI schema가 바뀜
- 환경변수의 이름·의미·기본 동작이 바뀜
- 운영자가 image 또는 배포 artifact를 새로운 기준선으로 구분해야 함
- 공유된 기준선의 실제 버그를 수정함
- GPU/vLLM 검증 결과를 반영해 새 운영 기준선을 만듦

다음만으로는 올리지 않는다.

- 문서 문구·오탈자·설명 보강
- 코드 동작을 바꾸지 않는 리팩터링
- 테스트 구조 변경 또는 내부 report 정리
- config schema version만 변경하는 경우

---

## Version 변경

```bash
make reset-version NEW_VERSION=<x.y.z 또는 x.y.z-rc.n>
make validate
```

이 명령은 `VERSION`, version manifest, Python package version, checked-in OpenAPI version, 안전한 env template의 `PROJECT_VERSION`과 기본 image tag, 권장 image 설정, root README의 package version 표기를 함께 맞춘다.

과거 `CHANGELOG` 항목, config schema version, CI에서 생성한 digest와 대상 서버의 runtime state는 변경하지 않는다.

---

## Release Package

배포용 source package는 필요할 때만 만든다.

```bash
make validate
make test
make package
```

출력은 `dist/ai_model_serving_platform_<VERSION>.zip`이다. ZIP에는 Git tracked source,
config, spec, 필요한 ops artifact, 동일 버전을 검증할 `tests/`와 안전한 env example을
포함한다. 실제 `.env`, `.runtime`, log, model cache, Python cache, private tool directory,
GitHub Actions workflow는 포함하지 않는다.

CI image build와 immutable digest 전달은 [9. CI/CD](../09_cicd.md), 대상 서버 적용과 rollback은 [10. 배포](../10_deployment.md)를 따른다.
