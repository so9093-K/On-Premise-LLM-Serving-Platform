# ADR-0031: Runtime Controller Docker authority boundary

- Status: Accepted
- Date: 2026-09-18
- Extends: [ADR-0030](./0030-target-architecture-state-and-artifact-boundary.md)

## Context

Linux/NVIDIA dynamic deployment의 Runtime Controller는 Docker Engine API를 통해
non-main Model Runtime의 start/stop, Main Model container 교체, observed state 확인,
log target projection을 수행한다.

이 권한은 Gateway와 분리되어 있고 Docker socket은 Runtime Controller에만 mount된다.
하지만 Unix socket bind mount를 `:ro`로 표시하는 것만으로 Docker Engine API가
read-only가 되는 것은 아니다. Controller가 손상되면 Docker daemon을 통한 host 영향이
커질 수 있으므로, 애플리케이션 코드가 행사할 수 있는 authority를 명시적으로 좁혀야 한다.

기존 lookup은 service label을 항상 사용했지만 `COMPOSE_PROJECT`가 비어 있으면
project label 없이 service-only 검색으로 넓어졌다. 같은 Docker host에서 여러 Compose
project가 동일한 service 이름을 사용할 수 있으므로 이는 의도한 deployment boundary보다
넓은 대상에 lifecycle operation을 적용할 가능성을 만든다.

## Decision

### 1. 모든 Docker container lookup은 Compose project scope를 요구한다

Runtime Controller와 Main Model Docker backend가 container를 찾을 때
`com.docker.compose.project=<COMPOSE_PROJECT>`를 반드시 포함한다.

service를 대상으로 하는 lookup은 다음 두 label을 함께 사용한다.

```text
com.docker.compose.project=<project>
com.docker.compose.service=<service>
```

`COMPOSE_PROJECT`가 비어 있으면 service-only fallback을 사용하지 않고 fail-closed한다.
실행 중 Controller의 초기화도 같은 invariant를 요구한다.

### 2. Docker daemon 응답도 scope를 다시 검증한다

request filter만 신뢰하지 않는다. `/containers/json` 결과의 label을 다시 확인해
요청한 project/service와 다르면 operation을 중단한다.

한 project/service에 둘 이상의 container가 발견되는 경우도 자동으로 하나를 고르지 않고
ambiguous state로 취급해 실패한다.

### 3. 외부 요청이 Docker object identity를 직접 지정하지 못하게 한다

public/Admin API 또는 Sidecar internal API 호출자는 다음 값을 직접 전달할 수 없다.

- Docker container ID / container name
- Compose project
- image reference
- command
- mount / network / device 설정

non-main Runtime lifecycle 대상은 `runtime_topology.yaml`의 controllable service allowlist에서,
Main Model image/command는 profile catalog에서 온다. Main Model container 교체 시 host/network
template은 project-scoped 기존 container에서 읽은 allowlisted field만 재사용한다.

### 4. Docker socket exposure를 Runtime Controller 하나로 제한한다

full-stack Compose에서 Docker socket consumer는 Runtime Controller 하나뿐이어야 한다.
Controller의 8080 port는 host publish하지 않고 Compose network 내부에만 expose한다.

`:ro` socket mount는 mount 경로 자체에 대한 filesystem 의미로만 취급하며
Docker API method authorization 경계로 간주하지 않는다.

### 5. 현재 hardening의 한계를 명시한다

이 결정은 Docker daemon 자체의 권한을 축소하는 sandbox가 아니다.
Controller process가 Docker socket에 접근할 수 있는 동안 해당 process는 높은 신뢰가 필요한
control-plane component다.

후속 hardening 후보는 별도 변경으로 평가한다.

- 최소 Docker API endpoint만 노출하는 socket proxy/agent
- Controller non-root 실행과 state directory ownership 정리
- image/container mutation API의 더 좁은 host-side policy
- control API와 Docker mutation path의 감사 로그
- host 단위 controller 격리 또는 rootless/remote daemon 구조

가짜 안전장치로 `:ro` 표기만 강화하거나, 실제 권한 모델을 바꾸지 않은 채
"read-only Docker access"라고 문서화하지 않는다.

## Validation

정적/단위 검증은 다음을 고정한다.

- project+service Docker filter
- project 누락 시 fail-closed
- cross-project/cross-service 응답 거부
- duplicate container 거부
- Docker socket이 Runtime Controller에만 mount됨
- Controller port가 host-published되지 않음
- Controller가 privileged/host-network/cap_add 없이 실행됨

## Consequences

- 동일 Docker host의 다른 Compose project를 service 이름만으로 잘못 제어할 수 없게 된다.
- 잘못된 `COMPOSE_PROJECT` 또는 label drift는 조용한 오동작 대신 명시적 failure로 나타난다.
- Runtime Controller는 여전히 privileged trust boundary이지만, 코드가 의도적으로 행사하는
  container authority는 현재 deployment project로 좁혀진다.
- 더 강한 host-level isolation은 이 ADR의 후속 단계이며 현재 API/운영 surface를 한 번에
  재설계하지 않는다.
