# 02. 의사결정 기록

## 1. 원칙

- 현재 플랫폼의 목적은 과거 프로젝트 이전이 아니라 모델 서빙 표준화다.
- 운영자가 읽는 설명 문서는 한국어를 기본으로 한다.
- API path, env key, JSON/YAML field, 명령어, 제품명은 영어 원문을 유지한다.
- Risk Adapter는 signal-only 계층이며 정책 결정을 하지 않는다.

## 2. 결정 목록

| ID | 결정 | 이유 | 상태 |
|---|---|---|---|
| D-001 | 현재 플랫폼 구조를 canonical source로 두고 과거 원천 프로젝트 코드는 포함하지 않는다. | 플랫폼 목적을 과거 통합 과정이 아니라 모델 서빙 표준화로 정의해야 함 | Accepted |
| D-002 | Gateway `9400`을 외부 단일 진입점으로 둔다. | 애플리케이션 연동을 단순화하고 내부 runtime 교체를 숨김 | Accepted |
| D-003 | Risk Adapter는 signal-only response만 반환한다. | 제품 정책 결정과 detector signal을 분리 | Accepted |
| D-004 | vLLM runtime은 모델별 독립 process/port로 둔다. | 리소스 제어와 장애 격리를 쉽게 함 | Accepted |
| D-005 | FastAPI `/docs`, `/redoc`, `/openapi.json`은 기본 활성화한다. | 초기 운영/디버깅 사용성을 막지 않음 | Accepted |
| D-006 | Prometheus/Grafana/DCGM exporter는 compose/staging/production-like 검증에서 기본 활성화한다. | 처음부터 관측 가능성을 확보 | Accepted |
| D-007 | `make build`는 서비스 시작을 하지 않는다. | build와 runtime lifecycle을 분리 | Accepted |
| D-008 | 대용량 model cache 삭제는 명시 opt-in으로 둔다. | 실수로 모델 cache를 지우지 않게 함 | Accepted |
| D-009 | 문서 기본 언어는 한국어다. | 주 운영자가 한국어 사용자이므로 별도 KO 문서가 아니라 기본 문서를 한국어로 관리 | Accepted |

## 3. Product Policy Layer 처리

최종 허용/차단/리뷰 정책은 이 패키지의 Risk Adapter가 담당하지 않는다. 필요하면 별도 product policy layer에서 signal response를 해석한다.
