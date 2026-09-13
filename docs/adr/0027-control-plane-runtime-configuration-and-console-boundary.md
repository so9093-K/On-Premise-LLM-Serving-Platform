# ADR-0027: Control Plane Runtime Configuration과 Admin Console 경계

## Status

Accepted

## Context

ADR-0021은 repository default, operator override, deployment environment, runtime state를
분리하고, operator-owned 설정에 revision/history를 갖는 mutation contract를 후속 단계로
정했다. 현재 구현은 read-only Configuration Plane까지만 완료되어 있다.

그 사이 ADR-0025가 일반 사용자의 접근 UX를 `ACCESS_PROFILE=local|private|edge`로
단순화했다. 따라서 향후 Admin Console이 이전의 `AUTH_MODE`/`EXPOSURE_MODE` primitive를
일반 운영자 UX의 중심으로 다시 노출해서는 안 된다.

또한 현재 `AppSettings`는 application boot 시 한 번 resolve되는 frozen snapshot이다.
Gateway service, middleware, RuntimeClient가 그 객체에서 값을 읽으므로
`operator-overrides.yaml`만 저장하고 effective API 값만 바꾸면 UI와 실제 요청 경로가
서로 다른 값을 사용하는 control-plane drift가 생길 수 있다.

## Decision

### Immutable startup configuration과 mutable runtime configuration을 분리한다

`AppSettings`는 다음과 같이 부팅 또는 재배포 경계가 소유하는 값을 계속 담당한다.

- Deployment Target과 feature capability
- runtime endpoint/model identity
- 인증 bootstrap과 secret reference
- Main Model catalog/profile identity
- release/deployment identity

운영 중 즉시 다시 읽을 수 있는 operator policy는 별도
`RuntimeConfigurationSnapshot`/`RuntimeConfigurationProvider`가 소유한다.

Runtime configuration snapshot은 frozen object이며 하나의 monotonically increasing
revision을 가진다. 요청 처리 코드는 하나의 snapshot을 읽어 그 요청 동안 일관된 값을
사용할 수 있고, apply는 새 snapshot으로 원자적으로 교체한다.

첫 foundation에서는 다음 값의 런타임 representation을 준비한다.

- `max_retrieval_documents`
- `streaming_max_duration_seconds`
- `streaming_max_chunks`
- `streaming_max_bytes`

이 목록이 곧바로 public mutation API를 의미하지는 않는다. Configuration Plane의
persistent store, revision precondition, plan/apply/verification이 준비되어 실제 consumer가
provider에서 값을 읽는 것이 검증된 key만 `editable=true`로 공개한다.

이미 생성된 RuntimeClient, semaphore, HTTP pool 또는 circuit breaker를 다시 만들어야 하는
설정은 재구성 semantics 없이 hot reload라고 선언하지 않는다.

### Configuration metadata의 editable 의미

`editable=true`는 "사람이 이론적으로 바꿀 수 있다"가 아니라 다음을 뜻한다.

> 현재 Configuration Plane mutation contract가 값을 검증·저장하고, 선언된 apply mode로
> 실제 runtime에 적용하고, verification까지 수행할 수 있다.

다른 owner의 값도 Admin Console에서 제어할 수 있지만 Configuration PATCH로 바꾸지는 않는다.

- `owner=runtime` → Runtime/Main Model Control
- `owner=deployment` → Deployment workflow
- `owner=secret` → Secret rotation workflow
- `owner=repository` → release/source change
- `owner=operator` + `editable=true` → Configuration Plane

향후 metadata에는 UI가 올바른 control surface로 연결할 수 있도록 `control_surface`, form
constraint, applicability 정보를 추가한다.

### Operator state persistence

ADR-0021의 `${RUNTIME_STATE_DIR}/config/operator-overrides.yaml` 개념은 유지하되, 실제 구현은
모든 supported target에서 release/container 교체 후에도 살아남는 canonical platform state
root를 먼저 정의해야 한다.

현재 dynamic Compose는 `/var/lib/ai-model-serving`을 persistent host directory에 mount하지만,
static target은 동일한 persistent mount가 없다. 따라서 static target persistence를 보완하기
전에는 operator store를 target-neutral하다고 선언하지 않는다.

Operator store는 Main Model state와 같은 수준의 durability를 요구한다.

- process-safe locking
- temp file + fsync + atomic replace
- schema/revision validation
- corrupt state quarantine 또는 명시적 fail-closed recovery
- secret 원문 저장 금지

### Plan, Apply, Verify, History

Configuration mutation은 다음 순서를 따른다.

```text
Edit -> Validate -> Plan -> Review -> Apply -> Verify -> History
```

Apply는 revision/ETag precondition을 요구한다. stale revision은 last-writer-wins로 덮지
않고 conflict/precondition failure로 거절한다.

Rollback은 revision 번호를 과거로 되돌리지 않고, 과거 상태를 목표로 하는 새 mutation을
plan/apply하여 새 revision을 생성한다.

Operator override reset은 repository default 값을 복사해 저장하는 작업이 아니라 해당
operator layer 값을 제거하는 작업이다. Deployment environment가 더 높은 precedence로 값을
덮고 있으면 API/UI는 operator override가 shadowed되었음을 명시한다.

### Admin Console

Control Plane UI는 Gateway가 same-origin으로 제공하는 first-party self-hosted Console로 만든다.
외부 CDN, runtime Node server, SSR/React Server Components에 의존하지 않는다. Frontend build
artifact는 air-gap에서 독립적으로 서빙 가능해야 한다.

Console asset lifecycle은 API docs와 분리한다. `FASTAPI_DOCS_ENABLED=false`가 Scalar/ReDoc을
끄더라도 Admin Console을 함께 제거해서는 안 된다.

일반 운영자 UX는 ADR-0025의 Access Profile을 우선 표시한다.

```text
local | private | edge | legacy/custom
```

`AUTH_MODE`, `EXPOSURE_MODE` 등의 primitive는 advanced diagnostics로 취급한다.

Deployment Target feature가 없는 action은 frontend가 OS/hostname으로 추론하지 않고 server
capability를 기준으로 숨기거나 비활성화한다.

### Browser authentication

v1에서는 기존 Admin Bearer API contract를 유지한다. 별도 cookie session subsystem을 지금
추가하지 않는다.

- admin auth가 비활성화된 local profile에서는 same-origin Console을 바로 사용한다.
- admin auth가 필요한 profile에서는 operator가 입력한 Admin key를 브라우저 메모리에만
  보관하고 `Authorization: Bearer`로 보낸다.
- Admin key를 localStorage/sessionStorage에 영구 저장하지 않는다.

지속 로그인, human principal, RBAC가 실제 요구가 되면 OIDC 또는 HttpOnly session/BFF를
별도 ADR에서 설계한다.

### Console 정보 구조

초기 Console은 다음 surface를 하나의 application 안에 둔다.

- Overview
- Runtimes
- Main Model
- Configuration
- Operations
- History

Grafana는 time-series/troubleshooting, Scalar/ReDoc은 API reference 책임을 계속 유지한다.
Console이 두 도구를 다시 구현하지 않는다.

## Consequences

### Positive

- UI에 보이는 effective 값과 실제 요청 경로가 다른 drift를 구조적으로 막을 수 있다.
- Deployment identity와 operator tuning의 mutation 경계가 섞이지 않는다.
- `editable`이 실제 end-to-end apply capability를 의미하게 된다.
- static/dynamic target의 persistence 차이를 구현 전에 드러낸다.
- 기존 Bearer API를 유지해 Console 때문에 새로운 session/CSRF subsystem을 성급하게 만들지 않는다.
- Access Profile을 일반 운영자 UX의 Source of Truth로 유지한다.

### Negative

- operator store를 열기 전에 runtime consumer를 provider 경계로 옮기는 작업이 필요하다.
- RuntimeClient 자체의 재구성이 필요한 설정은 별도 apply semantics가 필요하다.
- frontend build toolchain과 dependency governance가 새로 생긴다.

## Implementation order

1. `RuntimeConfigurationSnapshot`/provider와 consumer boundary
2. Configuration metadata type/constraint/control-surface 확장
3. canonical persistent platform state root 및 static target persistence
4. operator override store + revision/history
5. config plan/apply/verification
6. runtime plan API
7. Control Plane bootstrap/capabilities API
8. self-hosted Admin Console

각 단계에서 `editable=true`는 해당 key의 persistence와 runtime apply가 함께 검증된 이후에만
활성화한다.

## Related

- ADR-0020: Runtime Control과 Deployment Target 분리
- ADR-0021: Configuration Plane과 Operator Override 경계
- ADR-0025: 사용자 접근 Profile과 기존 환경의 명시적 전환
- `configs/configuration_schema.yaml`
- `configs/access_profiles.yaml`
- `src/ai_model_serving/runtime_configuration.py`
- `src/ai_model_serving/services/runtime_state.py`
- `src/ai_model_serving/main_model/state.py`
