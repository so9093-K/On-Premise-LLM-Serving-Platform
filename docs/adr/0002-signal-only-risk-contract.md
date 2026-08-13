# ADR-0002: Signal-only Risk Contract

## Status

Accepted

## Context

Risk Signal 계층은 최종 `allow`, `review`, `block` 결정을 하지 않는 독립 signal provider로 둔다.

Sensitive Data Protection(PII Protection + Secret Exposure Signal)이 추가되면서 data_exposure family(D1-D5)와 로컬 detector가 contract에 편입된다.

## Decision

Risk Adapter와 detector endpoint는 위험 신호만 반환한다. `decision`, `action`, `allow`, `review`, `block`, `safe_to_send`, `final_decision` 필드는 기본 contract에서 제외한다.

**Detector 유형:**

| Detector | 유형 | Risk Codes |
|---|---|---|
| PII Protection (`pii`) | local (regex) | D1, D2, D5 |
| Secret Exposure (`secret`) | local (regex + entropy) | D4, D5 |
| Prompt (`risk-prompt`) | vLLM | A1, A2 |

**Aggregate 실행 순서:** `pii → secret → prompt`

**Data exposure category 특성:**
- `label`은 entity type 이름(예: `KR_RRN`, `EMAIL_ADDRESS`). `<UNSAFE-Dx>` 형식이 아님.
- `span_count`: 탐지된 entity 개수(null 또는 0 이상의 integer).
- 원문 PII/Secret 값은 응답, 로그, metric에 포함되지 않음.
- D4(Secret/Credential)는 가장 강한 signal로 `strongest_code` 우선순위 1위.

## Consequences

| Positive | Negative |
|---|---|
| signal과 policy action의 책임이 분리됨 | Product policy는 별도 구현 필요 |
| 테스트가 명확함 | Gateway만으로 차단 정책을 제공하지 않음 |
| 정책 책임 분리 | 사용자 요구 시 별도 policy 문서 필요 |
| PII/Secret 탐지가 같은 aggregate contract 안에서 동작 | PII는 정규식으로 표현 가능한 신호만 탐지 |
| 원문 민감값이 응답/로그에 남지 않음 | — |
