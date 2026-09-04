# ADR-0021: Configuration Plane과 Operator Override 경계

## Status

Proposed

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
2. **Operator overrides** — `${RUNTIME_STATE_DIR}/config/operator-overrides.yaml`;
   배포 release와 분리된 persistent host state다.
3. **Deployment environment** — shared `.env`, orchestrator, secret manager가 소유한다.
4. **Runtime state** — 현재 모델, runtime desired state 등 관측/제어 state다.

environment와 runtime state는 UI가 일반 PATCH로 수정하는 대상이 아니다. 특히 image,
registry digest, bind address, Docker/GPU command, secret value는 deployment 또는 secret
owner의 입력으로 남긴다.

### Metadata-first read contract

설정 UI와 API는 raw file을 authority로 삼지 않는다. 서버는 key별로 아래 정보를 가진
metadata/effective projection을 제공한다.

- key, type, validation constraints, human-readable meaning
- configured/effective/default value 및 effective source
- owner (`repository`, `operator`, `deployment`, `runtime`, `secret`)
- sensitivity와 value-redaction policy
- impact 및 apply mode (`hot_reload`, service/runtime/compose restart, redeploy)
- 관련 ADR 및 target/feature applicability

초기 API 범위는 **read-only**다. `GET /admin/config/schema`와
`GET /admin/config/effective`는 admin authorization을 사용하고 secret 값은 어떤 profile에서도
반환하지 않는다. secret은 configured 여부, source class, rotation requirement만 표시한다.

### Operator mutation contract (후속 단계)

변경 API는 metadata에서 `owner=operator` 및 `editable=true`인 key만 대상으로 한다. 전체
파일 PUT 대신 key-scoped PATCH를 사용하며, revision/ETag precondition을 요구한다. apply 전에는
validation, effective diff, impact plan을 반환하고, apply 결과와 verification을 history에 남긴다.

`operator-overrides.yaml`, revision metadata, history는 release 밖 runtime state에 저장한다.
rollback은 history revision 단위로 수행하며, 직접 `.env` 편집이나 image 내 YAML 변경을 대신하지
않는다.

### Secret handling

secret의 존재와 소유자는 discoverable하게 표시할 수 있지만 원문 조회(reveal)는 기본 API/UI에
넣지 않는다. 새 값 입력·교체·회전은 secret owner workflow로 위임한다. 추후 break-glass 열람이
필요하다면 별도 권한, 목적 제한, 감사 로그, 만료 정책을 갖는 독립 설계로 다룬다.

## Consequences

| Positive | Negative |
|---|---|
| UI와 API가 배포 source-of-truth를 침범하지 않는다 | metadata와 effective resolver 구현이 선행된다 |
| 설정의 실제 값·출처·영향을 설명할 수 있다 | 일부 deployment-owned 값은 UI에서 편집할 수 없다 |
| concurrent overwrite와 secret export 위험을 줄인다 | revision/history persistence와 migration이 필요하다 |
| release rollback과 operator override rollback의 책임이 분리된다 | apply mode별 orchestration 계약이 추가된다 |

## Migration notes

1. read-only schema/effective resolver와 response contract를 추가한다.
2. repository policy key부터 metadata를 등록하고 deployment/runtime/secret key를 명시적으로
   분류한다.
3. persistent operator store와 revision/history를 추가한다.
4. PATCH validation, impact plan, apply/verification을 추가한 뒤 Admin Console을 연결한다.

## Related

- ADR-0012: Auth 소유권과 Compose Exposure Profile Source-of-Truth 분리
- ADR-0013: Env lifecycle non-destructive sync
- ADR-0020: Runtime Control과 Deployment Target 분리
- `configs/env_contract.yaml`
- `configs/deployment_targets.yaml`
- `src/ai_model_serving/services/runtime_state.py`
