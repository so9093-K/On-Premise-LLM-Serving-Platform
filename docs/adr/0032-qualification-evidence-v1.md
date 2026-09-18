# ADR-0032: Qualification evidence v1

- Status: Accepted
- Date: 2026-09-18
- Extends: [ADR-0030](./0030-target-architecture-state-and-artifact-boundary.md)

## Context

ADR-0030은 Main Model의 기술적 Compatibility와 실제 검증 수준인 Qualification을 분리하고,
`verified`를 장기적으로 model/revision/runtime image/hardware/capability/검증 시각과 연결된
evidence로 확장하기로 결정했다.

현재 repository에는 실제 GPU 검증 기록이 존재하지만 대부분
`configs/main_model_profiles.yaml`의 주석과 description, ADR, 운영 기록에 사람이 읽는 형태로
남아 있다. 이 상태에서는 다음 문제가 있다.

- profile의 model revision이나 capability가 바뀌어도 이전 `verified` 근거가 현재 tuple과
  같은 것인지 자동으로 확인할 수 없다.
- 과거 검증에서 기록하지 않은 driver/image digest를 나중에 추측해 채우면 evidence가 오염된다.
- 앞으로 수행하는 qualification run과 과거 기록의 품질 차이를 machine-readable하게 구분할
  방법이 없다.
- `verified`가 단순 enum 값으로만 남으면 CI가 “현재 profile에 실제 근거가 있는가?”를 검증할
  수 없다.

## Decision

### 1. Qualification evidence는 별도 catalog가 소유한다

Main Model qualification evidence의 repository Source of Truth는
`configs/qualification_evidence.yaml`이다.

profile config는 model identity, runtime policy와 `qualification.status`를 계속 소유하고,
evidence catalog는 해당 상태를 뒷받침하는 검증 기록을 소유한다.

API 응답에 evidence catalog 전체를 투영하지 않는다. evidence는 운영·검증 governance 자료이며
현재 public/Admin API compatibility와 분리한다.

### 2. evidence record는 현재 profile identity와 연결된다

각 record는 최소 다음을 가진다.

- record kind
- Main Model profile ID
- model ID와 immutable revision
- deployment target
- 검증한 capability 집합
- 검증한 check/API feature 집합
- result
- source path와 설명

현재 `qualification.status=verified`인 profile은 최소 하나의 `passed` record가 현재
`profile_id + model_id + revision + deployed_input capabilities`와 정확히 일치해야 한다.

profile ID가 같더라도 revision 또는 capability가 바뀌면 이전 evidence는 history로 남을 수 있지만
새 현재 상태의 qualification 근거로 인정하지 않는다.

### 3. 기존 검증 기록은 `legacy_backfill`로 정직하게 이관한다

v1 도입 전에 수행된 검증은 `legacy_backfill`로 기록한다.

과거 기록에 driver version, resolved image digest, 정확한 검증 시각이 없다면 값을 추측해 채우지
않는다. 대신 실제 출처와 현재 확인 가능한 field만 구조화한다.

이 정책은 기존 `verified` 상태를 임의로 강등하지 않으면서도, 어떤 fingerprint 정보가 과거에
빠졌는지를 명시적으로 드러낸다.

### 4. 새 검증은 `qualified_run` 완전 record를 사용한다

v1 도입 이후 새로 `verified` 승격의 근거로 만드는 record는 `qualified_run`을 사용한다.

`qualified_run`은 최소 다음 정보를 필수로 가진다.

- `validated_at`
- runtime engine
- runtime engine version
- resolved runtime image digest (`sha256:<64 hex>`)
- GPU
- driver version
- 검증한 named checks
- deployment target
- profile/model/revision
- 검증한 capability 집합
- result

추가 관측값(context, concurrency, KV cache, latency 등)은 `observations`에 확장할 수 있다.

### 5. failed run도 evidence로 보존할 수 있다

evidence catalog는 `passed`와 `failed`를 모두 보존할 수 있다.

failed record는 debugging/history에는 유효하지만 현재 `verified` qualification의 근거로 사용하지
않는다.

### 6. CI governance가 evidence 정합성을 fail-closed한다

repository validation은 다음을 강제한다.

- evidence catalog version과 구조
- `qualified_run`과 현재 verified 근거가 현재 deployment target을 참조하는지
- source path가 repository에 존재하는지
- `qualified_run`의 완전한 runtime/hardware fingerprint
- 현재 verified profile에 current tuple과 맞는 passed evidence가 존재하는지

Main Model profile의 model ID, revision 또는 deployed capability를 변경하고 evidence를 갱신하지
않으면 validation이 실패한다.

## Scope

v1은 Linux/NVIDIA Main Model Profile evidence부터 시작한다.

Deployment Target 자체의 qualification evidence, macOS/MLX의 별도 evidence catalog,
benchmark result artifact retention, CI에서 자동 evidence 생성/서명은 후속 범위다.

## Consequences

- `verified`가 사람이 읽는 주석만이 아니라 machine-checkable evidence와 연결된다.
- profile revision/capability 변경 시 stale qualification을 CI가 잡을 수 있다.
- 과거 기록의 불완전성을 숨기지 않고 `legacy_backfill`로 명시할 수 있으며, 삭제된 target을 가리키는 historical record도 보존할 수 있다.
- 향후 GPU qualification automation이 생기면 `qualified_run` 형식으로 결과를 추가할 수 있다.
- evidence는 source/config와 함께 Git에 저장하지만 Docker image나 대형 benchmark artifact 자체를
  Git history에 넣는다는 의미는 아니다.

## Non-goals

- 기존 verified profile을 일괄 재검증
- 누락된 과거 driver/image digest를 추정
- CI에서 GPU qualification 자동 실행
- benchmark raw log/large artifact 저장
- public API에 qualification evidence 전체 노출
