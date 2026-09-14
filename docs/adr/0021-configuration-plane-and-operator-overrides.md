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
2. **Operator overrides** — `${PLATFORM_STATE_DIR}/config/operator-overrides.yaml`;
   배포 release와 분리된 persistent host state다.
3. **Deployment environment** — shared `.env`, orchestrator, secret manager가 소유한다.
4. **Runtime state** — 현재 모델, runtime desired state 등 관측/제어 state다.

environment와 runtime state는 UI가 일반 PATCH로 수정하는 대상이 아니다. 특히 image,
registry digest, bind address, Docker/GPU command, secret value는 deployment 또는 secret
owner의 입력으로 남긴다.

현재 supported Compose target은 `PLATFORM_STATE_DIR=/var/lib/ai-model-serving`을 canonical
persistent state root로 사용한다. Operator override store는 이 root 아래 `config/`를 사용하며
Gateway startup 때 persisted revision을 읽어 shared `RuntimeConfigurationProvider`에 hydrate한다.
손상되거나 metadata와 맞지 않는 persistent state를 조용히 무시하지 않고 startup을
fail-closed로 중단한다.

### Metadata-first read contract

설정 UI와 API는 raw file을 authority로 삼지 않는다. 서버는 key별로 아래 정보를 가진
metadata/effective projection을 제공한다.

- key, type, validation constraints, human-readable meaning
- configured/effective/default value 및 effective source
- owner (`repository`, `operator`, `deployment`, `runtime`, `secret`)
- sensitivity와 value-redaction policy
- impact 및 apply mode (`hot_reload`, service/runtime/compose restart, redeploy)
- 관련 ADR 및 target/feature applicability

metadata는 API가 임의의 `AppSettings` attribute를 reflection으로 읽게 해서는 안 된다.
각 metadata item은 presentation용 `projection` id를 가지며, runtime은 코드에 명시된
allowlist에서만 해당 값을 읽는다. 이 allowlist는 설정 중복이 아니라 secret과 내부
state가 API 응답으로 새지 않게 하는 노출 경계다.

`GET /admin/config/schema`와 `GET /admin/config/effective`는 admin authorization을 사용하고
secret 값은 어떤 profile에서도 반환하지 않는다. secret은 configured 여부, source class,
rotation requirement만 표시한다. Operator-owned Configuration item은 default/operator/effective
value, effective source, shadowing 여부와 store revision을 함께 제공한다. Effective 응답은
같은 revision을 HTTP `ETag: "config-<revision>"`으로도 노출한다.

### Operator mutation contract

변경 API는 metadata에서 `owner=operator`, `control_surface=configuration`, `editable=true`인
key만 대상으로 한다. Generic mutation은 sensitive value를 받지 않으며, 현재 구현에서
실제로 hot-reload consumer 경계가 검증된 key만 `editable=true`로 연다.

전체 파일 PUT 대신 key-scoped `set`/`reset` mutation을 사용한다. `reset`은 repository default를
operator layer에 복사하지 않고 해당 override를 제거한다. 따라서 다음 release에서 repository
default가 바뀌면 reset된 key는 새 default를 그대로 따른다.

변경은 두 단계다.

1. `POST /admin/config/plans` — `base_revision`과 changes를 검증하고 실제 저장 없이
   operator/effective before·after, source, shadowing, apply mode, risk와 canonical SHA-256
   `plan_digest`를 반환한다.
2. `PATCH /admin/config` — `If-Match: "config-<revision>"`, 같은 changes와 `plan_digest`를
   요구한다. Apply 직전에 Plan을 다시 계산해 stale revision과 검토 후 drift를 거부한다.

Apply transaction은 **persist-first**다. 먼저 operator desired state를 revision+1로 durable하게
기록하고, 같은 persisted state를 resolver와 shared `RuntimeConfigurationProvider`에 설치한 뒤
store/resolver/runtime revision과 값을 다시 읽어 convergence를 검증한다. Runtime apply 실패를
감추기 위해 persisted desired state를 이전 값으로 조용히 되돌리지 않는다. 세 계층이
동기화되지 않으면 다음 write를 fail-closed로 막고, Gateway restart 시 persisted desired state를
다시 hydrate해 실제 runtime을 수렴시킨다.

### Durable operation journal과 History projection

Mutation audit는 apply 뒤의 best-effort 로그가 아니다. `${PLATFORM_STATE_DIR}/config/history/`
아래 operation record를 **persistent state 변경 전에** `pending`으로 fsync/atomic replace하여
기록하고 terminal 결과로 갱신한다. 기록에는 raw admin token 대신 인증 방식과 key fingerprint
actor만 남긴다. Generic mutation이 sensitive key를 받지 않으므로 journal에도 secret 원문은
들어가지 않는다.

프로세스가 중간에 종료되어 `pending` record가 남으면 startup hydration 뒤 실제
store/resolver/runtime revision을 대조한다. persistence 전 종료는 `interrupted_before_persist`,
persist 후 재시작으로 수렴한 작업은 `recovered_after_restart`, 설명할 수 없는 불일치는
`interrupted_state_mismatch`로 닫는다. 불일치를 정상으로 가장하지 않는다.

`GET /admin/config/history`는 이 durable journal을 raw file API로 노출하지 않는다. operation id,
kind/status/phase, actor/request id, revision, reviewed changes와 verification만 운영자 projection으로
반환하고, rollback 복원을 위한 `overrides_before`/`overrides_after` snapshot과 내부 exception 문자열은
응답에서 제외한다. 조회는 최신 작업부터 bounded cursor pagination을 사용하며 journal이 손상되면
부분 결과를 정상으로 가장하지 않고 fail-closed한다.

### Rollback은 과거 state를 목표로 한 새 transaction

Rollback은 revision counter를 과거 번호로 되돌리는 기능이 아니다.
`POST /admin/config/rollbacks/plans`는 현재 `base_revision`과 과거 `target_revision`을 받아 durable
journal에서 검증 가능한 operator override snapshot을 복원하고, 현재 state와의 차이를 다시
`set`/`reset` changes로 계산한다. 과거 effective 값을 그대로 복사하지 않고 현재 repository,
deployment precedence에서 effective before/after와 shadowing을 다시 resolve한다.

rollback 가능한 snapshot evidence는 다음처럼 fail-closed하게 해석한다.

- revision `0`은 빈 operator override state다.
- 모든 journal operation의 `base_revision + overrides_before`는 mutation 시작 직전 synchronized state다.
- `verified`, `noop`, `recovered_after_restart`처럼 convergence가 확인된 operation의 after snapshot은
  해당 candidate revision의 stable state로 사용할 수 있다.
- 같은 revision에 서로 다른 snapshot evidence가 존재하면 임의로 하나를 선택하지 않는다.

`POST /admin/config/rollbacks`는 현재 ETag와 rollback plan digest를 다시 검증한 뒤 일반 mutation과
동일한 persist-first → runtime apply → convergence verify 경로를 사용한다. durable journal v1은
이전 release의 reader/recovery와 downgrade 호환되도록 저장된 `kind=configuration_apply`를 유지하고
rollback intent는 optional `target_revision`으로 기록한다. History API projection에서만 이를
`configuration_rollback`으로 표현한다. 예를 들어 revision 20에서 revision 15의 operator state로
rollback하면 성공 결과는 revision 21이다. rollback plan digest에는 `configuration_rollback` intent와
target revision도 포함해 같은 diff의 일반 mutation과 구분한다.

### Secret handling

secret의 존재와 소유자는 discoverable하게 표시할 수 있지만 원문 조회(reveal)는 기본 API/UI에
넣지 않는다. 새 값 입력·교체·회전은 secret owner workflow로 위임한다. 추후 break-glass 열람이
필요하다면 별도 권한, 목적 제한, 감사 로그, 만료 정책을 갖는 독립 설계로 다룬다.

## Consequences

| Positive | Negative |
|---|---|
| UI와 API가 배포 source-of-truth를 침범하지 않는다 | metadata와 effective resolver 구현이 필요하다 |
| 설정의 실제 값·출처·영향을 설명할 수 있다 | 일부 deployment-owned 값은 UI에서 편집할 수 없다 |
| stale overwrite와 secret export 위험을 줄인다 | Plan/Apply와 durable journal orchestration이 추가된다 |
| partial failure를 숨기지 않고 재시작 복구 근거를 남긴다 | 불일치 상태에서는 후속 write가 차단된다 |
| release rollback과 operator override rollback의 책임이 분리된다 | apply mode별 orchestration 계약이 추가된다 |
| rollback도 동일 transaction과 revision invariants를 재사용한다 | 오래된 history가 없거나 상충하면 rollback이 거부될 수 있다 |

## Migration notes

1. read-only schema/effective projection과 metadata v2를 추가한다. **완료**
2. runtime mutable snapshot/provider와 target-neutral persistent platform state root를 추가한다. **완료**
3. persistent operator store, revision, effective resolver와 startup hydration을 추가한다. **완료**
4. Plan/Apply/Verify, revision/ETag precondition, durable operation journal을 추가하고 검증된 hot-reload key만 `editable=true`로 연다. **완료**
5. History 조회와 rollback Plan/Apply API를 durable journal 위에 추가한다. **완료**
6. first-party Admin Console에서 Configuration/History/Rollback flow를 연결한다. **후속**

## Related

- ADR-0013: Env lifecycle non-destructive sync
- ADR-0020: Runtime Control과 Deployment Target 분리
- ADR-0025: 사용자 접근 Profile과 기존 환경의 명시적 전환
- ADR-0027: Control Plane Runtime Configuration과 Admin Console 경계
- `configs/env_contract.yaml`
- `configs/deployment_targets.yaml`
- `src/ai_model_serving/configuration_mutation.py`
- `src/ai_model_serving/operator_configuration.py`
- `src/ai_model_serving/runtime_configuration.py`
- `src/ai_model_serving/services/runtime_state.py`
