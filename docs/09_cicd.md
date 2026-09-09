# 9. 자동화 경계

이 저장소는 GitHub를 Source Repository로 사용한다. 현재 자동화는
[GitHub 검증 워크플로](../.github/workflows/validate.yml)가 `main` push, Pull Request와
수동 실행에서 macOS·Ubuntu의 application/contract 검사를 수행하는 범위까지다.

Container image publish와 운영 배포를 담당하는 CI/CD pipeline은 현재 정의하지 않는다.
특정 CI provider, registry 또는 runner를 먼저 선택하지 않고도 같은 결과를 만들 수 있도록
검증·빌드·배포 동작을 저장소 안의 명령과 스크립트로 유지한다.

## 9.1 책임 구분

| 책임 | 현재 진입점 | 기준 |
|---|---|---|
| Application·contract 검사 | `make check` | `pyproject.toml`, lock, configs, specs, tests |
| Platform image build | `make build-image` | `Dockerfile`, `requirements.runtime.lock` |
| Unified vLLM image build | `make build-vllm-unified-image` | `configs/vllm_unified_build.yaml`, runtime Dockerfile·patch |
| Release source package | `make package` | Git tracked source와 packaging exclusion |
| 로컬 target lifecycle | `make setup/build/prepare/up/status/down` | deployment target와 `.env` |
| 원격 release 전송 | `scripts/deploy/deploy_compose_release.sh` | 명시적 image digest와 배포 환경 입력 |
| 원격 release 수렴·복구 | `scripts/deploy/apply_remote_release.sh` | staged release의 Compose 적용, readiness, rollback |

GitHub Actions 같은 자동화 도구는 위 진입점을 호출하는 adapter다. Python dependency,
Docker build argument, runtime 기동 순서와 rollback 정책을 workflow YAML에 다시 구현하지
않는다.

## 9.2 현재 검증 자동화

GitHub의 app/contract workflow는 다음 두 환경을 독립적으로 확인한다.

- Ubuntu: Linux application·shell·contract 호환성
- macOS: 로컬 개발 환경의 application·contract 호환성

Ubuntu는 `.python-version`, macOS는 `configs/macos_mlx_runtime.yaml`의 major.minor에 맞는 runner 제공 Python을 사용한다.
Linux 운영 image의 exact Python patch와 base digest는 `Dockerfile`이 별도로 소유한다.

이 workflow는 다음 작업을 수행하지 않는다.

- Docker image build 또는 registry push
- 모델 다운로드
- CUDA·NVIDIA GPU runtime 실행
- 운영 서버 배포
- 장시간 부하·성능 측정

따라서 workflow 성공은 application/contract 검증 결과이며 GPU runtime qualification을
대체하지 않는다.

## 9.3 빌드와 배포의 독립 경계

Image build와 registry publish는 다른 책임이다.

```text
Source + locked inputs
        ↓
Repository build script
        ↓
Local image
        ↓
선택된 외부 자동화가 registry에 publish
        ↓
Immutable name@sha256 digest
        ↓
Provider-neutral deploy entrypoint
```

현재 build script는 local tag와 image label을 만들지만 특정 registry로 push하지 않는다.
운영 승격 체계를 추가할 때는 build 결과를 registry에 push하고 실제 digest를 다음 단계로
전달해야 한다. Mutable branch tag나 local image ID를 운영 배포 identity로 사용하지 않는다.

원격 배포 controller는 CI provider의 predefined variable을 읽지 않는다. 호출자는 다음과 같은
일반 입력을 명시한다.

- 배포할 Platform image의 immutable ref
- 새 Unified vLLM image가 필요한 경우 해당 immutable ref
- release ID
- 배포 대상과 release root
- image를 pull할 registry endpoint와 credential
- full/rolling mode 및 runtime profile

환경별 credential 값과 인증 정책은 저장소에 기록하지 않는다.

## 9.4 자원 경계

정적 검증과 unit/contract test는 GPU runtime을 시작하지 않는다. `make check` 안에서
`validate`와 `test`는 순서대로 실행되며, OS matrix의 병렬 실행은 GPU VRAM과 무관하다.

향후 image publish·배포 자동화를 추가할 때는 다음 자원을 별도로 직렬화한다.

| 자원 | 직렬화할 작업 | 이유 |
|---|---|---|
| Linux image builder | 대형 Unified vLLM image build·push | base layer의 disk·memory·network 경합 방지 |
| GPU runtime target | deploy와 live runtime qualification | 동시에 model을 load하거나 release를 교체하지 않도록 보장 |

Runtime 내부의 실제 VRAM 안전성은 CI stage가 아니라 `configs/gpu_budgets.yaml`, runtime
admission, prerequisite 순차 기동과 readiness/rollback 정책이 소유한다.

## 9.5 미래 자동화 추가 원칙

실제 publish·배포 요구가 생겼을 때만 자동화를 추가한다.

1. 기존 명령으로 로컬에서 같은 작업을 먼저 수행할 수 있어야 한다.
2. Workflow는 provider adapter로 유지하고 동작을 중복 구현하지 않는다.
3. Build와 publish를 구분하고 운영 결과는 immutable digest로 연결한다.
4. 대형 image builder와 GPU deployment는 서로 다른 resource lock을 사용한다.
5. 오래된 실행은 취소할 수 있지만 진행 중인 운영 배포는 새 요청으로 중단하지 않는다.
6. Workflow 문법은 provider 자체 검증을 사용하며 `make validate`에 YAML parser를 넣지 않는다.
7. CI를 위해 테스트 전용 helper나 Source of Truth 복제 파일을 만들지 않는다.

## 9.6 관련 문서

- 로컬 개발과 image build: [7. 로컬 개발과 빌드](./07_local_dev_build.md)
- 테스트와 검증 범위: [8. 테스트와 검증](./08_testing_validation.md)
- Release 적용과 복구: [10. 배포](./10_deployment.md)
- 변경 영향에 따른 확인 범위: [13. 변경 가이드](./13_change_guide.md)
