# ADR-0021: Configuration Plane과 Operator Override 경계

## Status

Accepted

## Context

플랫폼의 repository YAML은 Docker image에 포함되고, 배포 환경의 `.env`와 runtime
state는 host에서 소유한다. 따라서 Admin UI가 image 안의 YAML 또는 shared `.env`를
직접 수정하면 다음 문제가 생긴다.

- container 교체 또는 새 release에서 변경이 사라진다.
- CI/CD가 소유하는 배포 입력과 운영자 변경이 섞여 재현성과 rollback이 깨진다.
- 전체 설정 파일 PUT은 서로 무관한 값을 덮어쓸 수 있다.
- secret 원문을 UI에 반환하면 API 자체가 credential export 경로가 된다.

운영자는 설정을 발견하고, 현재의 effective 값과 출처 및 변경 영향을 이해할 수
있어야 한다. 이는 `editable`과 `즉시 적용 가능`을 같은 의미로 취급하지 않는
Configuration Plane이 필요함을 뜻한다.

## Decision

### Canonical sources와 precedence

설정은 아래 계층을 갖는다. 뒤의 계층이 앞의 계층을 override할 수 있는지는 각 key의
metadata가 명시적으로 허용할 때만 가능하다.

1. **Repository policy/defaults** — `configs/*.yaml`; release와 함께 immutable하다.
2. **Operator overrides** — persistent platform state의 `config/operator-overrides.yaml`;
   배포 release와 분리된 host state다.
3. **Deployment environment** — shared `.env`, orchestrator, secret manager가 소유한다.
4. **Runtime state** — 현재 모델, runtime desired state 등 관측/제어 state다.

environment와 runtime state는 UI가 일반 PATCH로 수정하는 대상이 아니다. 특히 image,
registry digest, bind address, Docker/GPU command, secret value는 deployment 또는 secret
owner의 입력으로 남긴다.

Operator override의 canonical root는 실제 구현에서 target-neutral persistent platform state
경로로 고정한다. dynamic/static target 중 일부에서만 유지되는 경로를 Configuration Plane의
영속 저장소로 사용하지 않는다.

### Metadata-first read contract

설정 UI와 API는 raw file을 authority로 삼지 않는다. 서버는 key별로 아래 정보를 가진
metadata/effective projection을 제공한다.

- key, type, validation constraints, human-readable label/meaning
- configured/effective/default/operator value 및 effective source
- owner (`repository`, `operator`, `deployment`, `runtime`, `secret`)
- `control_surface` (`configuration`, `runtime`, `main_model`, `deployment`, `secret`, `repository`)
- sensitivity와 value-redaction policy
- impact 및 apply mode (`hot_reload`, service/runtime/compose restart, redeploy)
- 관련 ADR 및 target/feature applicability

metadata는 API가 임의의 `AppSettings` attribute를 reflection으로 읽게 해서는 안 된다.
각 metadata item은 presentation용 `projection` id를 가지며, runtime은 코드에 명시된
allowlist에서만 해당 값을 읽는다. 이 allowlist는 설정 중복이 아니라 secret과 내부
state가 API 응답으로 새지 않게 하는 노출 경계다.

초기 API 범위는 **read-only**다. `GET /admin/config/schema`와
`GET /admin/config/effective`는 admin authorization을 사용하고 secret 값은 어떤 profile에서도
반환하지 않는다. secret은 configured 여부, source class, rotation requirement만 표시한다.

### `editable` 의미

`editable=true`는 현재 Configuration Plane이 해당 key를 실제로 validate, plan, persist,
apply, verify 할 수 있다는 뜻이다. 단순히 사람이 바꿀 수 있는 값이라는 뜻이 아니다.
`editable=true`는 `owner=operator`인 key에서만 허용한다.

runtime/main-model/deployment/secret owner의 값은 Configuration PATCH 대상이 아니더라도
Admin Console에서 해당 `control_surface`로 이동하거나 상태를 설명할 수 있다. 따라서
`editable=false`를 사용자에게 일괄적인 "수정 불가"로 표현하지 않는다.

### Runtime configuration boundary

`AppSettings`는 startup/deployment snapshot으로 유지한다. Hot-reload 가능한 operator policy는
immutable runtime snapshot/provider를 통해 요청 경로에 투영한다. 파일의 effective 값만
바뀌고 이미 생성된 GatewayService/RuntimeClient가 이전 값을 계속 사용하는 상태는 허용하지
않는다.

한 프로세스에만 안전하게 적용할 수 있는 값과 여러 프로세스가 공유하는 값은 구분한다.
후자는 공유 persistence/reload protocol과 verification이 준비되기 전에는 `editable=true`로
열지 않는다.

또한 JSON Schema, OpenAPI 설명, SLO 등 별도 public/static contract에 같은 숫자가 박혀 있는
값은 해당 계약과 runtime override의 관계를 먼저 정의한다. Operator override가 새 SSOT drift를
만들어서는 안 된다.

### Operator mutation contract

변경 API는 metadata에서 `owner=operator` 및 `editable=true`인 key만 대상으로 한다. 전체
파일 PUT 대신 key-scoped change set을 사용하며 revision/ETag precondition을 요구한다.

mutation lifecycle은 다음 순서를 따른다.

1. validation
2. effective diff와 impact plan
3. stale revision 확인 후 apply
4. 실제 runtime/effective state verification
5. history 기록

Reset은 default 값을 override 파일에 복사하는 것이 아니라 해당 operator override를 제거한다.
Rollback은 과거 revision 번호로 저장소를 되감지 않고, 과거 상태를 다시 plan/apply하여 새로운
revision을 만든다.

`operator-overrides.yaml`, revision metadata, history는 release 밖 persistent platform state에
저장한다. 직접 `.env` 편집이나 image 내 YAML 변경을 대신하지 않는다.

### Access profile과 Configuration Plane

일반 사용자 접근 UX의 기준은 ADR-0025의 `ACCESS_PROFILE=local|private|edge`다. raw
`AUTH_MODE`, `EXPOSURE_MODE`, bind primitive를 일반 Configuration form으로 노출하는 것이
기본 UX가 아니다.

Access Profile 전환은 기존 setup lifecycle의 plan/confirm 및 legacy/custom 보존 의미와
정합해야 하므로 일반 operator key PATCH와 분리한다. Console은 resolved access/security posture를
기본 표시하고 raw primitive는 Advanced/diagnostic 정보로 제공한다.

### Secret handling

secret의 존재와 소유자는 discoverable하게 표시할 수 있지만 원문 조회(reveal)는 기본 API/UI에
넣지 않는다. 새 값 입력·교체·회전은 secret owner workflow로 위임한다. 추후 break-glass 열람이
필요하다면 별도 권한, 목적 제한, 감사 로그, 만료 정책을 갖는 독립 설계로 다룬다.

## Consequences

| Positive | Negative |
|---|---|
| UI와 API가 배포 source-of-truth를 침범하지 않는다 | metadata와 effective resolver 구현이 선행된다 |
| 설정의 실제 값·출처·영향을 설명할 수 있다 | owner별 control surface가 추가된다 |
| concurrent overwrite와 secret export 위험을 줄인다 | revision/history persistence와 migration이 필요하다 |
| release rollback과 operator override rollback의 책임이 분리된다 | apply mode별 orchestration 계약이 추가된다 |
| UI effective 값과 실제 요청 경로의 runtime policy를 일치시킬 수 있다 | cross-process 설정은 별도 reload protocol이 필요하다 |

## Migration notes

1. read-only schema/effective resolver와 response contract를 추가한다. **완료**
2. repository/deployment/runtime/secret key의 metadata와 projection allowlist를 추가한다. **완료**
3. startup `AppSettings`와 hot-reload 가능한 Runtime Configuration 경계를 분리한다.
4. metadata schema를 constraints/control surface/applicability를 표현하도록 확장한다.
5. target-neutral persistent operator store와 revision/history를 추가한다.
6. PATCH validation, impact plan, apply/verification을 추가하고 실제 operator-owned key를 연다.
7. ADR-0027의 Admin Console을 연결한다.

## Related

- ADR-0012: Auth/Exposure primitive Source-of-Truth 분리 (일반 UX는 ADR-0025가 대체)
- ADR-0013: Env lifecycle non-destructive sync
- ADR-0020: Runtime Control과 Deployment Target 분리
- ADR-0025: 사용자 접근 Profile과 기존 환경의 명시적 전환
- ADR-0027: Control Plane Console과 Runtime Configuration 경계
- `configs/env_contract.yaml`
- `configs/deployment_targets.yaml`
- `configs/access_profiles.yaml`
- `src/ai_model_serving/runtime_configuration.py`
- `src/ai_model_serving/services/runtime_state.py`
