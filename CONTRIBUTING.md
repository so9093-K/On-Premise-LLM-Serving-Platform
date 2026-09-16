# Contributing

이 저장소의 변경 기록과 테스트는 구현 과정이 아니라 **최종 시스템의 책임, 불변식, 운영 의미**를 남기는 것을 우선한다.

## 기록 계층

같은 내용을 여러 곳에 복제하지 않고, 기록마다 소유하는 시간 범위와 목적을 분리한다.

- **Work notes** — 조사 중 가설, 대안, 실패한 시도, 불확실성, TODO처럼 작업이 끝나면 영구 기록일 필요가 없는 내용을 담는다.
- **Review notes** — merge 전에 확인해야 할 위험, 질문, 검증되지 않은 우려와 피드백을 담는다.
- **결과 보고** — 무엇을 조사·검증했고 현재 상태가 무엇인지, 다음 후보 작업이 무엇인지 사용자에게 전달한다.
- **Commit** — 변경 뒤 시스템에 남은 ownership, Source of Truth, 실패 의미, operator-visible behavior, 회귀 invariant를 기록한다.
- **Pull Request** — 한 변경 단위의 배경, 변경 경계, 설계 결정과 특별한 검증을 기록한다.
- **ADR** — 현재 architecture를 이해하는 데 장기간 필요한 결정, tradeoff, 대안과 migration 의미를 기록한다.
- **CHANGELOG** — 사용자·운영자가 체감하는 변경과 필요한 migration을 기록한다.
- **Issue / Roadmap** — 아직 구현되지 않은 후속 작업과 미해결 결정을 추적한다.

작업 과정의 메모나 리뷰 피드백을 commit/ADR에 옮겨 적지 않고, 영구 기록은 최종 상태를 이해하는 데 필요한 정보만 남긴다.

## 표현 원칙

영구 기록은 가능하면 **무엇이 아닌가**보다 **현재 무엇이 authority이고 각 구성요소가 무엇을 소유하는가**를 설명한다.

예를 들어 `Compose를 제거하는 것은 아니다`보다 `runtime topology가 prerequisite policy를 소유하고 Compose는 deployment projection을 표현한다`가 더 오래 의미가 남는다. `새 SoT를 만들지 않는다`보다 `기존 X가 authority이고 새 surface는 projection으로 구성한다`처럼 결과 상태를 기록한다.

금지 자체가 security/safety contract인 경우에는 부정형을 명확하게 유지한다. 예를 들어 secret을 노출하면 안 되는 경계, unsupported mutation을 거부하는 경계, force-push 차단처럼 **하지 않는 것이 계약 자체인 경우**가 이에 해당한다.

## Commit

Commit 제목은 변경 뒤 시스템에 남은 결과를 설명한다.

본문에는 다음처럼 시간이 지나도 의미가 남는 내용을 적는다.

- 책임 또는 ownership 경계의 변화
- Source of Truth의 통합·분리
- fail-open / fail-closed 등 실패 의미의 변화
- public/operator-visible behavior의 변화
- 회귀를 막기 위해 새로 고정한 중요한 invariant

단순 파일 목록, 작업 중 메모, 리뷰 도구의 코멘트, AI와의 대화 과정, 일시적인 테스트 실행 결과는 commit message의 목적이 아니다.

## Pull Request

PR 본문은 변경 단위의 의사결정 기록으로 사용한다. 필요한 경우 다음 항목을 사용한다.

- **배경** — 왜 이 변경이 필요한가
- **범위** — 무엇이 변경되는가
- **설계 결정** — 어떤 책임·계약 경계를 선택했는가
- **변경 경계** — 이번 변경 뒤에도 어떤 ownership과 contract가 다른 구성요소에 남는가
- **검증** — 일반 CI 결과가 아닌, 변경 의미를 설명하는 특별한 검증이 있는가

`의도적으로 제외` 목록을 기계적으로 만들기보다, 살아 있는 ownership을 설명해야 할 때는 `변경 경계` 또는 `계약 경계`를 사용한다. 금지나 비지원 자체가 중요한 계약인 경우에는 그 제한을 직접 기록한다.

사용자에게 전달하는 리뷰 결과, 다음 작업 제안, 작업 중 판단 메모는 PR의 영구 기록과 분리한다. 일반적인 CI pass/fail은 GitHub Checks가 소유하므로 PR 본문에 반복해서 복사하지 않는다.

## Regression tests

테스트는 구현 구조보다 durable behavior와 safety invariant를 보호한다. 새 테스트를 추가하기 전에 다음 질문에 답할 수 있어야 한다.

1. 어떤 실패 모드 또는 invariant를 보호하는가?
2. 왜 이 계층에서 검증해야 하는가?
3. 구현 방법이 바뀌어도 의미가 남는가?
4. 어떤 계약이 없어지면 이 테스트도 안전하게 삭제할 수 있는가?

UI layout, component 개수, 내부 hook 호출 방식, 광범위한 snapshot처럼 구현 세부에 강하게 결합된 테스트는 명확한 회귀 근거가 없으면 추가하지 않는다.

프로젝트의 기존 원칙대로 변경으로 발생할 수 있는 문제를 가능한 가장 가까운 계층에서 검증하고, 실제 runtime/GPU 검증이 필요한 경우에만 검증 범위를 확장한다.
