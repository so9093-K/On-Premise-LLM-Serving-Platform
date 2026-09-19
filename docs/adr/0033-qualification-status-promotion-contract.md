# ADR-0033: Qualification status promotion contract

- Status: Accepted
- Date: 2026-09-19
- Extends: [ADR-0032](./0032-qualification-evidence-v1.md)

## Context

ADR-0032는 Main Model의 `qualification.status=verified`가 현재 profile tuple과 정확히 일치하는
`passed` evidence로 뒷받침되어야 한다고 정했고, 이후 workflow는 live validation report에서
reviewable candidate를 만들고 사람이 검토한 candidate만 durable receipt/catalog evidence로
승격하도록 분리되었다.

여기서 durable evidence 생성과 profile status 변경을 같은 자동 동작으로 취급하면 다음 문제가
생긴다.

- candidate 생성 또는 receipt 저장만으로 운영상 `verified` 의미가 바뀔 수 있다.
- evidence가 현재 profile과 일치하더라도 사람이 검토하지 않은 상태 변경이 일어날 수 있다.
- profile identity/revision/capability가 evidence 검토 뒤 바뀐 경우 stale evidence로 승격할 수 있다.
- 기존 `legacy_backfill`의 compatibility 의미와 새 `qualified_run`의 promotion 의미가 섞일 수 있다.

## Decision

### 1. Durable evidence와 profile status 변경은 별도 commit boundary다

`qualification/produce_candidate.py`와 `qualification/promote_candidate.py`는 profile의
`qualification.status`를 변경하지 않는다. Durable `qualified_run` receipt/catalog가 repository에
존재하는 것만으로 profile을 자동 `verified`로 바꾸지 않는다.

`unverified -> verified` 변경은 별도 reviewable PR에서 명시적으로 수행한다. 그 PR은 현재
repository state를 기준으로 eligibility를 다시 검증해야 한다.

### 2. 새 verified promotion은 current-contract evidence만 사용한다

새로운 `unverified -> verified` 승격의 positive evidence는 다음 조건을 모두 만족하는
`qualified_run`이어야 한다.

- `result: passed`
- 현재 profile의 `profile_id + model_id + revision + deployed_input capabilities`와 정확히 일치
- 현재 deployment target을 참조
- repository-owned durable receipt가 존재하고 catalog record와 `source`를 제외하고 일치
- runtime engine/version, distribution image digest, GPU/driver fingerprint가 완전함
- capability registry가 요구하는 stable check ID가 모두 존재하고 모두 `passed`

`legacy_backfill`은 기존 verified 상태의 역사적 근거를 정직하게 보존하기 위한 compatibility
record다. 새 status promotion의 근거로 사용하지 않는다.

### 3. Promotion은 fail-closed하고 history를 추론하지 않는다

현재 profile/evidence tuple이 일치하지 않거나 required field/check가 누락되거나 ambiguous하면
승격을 거부한다. 과거 ADR, changelog, 주석, 이전 profile revision에서 값을 추론해 eligibility를
보충하지 않는다.

Evidence가 여러 개면 적어도 하나의 완전한 current `qualified_run`이 위 조건을 만족해야 한다.
Failed/skipped run은 history/debugging 자료로 남길 수 있지만 positive eligibility가 아니다.

### 4. Demotion과 evidence retention은 분리한다

현재 verified profile의 model ID, revision 또는 deployed capability가 바뀌어 current evidence와
불일치하면 기존 ADR-0032 validator가 fail-closed한다. 변경자는 같은 PR에서 새 current evidence를
제공하거나 profile을 `unverified`로 명시적으로 낮춰야 한다.

Demotion은 과거 durable evidence를 삭제하거나 rewrite하는 exit condition이 아니다. 이전 receipt와
catalog record는 history로 보존한다.

### 5. Public/Admin compatibility는 유지한다

이 결정은 기존 `qualification.status` enum과 switch confirmation 계약을 바꾸지 않는다.
`unverified` profile은 기존과 같이 명시적 confirmation이 필요한 전환 가능 profile일 수 있고,
`compatibility.status`는 계속 기술적 전환 가능성만 소유한다. Qualification evidence 전체를 public
API에 새로 노출하지 않는다.

## CI Gate

향후 status-promotion 구현은 최소 다음 current-contract invariant를 검증해야 한다.

- durable current `qualified_run` 없이는 `verified` promotion을 거부
- `legacy_backfill`만으로 새 promotion을 거부
- profile identity/revision/capability drift를 거부
- failed/skipped/missing required check를 거부
- receipt/catalog drift를 거부
- eligibility 확인과 profile mutation 사이의 repository state drift를 거부하거나 원자적으로 검증

특정 과거 commit, PR 번호, migration sequence를 성공 조건으로 고정하지 않는다.

## Consequences

- Evidence capture와 운영 status mutation의 review 책임이 분명해진다.
- 새 qualification automation이 생겨도 검증 성공만으로 profile이 자동 verified되지 않는다.
- 기존 verified + legacy evidence compatibility는 ADR-0032의 현재 validator 계약 아래 유지된다.
- 후속 구현은 이 ADR의 eligibility를 reusable validator/command로 만들고, 별도 PR에서 명시적 status
  mutation을 연결할 수 있다.

## Non-goals

- 기존 verified profile의 일괄 재검증 또는 강등
- legacy evidence 삭제/재작성
- GPU qualification 자동 실행
- public API schema 변경
- candidate 생성과 status mutation의 단일 자동 transaction화
