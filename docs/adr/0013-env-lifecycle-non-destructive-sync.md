# ADR-0013: .env 비파괴 동기화 정책

## Status

Accepted

## Context

`.env`는 템플릿(`.env.compose.example`, `.env.local.example`)에서 생성되지만 운영자가 소유한다.
ADR 채택 당시에는 실제 크리덴셜(HF_TOKEN, API 키, ADMIN_API_KEY, GRAFANA_ADMIN_PASSWORD 등), 서버 전용 설정(GATEWAY_BIND_ADDR, HF_CACHE_DIR 절대경로), 배포 이미지 참조(당시 PLATFORM_IMAGE, RISK_VLLM_IMAGE 등)가 함께 담겨 있어 파일 전체를 재생성하면 안 됐다. 현재 shared vLLM persistent authority는 `VLLM_IMAGE`로 수렴했으며, 이 ADR의 비파괴 동기화 원칙은 그 이후 ownership 변경에도 적용된다.

`git pull` 이후 템플릿에 새 키가 추가되거나 폐기 키가 제거될 수 있다.
이때 `.env`를 그대로 두면 신규 키 누락으로 서비스가 기동 실패하고, `--force`로 재생성하면 크리덴셜이 교체되어 운영 중인 서비스가 인증 실패한다.

기존 `preserve_existing_values()` 로직은 `--force` 재생성 시 기존 값을 최대한 유지하지만, 파일 전체를 다시 쓰기 때문에 신규 키만 선택적으로 추가하는 용도로는 적합하지 않다.

## Decision

`make sync-env`(`setup_env.py --sync-env`)를 도입한다.

- 기존 `.env`의 `BUILD_PROFILE` 값으로 참조 템플릿을 자동 감지한다.
- 템플릿에만 있고 `.env`에 없는 키를 템플릿 기본값으로 추가한다.
- `configs/env_contract.yaml`의 `removed_keys`에 등록된 키만 제거한다. env 키 정책의 단일 소스는 그 파일이며, `setup_env.py`는 목록을 복제하지 않고 읽어서 쓴다. 각 항목은 `reason`으로 구분한다.
  - `yaml_owned` — 다른 `configs/*.yaml`이 소유하므로 `.env`가 가리면 안 되는 키.
  - `deprecated` — 가리킬 대상 자체가 없어진 키(참조하는 코드가 사라졌거나, 값이 남아 있는 것 자체가 위험한 경우).
- `validate_env_contract.py`가 같은 목록으로 "등록된 키가 `.env*.example`에 다시 등장하지 않았는지"를 검증한다. 제거와 검증이 한 소스를 공유하므로, 등록과 템플릿이 갈라져 "validate는 통과하는데 sync-env는 매번 지우는" 상태가 생기지 않는다.
- 등록된 키는 그 키를 소비하는 쪽에서도 `.env`를 읽지 않아야 한다. 지우는 쪽과 읽는 쪽이 어긋나면 제거가 무의미하다(예: `RISK_VLLM_BASE_IMAGE`는 프로세스 환경변수 override만 받는다).
- **"템플릿에 없는 키"는 제거 기준이 아니다.** 배포 서버 `.env`에는 템플릿에 존재한 적 없는 서버 전용 설정(`MAIN_MODEL_STATE_PATH` 등)이 정상적으로 들어 있고, 원격 release 적용 경로가 이미지 참조 갱신 직후 `sync-env`를 호출하므로 그 기준을 쓰면 배포할 때마다 운영 설정이 사라진다.
- Operator-owned 기존 값은 보존하고 시크릿을 재생성하지 않는다. 단, `configs/recommended_images.yaml`에서 `reference_policy: immutable_upstream`으로 선언한 third-party image env key는 repository-owned projection이므로 `sync-env`가 canonical digest로 갱신한다. Platform/vLLM처럼 project-built image reference는 기존 값을 보존한다.
- `--env-file <path>` 옵션으로 프로젝트 루트 밖의 `.env`(별도 배포 디렉터리 등)도 대상으로 지정할 수 있다.
- `--dry-run`으로 실제 변경 없이 추가·제거 대상 키를 미리 확인할 수 있다.

당시 `EXPOSURE_MODE`와 `EXPOSURE_AUDIENCE`는 bootstrap의 compose env 강제 재생성 후
리셋되므로, 재초기화 전 기존 값을 읽어 복원했다. 현재 bootstrap 명령은
[ADR-0023](0023-local-lifecycle-command-boundaries.md)에 따라 제거됐고 `make setup`은
기존 `.env`를 강제 재생성하지 않고 target 소유 필드만 동기화한다.

현재 provider-neutral 원격 적용 스크립트(`scripts/deploy/apply_remote_release.sh`)는 `.env`
이미지 참조 업데이트 직후 `make sync-env`를 호출해 서버 `.env`에 신규 템플릿 키를
자동 반영한다. 과거 provider-specific wrapper는 제거됐다.

## Consequences

| Positive | Negative |
|---|---|
| `git pull` 이후 크리덴셜 재생성 없이 `.env` 최신화 가능 | 신규 키 기본값이 환경에 맞지 않으면 운영자가 수동으로 수정해야 한다 |
| 원격 배포 시 서버 `.env` 크리덴셜 보존 + upstream infrastructure image digest 수렴 | `removed_keys`와 repository-managed image policy 관리가 필요하다 |
| target setup 재실행 시 EXPOSURE_MODE 설정이 초기화되지 않음 | |
| `--env-file`로 외부 `.env` 동기화 가능 | |

## Operational impact

| 상황 | 명령 |
|---|---|
| `git pull` 이후 `.env` 키 동기화 | `make sync-env` |
| 동기화 대상 확인 (미리보기) | `.venv/bin/python scripts/config/setup_env.py --sync-env --dry-run` |
| 배포 디렉터리 `.env` 동기화 | `setup_env.py --sync-env --env-file /opt/acl-ai-gateway/.env` |
| target setup 재실행 시 EXPOSURE_MODE 유지 | `make setup` — 기존 `.env`의 비소유 필드 보존 |

원격 release 적용 경로에서는 `apply_remote_release.sh`가 자동으로 `make sync-env`를
호출하므로 별도 수동 개입이 없다.

## Migration notes

이 ADR 도입 이전에 생성된 `.env`는 신규 키 16개(`EXPOSURE_MODE`, `EXPOSURE_AUDIENCE`, `GRAFANA_BIND_ADDR`, `PROMETHEUS_BIND_ADDR`, `*_BIND_ADDR` 계열 등)가 누락되어 있을 수 있다.
`make sync-env`를 한 번 실행하면 operator-owned 기존 값은 유지한 채 누락 키가 추가되고, repository-managed upstream image key는 현재 pinned digest로 수렴한다.

## Related

- [ADR-0012](0012-auth-ownership-and-compose-exposure-source-of-truth.md) — Auth·Exposure profile source-of-truth 정책 (EXPOSURE_MODE 보존 배경)
- `configs/env_contract.yaml` — `removed_keys`(제거 대상 키와 사유의 단일 소스)
- `scripts/config/setup_env.py` — `sync_env_keys()`, `_removed_env_keys()`, `ALWAYS_REFRESH_KEYS`
- `scripts/validation/validate_env_contract.py` — 등록된 키가 예시 파일에 다시 등장하지 않는지 검증
- `docs/05_configuration.md` — `.env` 환경 파일 선택 및 동기화 UX
