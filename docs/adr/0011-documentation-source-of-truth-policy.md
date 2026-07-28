# ADR-0011: 문서 Source-of-Truth와 Generated Block 정책

## Status

Accepted

## Context

프로젝트가 성장하면서 운영 문서가 실제 설정, 모델 목록, API 경로, risk code와 어긋나는 사례가 발생했다. 구체적으로:

- 문서가 taxonomy에 없는 risk code(A3–A5, I5)를 예시로 나열했다.
- Retired Siren endpoint가 active detector처럼 문서화됐다.
- `/v1/embeddings`가 `local-embed` 전용이라고 잘못 설명됐다.
- 존재하지 않는 embedding-ko 전용 Dockerfile과 make target이 문서에 남았다.
- 모델 목록이 `model_cards/*.json`과 일치하지 않는 문서가 있었다.

근본 원인: 문서가 source-of-truth를 참조하는 대신 직접 사실을 정의하려 했다.

## Decision

문서는 사실을 새로 정의하지 않는다. 각 정보 유형별 source-of-truth를 아래와 같이 확정하고, 문서는 해당 source-of-truth를 참조하거나 반영하는 역할만 한다.

| 정보 유형 | Source-of-Truth |
|---|---|
| 모델 목록 | `configs/model_catalog.yaml`, `model_cards/*.json` |
| 모델 runtime 구성 | `configs/model_serving.yaml` |
| risk code | `configs/risk_taxonomy.yaml` |
| API endpoint | `src/ai_model_serving/api/endpoint_spec.py`, `specs/openapi.gateway.yaml` |
| request/response schema | `specs/schemas/*.json` |
| monitoring target | `configs/monitoring.yaml` |
| compose service | `ops/compose/*.yaml` |
| build target | `Makefile`, `scripts/` |
| architectural decision | `docs/adr/*.md` |

추가 원칙:

- examples는 API contract sample이다. risk code는 `configs/risk_taxonomy.yaml`에 존재하는 code만 사용한다.
- retired endpoint는 active 검증 예시로 작성하지 않는다.
- Tier 1 reference 문서는 generated block 또는 governance test로 source-of-truth와의 일치를 보호한다.
- `docs/adr/`는 canonical decision record다. `docs/02_decision_register.md`는 ADR index로 운영한다.

## Consequences

| Positive | Negative |
|---|---|
| 문서 드리프트를 governance test로 조기 탐지 | 문서 작성자가 source-of-truth를 먼저 확인해야 함 |
| source-of-truth 변경 시 문서 업데이트가 명확해짐 | generated block 도입 시 generator 유지 비용 발생 |
| examples가 contract와 함께 검증됨 | — |

## Operational impact

- `tests/contract/test_document_structure.py`는 문서 홈, manifest, archive/current 분리 같은 구조 정책을 검증한다.
- `tests/contract/test_document_source_of_truth.py`는 source-of-truth와 문서의 정합성을 검증한다.
- `tests/contract/test_document_stale_markers.py`는 재발하면 위험한 retired marker만 제한적으로 검증한다.
- Tier 1 문서(`endpoint_reference.md`, `model_cards.md`, `model_parameter_discovery.md`)의 generated block 전환은 migration plan으로 별도 추적한다.

## Migration notes

이 ADR 채택과 함께 다음 수정을 완료했다.

- `docs/examples/requests.md`에서 A3/A4/A5/I5 제거, Siren active 예시를 retired 안내로 교체
- `docs/models/model_cards.md`에 `local-embed-ko` 추가, `risk-siren` retained 거짓 문장 제거
- `docs/operations/endpoint_reference.md`에서 `/v1/embeddings` local-embed 전용 주장 제거
- `docs/development/build_ux.md`에서 존재하지 않는 embedding-ko 전용 build 참조 제거
- `docs/operations/gitlab_cicd_deployment.md`에서 존재하지 않는 embedding-ko 전용 Dockerfile 참조 제거

## Related

- ADR-0010: ColBERT 제거와 Dense Korean Retrieval 전환
- `docs/governance/document_management.md`
- `tests/contract/test_document_structure.py`
- `tests/contract/test_document_source_of_truth.py`
- `tests/contract/test_document_stale_markers.py`
- `configs/risk_taxonomy.yaml`
