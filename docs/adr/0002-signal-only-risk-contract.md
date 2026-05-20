# ADR-0002: Signal-only Risk Contract

## Status

Accepted

## Context

Risk Signal 계층은 최종 `allow`, `review`, `block` 결정을 하지 않는 독립 signal provider로 둔다.

## Decision

Risk Adapter와 detector endpoint는 위험 신호만 반환한다. `decision`, `action`, `allow`, `review`, `block`, `safe_to_send`, `final_decision` 필드는 기본 contract에서 제외한다.

## Consequences

| Positive | Negative |
|---|---|
| signal과 policy action의 책임이 분리됨 | Product policy는 별도 구현 필요 |
| 테스트가 명확함 | Gateway만으로 차단 정책을 제공하지 않음 |
| 정책 책임 분리 | 사용자 요구 시 별도 policy 문서 필요 |
