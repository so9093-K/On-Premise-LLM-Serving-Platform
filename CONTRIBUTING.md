# Contributing

이 저장소의 변경 기록과 테스트는 구현 과정이 아니라 **변경 뒤 시스템에 남는 계약**을 설명한다.

## Working notes and review notes

작업 중 발견, 가설, 대안, 시행착오, 임시 순서는 작업 메모에 남긴다. 작업 메모는 구현을 진행하고 판단하기 위한 단기 기록이며 Git history의 계약이 아니다.

검토 메모는 reviewer가 선택과 tradeoff를 판단하는 데 필요한 설명이다. 삭제 후보, 대안 비교, 아직 확인 중인 위험은 검토 메모에서 자유롭게 다루되, 최종 PR과 Commit에는 resulting state만 남긴다.

완료되지 않은 후속 작업과 미해결 결정은 Issue 또는 Roadmap에서 추적한다. PR 본문과 Commit message를 future work의 보관소로 사용하지 않는다.

## Commit

Commit 제목은 변경 행위보다 **변경 뒤 시스템에 남은 결과**를 설명한다.

본문에는 이 변경을 이해하는 데 필요한 경우 다음과 같은 지속적인 의미를 기록한다.

- behavior 또는 operator-visible effect
- ownership 또는 Source of Truth의 변화
- failure semantics
- 장기간 보호해야 할 invariant

파일 목록, 구현 순서, 일시적인 branch 상태, merge 순서 같은 작업 과정은 기본적으로 기록하지 않는다. `제거했다`·`추가했다`의 나열보다 누가 무엇을 소유하고 어떤 상태가 유효한지를 우선 설명한다.

변경하지 않은 영역을 습관적으로 나열하지 않는다. 검토에 필요한 boundary가 있을 때는 `X는 바꾸지 않는다`보다 `Y가 X의 authority를 유지한다`처럼 지속되는 계약을 긍정형으로 설명한다.

## Pull Request

PR은 하나의 변경 단위가 해결하는 **현재 문제**, 변경 뒤의 **resulting contract**, 그 계약을 직접 확인하는 **meaningful verification**을 설명한다.

기본 구조는 다음 세 섹션이다.

1. `## 문제` — 현재 상태에서 실제로 존재하는 문제와 그 영향
2. `## 변경 뒤 계약` — merge 뒤 시스템에서 참이 되는 ownership, behavior, failure semantics, invariant
3. `## 검증` — 위 계약을 직접 증명하는 검증

Operator-visible migration, 별도 architecture decision처럼 reviewer가 반드시 알아야 하는 내용만 필요할 때 추가 섹션으로 둔다.

Diff가 이미 보여 주는 파일별 변경 목록, 작업 중 시행착오, 다음 PR 계획, merge 순서, 일반적인 CI pass/fail 목록은 PR 본문의 기본 내용이 아니다. 일반 CI 결과의 source of truth는 GitHub Checks다.

## Durable records

장기간 유지되어야 하는 architecture 결정과 tradeoff는 ADR에 기록한다. 사용자·운영자가 체감하는 변경과 필요한 migration은 CHANGELOG에 기록한다. 아직 구현되지 않은 후속 작업과 미해결 결정은 Issue 또는 Roadmap에서 추적한다.

각 기록은 자신의 시간축만 소유한다.

- Commit — 해당 변경 뒤 남은 코드 계약
- PR — 변경 단위의 문제, resulting contract, review evidence
- ADR — 장기 architecture decision과 tradeoff
- CHANGELOG — release 관점의 operator/user-visible delta와 migration
- Issue/Roadmap — future work와 unresolved decision
- GitHub Checks — CI 실행 결과

현재 architecture 문서에는 완료된 migration 순서나 과거 PR 번호보다 현재의 authority와 operational contract를 우선 남긴다.

## Regression tests

테스트는 구현 구조보다 지속되어야 할 behavior와 safety invariant를 보호한다. 가능한 가장 가까운 계층에서 검증하고, 내부 구현이 바뀌어도 같은 계약을 보호하는 동안 의미가 유지되도록 한다.

테스트를 추가하거나 유지할 때는 해당 테스트가 없으면 어떤 production regression이 통과할 수 있는지 설명할 수 있어야 한다. Schema, type checker, canonical validator가 이미 같은 사실을 충분히 보장한다면 중복 검증을 늘리지 않는다.

실제 runtime 또는 GPU 동작 자체가 계약인 경우에만 해당 검증 범위까지 확장한다.
