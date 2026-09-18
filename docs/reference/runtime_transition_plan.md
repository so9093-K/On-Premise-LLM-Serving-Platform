# Runtime Transition Plan

`POST /admin/runtimes/{service_key}/plans`는 런타임을 변경하지 않고 현재 GPU budget과 prerequisite 상태에서 transition 영향을 계산한다.

- `desired_state`: `active` 또는 `stopped`
- `force`: `active` 전환에서 GPU 예산 확보를 위해 낮은 우선순위 런타임을 자동 정지할지 여부. `stopped` 전환에서는 의미가 없으므로 canonical plan에서 `false`로 정규화한다.
- 응답은 `start`, `stop`, `prerequisites`, `impact`, `budget.before/after`, `admissible`, `requires_force`, `plan_digest`를 포함한다.

Plan과 Apply는 서로 다른 admission 구현을 갖지 않는다. Runtime Controller가 기존 `gpu_budget.plan_activation`을 사용해 plan을 만들고, `plan_digest`가 PATCH body에 있으면 실제 mutation 직전 GPU budget lock 안에서 다시 계산한다. digest가 달라지면 실행하지 않고 `409 CONFLICT`와 `reason=RUNTIME_PLAN_CHANGED`를 반환한다.

`plan_digest`는 기존 자동화 호환성을 위해 PATCH에서 선택 사항이다. first-party Admin Console은 항상 Plan을 먼저 요청하고 반환된 digest를 Apply에 전달한다. 이 digest는 클라이언트가 public projection을 다시 해시하기 위한 값이 아니라, sidecar가 검토 시점의 canonical transition snapshot을 apply-time 재계산과 비교하기 위한 opaque token이다.

## Apply verification과 operation history

Runtime mutation은 sidecar 호출 성공만으로 완료 처리하지 않는다. Gateway는 apply 이후 컨테이너 또는 Main Model의 실제 관측 상태를 다시 읽고 목표 상태와 prerequisite/eviction effect가 수렴했는지 확인한다. 수렴하지 않거나 관측 자체가 실패하면 `RUNTIME_VERIFICATION_FAILED`로 fail-closed하며 operation record는 `verification_failed`로 남는다. destructive mutation은 자동 재실행하지 않는다.

각 Apply는 `rt_<uuid>` operation id를 만들고 actor, request id, service key, desired state, force, 검토된 `plan_digest`, before snapshot, apply result, verification을 journal에 기록한다. 배포에서는 desired-state 파일과 같은 persistent platform state root 아래 `runtime-history/`를 사용하고 temp file + fsync + atomic replace로 terminal record를 갱신한다. source-tree/test 실행처럼 persistent root가 없는 경우 동일 API를 in-memory evidence로 제공하며 record의 `durable=false`로 구분한다.

- `GET /admin/runtimes/operations` — 최신 Runtime transition 이력, cursor pagination
- `GET /admin/runtimes/operations/{operation_id}` — 단일 operation evidence

process가 apply 도중 종료되어 `pending` record가 남으면 다음 Gateway composition에서 이를 성공으로 추정하지 않는다. 실제 apply 시점을 안전하게 재구성할 수 없으므로 `interrupted_after_restart` terminal evidence로 닫고, 운영자는 현재 `/admin/runtimes` 상태를 다시 확인한 뒤 새 Plan을 검토한다.
