# ADR-0013: .env 비파괴 동기화 정책

## Status

Accepted

## Context

`.env`는 템플릿(`.env.compose.example`, `.env.local.example`)에서 생성되지만 운영자가 소유한다.
실제 크리덴셜(HF_TOKEN, API 키, ADMIN_API_KEY, GRAFANA_ADMIN_PASSWORD 등), 서버 전용 설정(GATEWAY_BIND_ADDR, HF_CACHE_DIR 절대경로), 배포 이미지 참조(PLATFORM_IMAGE, RISK_VLLM_IMAGE)가 담겨 있어 한 번 설정하면 재생성하면 안 된다.

`git pull` 이후 템플릿에 새 키가 추가되거나 폐기 키가 제거될 수 있다.
이때 `.env`를 그대로 두면 신규 키 누락으로 서비스가 기동 실패하고, `--force`로 재생성하면 크리덴셜이 교체되어 운영 중인 서비스가 인증 실패한다.

기존 `preserve_existing_values()` 로직은 `--force` 재생성 시 기존 값을 최대한 유지하지만, 파일 전체를 다시 쓰기 때문에 신규 키만 선택적으로 추가하는 용도로는 적합하지 않다.

## Decision

`make sync-env`(`setup_env.py --sync-env`)를 도입한다.

- 기존 `.env`의 `BUILD_PROFILE` 값으로 참조 템플릿을 자동 감지한다.
- 템플릿에만 있고 `.env`에 없는 키를 템플릿 기본값으로 추가한다.
- `RETIRED_ENV_KEYS`에 등록된 폐기 키를 제거한다.
- `ALWAYS_REFRESH_KEYS`·`GENERATED_SECRET_KEYS` 여부와 무관하게 **기존 값은 모두 보존한다.** 시크릿을 재생성하지 않는다.
- `--env-file <path>` 옵션으로 프로젝트 루트 밖의 `.env`(별도 배포 디렉터리 등)도 대상으로 지정할 수 있다.
- `--dry-run`으로 실제 변경 없이 추가·제거 대상 키를 미리 확인할 수 있다.

`EXPOSURE_MODE`와 `EXPOSURE_AUDIENCE`는 `init-env-compose-force` 후 리셋되므로, `bootstrap.sh`(`make rebuild-full`)에서 재초기화 전 기존 값을 읽어 재초기화 후 복원한다. 이는 `AUTH_MODE`에 이미 적용된 패턴과 동일하다.

CI/CD 배포(`deploy_gitlab_compose.sh`)는 `.env` 이미지 참조 업데이트 직후 `make sync-env`를 호출해 서버 `.env`에 신규 템플릿 키를 자동 반영한다.

## Consequences

| Positive | Negative |
|---|---|
| `git pull` 이후 크리덴셜 재생성 없이 `.env` 최신화 가능 | 신규 키 기본값이 환경에 맞지 않으면 운영자가 수동으로 수정해야 한다 |
| CI/CD 배포 시 서버 `.env` 크리덴셜 보존 보장 | `RETIRED_ENV_KEYS` 관리가 필요하다 — 폐기 키를 등록하지 않으면 `.env`에 남는다 |
| `rebuild-full` 시 EXPOSURE_MODE 설정이 초기화되지 않음 | |
| `--env-file`로 외부 `.env` 동기화 가능 | |

## Operational impact

| 상황 | 명령 |
|---|---|
| `git pull` 이후 `.env` 키 동기화 | `make sync-env` |
| 동기화 대상 확인 (미리보기) | `make sync-env --dry-run` (또는 `setup_env.py --sync-env --dry-run`) |
| 배포 디렉터리 `.env` 동기화 | `setup_env.py --sync-env --env-file /opt/acl-ai-gateway/.env` |
| 전체 재빌드 시 EXPOSURE_MODE 유지 | `make rebuild-full` — 자동 처리, 별도 조치 불필요 |

CI/CD 파이프라인에서는 `deploy_gitlab_compose.sh`가 자동으로 `make sync-env`를 호출하므로 별도 수동 개입이 없다.

## Migration notes

이 ADR 도입 이전에 생성된 `.env`는 신규 키 16개(`EXPOSURE_MODE`, `EXPOSURE_AUDIENCE`, `GRAFANA_BIND_ADDR`, `PROMETHEUS_BIND_ADDR`, `*_BIND_ADDR` 계열 등)가 누락되어 있을 수 있다.
`make sync-env`를 한 번 실행하면 기존 값 변경 없이 누락 키가 추가된다.

## Related

- [ADR-0012](0012-auth-ownership-and-compose-exposure-source-of-truth.md) — Auth·Exposure profile source-of-truth 정책 (EXPOSURE_MODE 보존 배경)
- `scripts/config/setup_env.py` — `sync_env_keys()`, `RETIRED_ENV_KEYS`, `ALWAYS_REFRESH_KEYS`
- `docs/operations/configuration_lifecycle.md` — `.env` 환경 파일 선택 및 동기화 UX
