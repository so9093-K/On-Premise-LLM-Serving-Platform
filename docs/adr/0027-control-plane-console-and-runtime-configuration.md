# ADR-0027: Control Plane Console과 Runtime Configuration 경계

## Status

Accepted

## Context

ADR-0020은 Deployment Target과 Runtime lifecycle control을 분리했고, ADR-0021은
Configuration Plane의 metadata/effective projection과 향후 operator override 경계를
정의했다. ADR-0025는 일반 사용자의 접근 의도를 `ACCESS_PROFILE=local|private|edge`로
단순화했다.

현재 Configuration Plane은 read-only다. 또한 `AppSettings`는 프로세스 시작 시 한 번
생성되는 frozen snapshot이며 GatewayService, RetrievalService, middleware와 runtime client가
이를 오래 보유한다. 이 상태에서 operator override 파일만 추가하면 UI/API의 effective 값과
실제 요청 경로가 서로 다른 값을 사용할 수 있다.

또한 모든 설정이 한 프로세스에만 속하지 않는다. 예를 들어 request-body limit과 readiness
probe timeout은 Gateway 외 서비스에도 적용될 수 있다. 한 프로세스만 hot reload하면
cross-process configuration drift가 된다.

Admin Console은 온프레미스/air-gap 환경에서도 외부 CDN이나 별도 UI server 없이 완전히
동작해야 한다. 기존 `/docs`와 `/redoc` asset lifecycle은 documentation profile에 따라 꺼질
수 있으므로 Console을 documentation UI에 종속시킬 수 없다.

## Decision

### 1. Startup configuration과 runtime configuration을 분리한다

`AppSettings`는 deployment/startup snapshot으로 유지하며 mutable object로 바꾸지 않는다.
요청 경로에서 안전하게 교체할 수 있는 운영 정책만 immutable
`RuntimeConfigurationSnapshot`에 투영하고, `RuntimeConfigurationProvider`가 현재 snapshot을
원자적으로 교체한다.

첫 경계에는 다음 값을 포함할 수 있다.

- Gateway request timeout
- retrieval document limit
- streaming duration/chunk/byte limit

단, schema/document/public contract에 정적 상한이 별도로 존재하는 값은 operator mutation을
열기 전에 그 계약과 effective runtime limit의 관계를 명시해야 한다. Provider에 값이
존재한다는 사실만으로 `editable=true`가 되는 것은 아니다.

request-body limit, readiness probe timeout처럼 여러 프로세스가 동일한 정책을 소비하는 값은
공유 persistence/reload protocol이 마련되기 전까지 첫 writable tranche에 포함하지 않는다.

### 2. `editable`은 실제 Configuration Plane write capability를 의미한다

`editable=true`는 현재 Configuration Plane이 validate, plan, persist, apply, verify 할 수 있는
`owner=operator` key에만 사용한다. 사람이 바꿀 수 있는 모든 값을 뜻하지 않는다.

다른 owner의 제어 가능한 값은 `control_surface` metadata로 연결한다.

- `configuration`: operator configuration mutation
- `runtime`: runtime desired-state control
- `main_model`: main-model profile switching
- `deployment`: deployment workflow
- `secret`: secret rotation workflow
- `repository`: release/code change

따라서 Console은 read-only 항목에 단순히 "수정 불가"만 표시하지 않고 실제 owner/control
surface를 설명하거나 해당 작업 화면으로 연결할 수 있다.

### 3. Access UX는 ADR-0025를 기준으로 한다

Console의 기본 security/access 표현은 raw `AUTH_MODE`, `EXPOSURE_MODE`가 아니라
`ACCESS_PROFILE=local|private|edge`와 resolved posture다. 기존 환경에 Access Profile이 없는
경우는 `legacy/custom`으로 표시하고 자동 이관하지 않는다.

Raw auth/exposure primitive는 Advanced/diagnostic 정보로 유지한다. Access Profile 변경은
Configuration Plane의 일반 key PATCH가 아니라 기존 plan/confirm lifecycle과 정합한 별도
control workflow로 다룬다.

### 4. Admin Console은 first-party same-origin SPA로 제공한다

Gateway가 `/admin/console/`과 content-hashed `/admin/console/assets/*`를 직접 서빙한다.
Frontend는 client-only React/TypeScript application으로 구성하고 production runtime에는
compiled HTML/JS/CSS만 포함한다.

- 외부 CDN, external font, external telemetry를 사용하지 않는다.
- runtime Node server, SSR, React Server Components, service worker/PWA를 사용하지 않는다.
- Console asset registration은 `/docs`/`/redoc`의 `documentation.enabled`와 분리한다.
- `index.html`은 재검증 가능하도록 cache를 짧게/비활성화하고 content-hashed assets는
  immutable cache를 사용한다.
- production Console bundle은 repository의 generated-artifact 정책에 맞춰 build drift를
  validation에서 확인한다.

초기 UI stack의 기본 선택은 React + TypeScript + Vite + PatternFly다. 정확한 patch version은
implementation 시 lockfile과 상호 호환성을 검증해 고정한다.

### 5. Browser auth는 기존 Admin Bearer 경계를 유지한다

v1에서는 별도의 cookie session subsystem을 만들지 않는다.

- `local` profile에서 admin auth가 비활성이면 Console은 같은 네트워크 경계를 따른다.
- admin Bearer가 필요한 profile에서는 사용자가 입력한 token을 JavaScript memory에만 유지한다.
- token을 localStorage/sessionStorage/IndexedDB에 저장하지 않는다.
- page reload 후에는 다시 인증한다.

Persistent human session, OIDC/SSO, principal별 AuthZ가 실제 요구될 때 별도 ADR로 설계한다.
Audit actor abstraction은 향후 principal을 수용할 수 있게 하되 raw admin token은 절대 기록하지
않는다.

### 6. Mutation은 Plan -> Apply -> Verify -> History 순서를 따른다

Configuration mutation은 key-scoped change set과 base revision/ETag를 사용한다.

1. Validate
2. Plan: before/after/effective source/impact 계산
3. Apply: stale revision이면 거절
4. Verify: 실제 runtime snapshot/effective state 확인
5. History: 새 revision으로 기록

Reset은 repository default 값을 복사해 저장하는 것이 아니라 operator override를 제거하는
operation이다. Rollback도 과거 revision 번호로 되돌리는 대신 과거 상태를 재적용하는 새로운
revision을 만든다.

Runtime start/stop도 destructive operation 전에 explicit plan endpoint를 제공해 GPU budget,
prerequisite, eviction, capability impact를 같은 UX로 보여준다.

### 7. Console navigation은 Deployment Target capability가 결정한다

Frontend가 OS, hostname, container name으로 capability를 추론하지 않는다. Browser-safe
bootstrap endpoint가 platform/release version, deployment target, feature set, Access Profile,
Configuration schema/revision을 제공하고 Console은 이를 기준으로 navigation/action을 연다.

Static target에서 runtime control/model switching이 false이면 해당 mutation UI는 노출하지 않는다.

## Consequences

### Positive

- UI에 보이는 effective value와 실제 request-path policy가 같은 runtime snapshot을 사용할 수 있다.
- immutable deployment settings와 operator policy의 책임을 섞지 않는다.
- owner가 다른 값도 하나의 Console에서 올바른 control surface로 연결할 수 있다.
- Console이 docs enablement나 외부 인터넷에 의존하지 않는다.
- 새로운 browser session/CSRF subsystem 없이 기존 Admin API security boundary를 재사용한다.

### Negative

- Runtime Configuration Provider와 operator persistence/revision/history가 추가된다.
- 여러 프로세스가 공유하는 설정은 별도 reload protocol 없이는 즉시 editable로 열 수 없다.
- frontend build toolchain과 npm supply-chain validation이 새로 필요하다.
- page reload마다 admin credential을 다시 입력하는 v1 UX 제약이 있다.

## Migration

1. Runtime Configuration snapshot/provider와 settings view를 추가한다.
2. Configuration metadata schema를 실제 operator form/owner/control-surface를 표현하도록 확장한다.
3. canonical persistent platform state root와 operator store를 추가한다.
4. Plan/Apply/revision/history API를 추가하고 첫 operator-owned keys를 연다.
5. Runtime plan/bootstrap API를 추가한다.
6. self-hosted Admin Console을 연결한다.
7. packaging, CI, CSP, air-gap/browser tests를 release gate에 포함한다.

## Related

- ADR-0020: Runtime Control과 Deployment Target 분리
- ADR-0021: Configuration Plane과 Operator Override 경계
- ADR-0025: 사용자 접근 Profile과 기존 환경의 명시적 전환
- `configs/configuration_schema.yaml`
- `configs/access_profiles.yaml`
- `src/ai_model_serving/runtime_configuration.py`
