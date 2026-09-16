# Contributing

이 저장소의 변경 기록과 테스트는 구현 과정이 아니라 **변경 뒤 시스템에 남는 계약**을 설명한다.

## Commit

Commit 제목은 변경 뒤 시스템에 남은 결과를 설명한다.

본문에는 이 변경을 이해하는 데 필요한 경우 다음과 같은 지속적인 의미를 기록한다.

- behavior 또는 operator-visible effect
- ownership 또는 Source of Truth의 변화
- failure semantics
- 장기간 보호해야 할 invariant

## Pull Request

PR은 하나의 변경 단위가 해결하는 문제, 변경 뒤의 contract, 검토에 필요한 의미 있는 검증을 설명한다.

일반적인 CI pass/fail의 source는 GitHub Checks다.

## Durable records

장기간 유지되어야 하는 architecture 결정과 tradeoff는 ADR에 기록한다. 사용자·운영자가 체감하는 변경과 필요한 migration은 CHANGELOG에 기록하고, 아직 구현되지 않은 후속 작업과 미해결 결정은 Issue 또는 Roadmap에서 추적한다.

## Regression tests

테스트는 구현 구조보다 지속되어야 할 behavior와 safety invariant를 보호한다. 가능한 가장 가까운 계층에서 검증하고, 내부 구현이 바뀌어도 같은 계약을 보호하는 동안 의미가 유지되도록 한다.

실제 runtime 또는 GPU 동작 자체가 계약인 경우에만 해당 검증 범위까지 확장한다.
