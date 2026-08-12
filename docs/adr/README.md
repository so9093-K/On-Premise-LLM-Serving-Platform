# Architectural Decision Records

`이 디렉터리(`docs/adr/`)는 프로젝트의 **canonical decision record**다.

과거 결정의 맥락과 이유를 보존하고, 운영 정책 변경이 어떤 결정에 근거하는지 추적할 수 있도록 유지한다. 이 README는 탐색용 index와 legacy D-xxx mapping을 함께 제공한다.

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

이 디렉터리의 번호가 매겨진 Markdown 파일이 ADR 목록이자 canonical record다. 새 ADR을 추가하거나 Status를 바꾸면 해당 ADR 파일을 갱신한다.

## Legacy 결정 요약

ADR 체계 도입 이전 결정 중 현재 맥락을 설명하는 항목만 보존한다. 최종 허용·차단·리뷰 정책은 이 플랫폼의 Risk Adapter가 담당하지 않으며, 필요하면 별도 product policy layer에서 signal response를 해석한다.

| ID | 결정 | 이유 | Status |
|---|---|---|---|
| D-001 | 현재 플랫폼 구조를 canonical source로 두고 과거 원천 프로젝트 코드는 포함하지 않는다. | 플랫폼 목적을 과거 통합 과정이 아니라 모델 서빙 표준화로 정의 | Accepted |
| D-002 | Gateway `9400`을 외부 단일 진입점으로 둔다. | 애플리케이션 연동 단순화, 내부 runtime 교체 은닉 | Accepted (→ ADR-0004) |
| D-003 | Risk Adapter는 signal-only response만 반환한다. | 제품 정책 결정과 detector signal 분리 | Accepted (→ ADR-0002) |
| D-004 | vLLM runtime은 모델별 독립 process/port로 둔다. | 리소스 제어와 장애 격리 | Accepted (→ ADR-0003) |
| D-005 | FastAPI `/docs`, `/redoc`, `/openapi.json`은 기본 활성화한다. | 초기 운영/디버깅 사용성 확보 | Accepted |
| D-006 | Prometheus/Grafana/DCGM exporter는 compose/staging에서 기본 활성화한다. | 처음부터 관측 가능성 확보 | Accepted |
| D-007 | `make build`는 서비스 시작을 하지 않는다. | build와 runtime lifecycle 분리 | Accepted |
| D-008 | 대용량 model cache 삭제는 명시 opt-in으로 둔다. | 실수로 모델 cache 삭제 방지 | Accepted |
| D-009 | 문서 기본 언어는 한국어다. | 주 운영자가 한국어 사용자이므로 기본 문서를 한국어로 관리 | Accepted |
