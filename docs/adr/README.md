# Architectural Decision Records

`docs/adr/`는 프로젝트의 **canonical decision record**다.

과거 결정의 맥락과 이유를 보존하고, 운영 정책 변경이 어떤 결정에 근거하는지 추적할 수 있도록 유지한다. `docs/02_decision_register.md`는 이 디렉터리의 index와 legacy D-xxx mapping이다.

---

## ADR Status 정책

| Status | 의미 |
|---|---|
| `Proposed` | 검토 중. 아직 확정되지 않음 |
| `Accepted` | 채택됨. 현재 플랫폼 운영 기준 |
| `Superseded by ADR-XXXX` | 다른 ADR로 대체됨. 원본 기록은 보존 |
| `Deprecated` | 더 이상 권장하지 않지만 제거하지 않음 |
| `Rejected` | 검토 후 채택하지 않기로 결정 |

---

## ADR Template

```markdown
# ADR-XXXX: 제목

## Status

[Proposed | Accepted | Superseded by ADR-XXXX | Deprecated | Rejected]

## Context

결정이 필요했던 배경과 제약 조건을 기술한다.

## Decision

무엇을 결정했는지 명확하게 기술한다.

## Consequences

| Positive | Negative |
|---|---|
| 긍정적 결과 | 부정적 결과 또는 트레이드오프 |

## Operational impact

운영 절차, 설정, 도구에 미치는 영향을 기술한다.

## Migration notes

기존 시스템/코드에서 이 결정으로 전환할 때 필요한 작업을 기술한다.

## Related

- 연관 ADR, 문서, 정책
```

---

## ADR 인덱스

| ID | 제목 | Status |
|---|---|---|
| ADR-0002 | Signal-only Risk Contract | Accepted |
| ADR-0003 | All Major Models as vLLM Runtime | Superseded by ADR-0010 |
| ADR-0004 | 외부 진입 포트 9400 정책 | Accepted |
| ADR-0010 | ColBERT 제거와 Dense Korean Retrieval 전환 | Accepted |
| ADR-0011 | 문서 Source-of-Truth와 Generated Block 정책 | Accepted |
| [ADR-0012](0012-auth-ownership-and-compose-exposure-source-of-truth.md) | Auth 소유권과 Compose Exposure Profile Source-of-Truth 분리 | Accepted |
| [ADR-0013](0013-env-lifecycle-non-destructive-sync.md) | .env 비파괴 동기화 정책 | Accepted |
| [ADR-0014](0014-image-validation-policy.md) | Vision 이미지 검증 정책 — 한도 상향과 MIME type 독립 파서 탐지 | Accepted |
| [ADR-0015](0015-main-llm-20k-o3-runtime-target.md) | Main LLM 20K O3 Runtime Target | Accepted |

전체 결정의 canonical record는 이 `docs/adr/` 디렉터리의 파일이며, `docs/02_decision_register.md`는 ADR index와 legacy D-xxx 매핑을 제공한다.
