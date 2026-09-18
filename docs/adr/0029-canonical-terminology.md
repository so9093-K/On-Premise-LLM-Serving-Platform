# ADR-0029: Canonical terminology와 stable identifier 분리

- Status: Superseded by [ADR-0030](./0030-target-architecture-state-and-artifact-boundary.md)
- Date: 2026-09-18

## Supersession note

ADR-0030은 이 ADR의 사용자-facing terminology 개선은 유지하지만, 기존 namespace를 장기적으로
영구 stable canonical로 두는 해석과 compatibility/qualification 상태 결합은 supersede한다.
기존 식별자는 별도 migration 전까지 호환 계약으로 유지한다.

## Context

프로젝트가 inference API에서 Control Plane, runtime switching, configuration transaction,
observability까지 확장되면서 초기 구현 이름이 현재 역할보다 좁거나 내부 구현을 과도하게
노출하는 경우가 생겼다.

대표적으로 `admin-sidecar`는 실제로 독립된 runtime control service인데 사용자 문서에서는
Sidecar라는 배포 패턴 이름으로 읽히고, `Secondary Runtime`은 embedding과 risk detector의 역할을
설명하지 못한다. `Risk Adapter` 역시 최종 정책 결정을 하지 않고 risk signal만 정규화한다는
계약이 이름에 드러나지 않는다. Console의 `Plan`, `Force`, `Admissible`, `operation evidence`
같은 표현은 API 내부에서는 유용하지만 운영자 UX에서는 실제 효과를 이해하기 어렵다.

반대로 기존 API path, Compose service ID, environment key는 이미 자동화와 배포에서 참조될 수 있어
표시 용어를 개선한다는 이유만으로 한 번에 바꾸면 안 된다.

## Decision

1. 표준 용어의 Source of Truth를 `docs/reference/terminology.md`로 둔다.
2. 사용자-facing 용어는 역할 중심으로 정렬한다.
   - Admin Sidecar → **Runtime Controller**
   - Risk Adapter → **Risk Signal Service**
   - Secondary Runtime → **Model Runtime** 또는 구체 역할명
   - Deploy Runtime Profile → **Runtime Startup Profile**
   - Operations 화면 → **Activity**
3. `local-main`은 model 자체가 아니라 **Public Model Alias**로 설명한다.
4. Main Model profile의 기존 `compatibility.status`는 안정 API field로 유지하되 Console에서는
   **Qualification**로 표현한다. `likely`는 신규 상태명으로 확대하지 않고 향후 상태 모델 분리 시
   제거 대상으로 취급한다.
5. Deployment Target ID(`linux-nvidia-dynamic` 등)는 안정 식별자로 유지하되 display name은
   managed/external lifecycle 의미가 드러나도록 한다.
6. 내부 `operation`, `desired_state`, `plan` 같은 계약 이름은 API에서 유지할 수 있다. Console은
   실제 사용자 행동과 영향에 맞춰 별도 표시 문구를 사용한다.
7. 영문 제품명은 **On-Premises LLM Serving Platform**을 canonical로 한다.
8. 기존 env/service ID를 바꿀 때는 alias → migration → validation → legacy removal 순서를 따른다.

## Consequences

- 문서와 Console에서 구현 세부보다 역할과 운영 효과가 먼저 보인다.
- Compose service와 API field를 즉시 rename하지 않으므로 기존 자동화 호환성을 유지한다.
- 새 기능과 AI-assisted 변경은 terminology 문서를 기준으로 이름을 선택해야 한다.
- stale identifier는 별도 migration 없이 단순 검색/치환으로 제거하지 않는다.
