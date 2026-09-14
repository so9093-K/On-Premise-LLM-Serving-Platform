# Runtime Transition Plan

`POST /admin/runtimes/{service_key}/plans`는 런타임을 변경하지 않고 현재 GPU budget과 prerequisite 상태에서 transition 영향을 계산한다.

- `desired_state`: `active` 또는 `stopped`
- `force`: `active` 전환에서 GPU 예산 확보를 위해 낮은 우선순위 런타임을 자동 정지할지 여부. `stopped` 전환에서는 의미가 없으므로 canonical plan에서 `false`로 정규화한다.
- 응답은 `start`, `stop`, `prerequisites`, `impact`, `budget.before/after`, `admissible`, `requires_force`, `plan_digest`를 포함한다.

Plan과 Apply는 서로 다른 admission 구현을 갖지 않는다. Admin Sidecar가 기존 `gpu_budget.plan_activation`을 사용해 plan을 만들고, `plan_digest`가 PATCH body에 있으면 실제 mutation 직전 GPU budget lock 안에서 다시 계산한다. digest가 달라지면 실행하지 않고 `409 CONFLICT`와 `reason=RUNTIME_PLAN_CHANGED`를 반환한다.

`plan_digest`는 기존 자동화 호환성을 위해 PATCH에서 선택 사항이다. first-party Admin Console은 항상 Plan을 먼저 요청하고 반환된 digest를 Apply에 전달한다. 이 digest는 클라이언트가 public projection을 다시 해시하기 위한 값이 아니라, sidecar가 검토 시점의 canonical transition snapshot을 apply-time 재계산과 비교하기 위한 opaque token이다.
