# 02. 의사결정 기록 (ADR Index)

이 파일은 `docs/adr/` 디렉터리의 Architectural Decision Records(ADR) index와 legacy D-xxx mapping이다.  
각 ADR은 `docs/adr/` 디렉터리의 파일이 source-of-truth이며, 이 문서는 canonical record가 아니라 탐색용 index다.

---

## 운영 원칙

- 현재 플랫폼의 목적은 과거 프로젝트 이전이 아니라 모델 서빙 표준화다.
- 운영자가 읽는 설명 문서는 한국어를 기본으로 한다.
- API path, env key, JSON/YAML field, 명령어, 제품명은 영어 원문을 유지한다.
- Risk Adapter는 signal-only 계층이며 정책 결정을 하지 않는다.

---

## ADR 목록

| ID | 파일 | 제목 | Status |
|---|---|---|---|
| ADR-0002 | [docs/adr/0002-signal-only-risk-contract.md](adr/0002-signal-only-risk-contract.md) | Signal-only Risk Contract | Accepted |
| ADR-0003 | [docs/adr/0003-all-vllm-runtime.md](adr/0003-all-vllm-runtime.md) | All Major Models as vLLM Runtime | Superseded by ADR-0010 |
| ADR-0004 | [docs/adr/0004-port-policy-9400.md](adr/0004-port-policy-9400.md) | 외부 진입 포트 9400 정책 | Accepted |
| ADR-0010 | [docs/adr/0010-colbert-removal-dense-korean-retrieval.md](adr/0010-colbert-removal-dense-korean-retrieval.md) | ColBERT 제거와 Dense Korean Retrieval 전환 | Accepted |
| ADR-0011 | [docs/adr/0011-documentation-source-of-truth-policy.md](adr/0011-documentation-source-of-truth-policy.md) | 문서 Source-of-Truth와 Generated Block 정책 | Accepted |
| ADR-0012 | [docs/adr/0012-auth-ownership-and-compose-exposure-source-of-truth.md](adr/0012-auth-ownership-and-compose-exposure-source-of-truth.md) | Auth 소유권과 Compose Exposure Profile Source-of-Truth 분리 | Accepted |
| ADR-0013 | [docs/adr/0013-env-lifecycle-non-destructive-sync.md](adr/0013-env-lifecycle-non-destructive-sync.md) | .env 비파괴 동기화 정책 | Accepted |
| ADR-0014 | [docs/adr/0014-image-validation-policy.md](adr/0014-image-validation-policy.md) | Vision 이미지 검증 정책 — 한도 상향과 MIME type 독립 파서 탐지 | Accepted |
| ADR-0015 | [docs/adr/0015-main-llm-20k-o3-runtime-target.md](adr/0015-main-llm-20k-o3-runtime-target.md) | Main LLM 20K O3 Runtime Target | Accepted |
| ADR-0016 | [docs/adr/0016-xgrammar-disable-any-whitespace.md](adr/0016-xgrammar-disable-any-whitespace.md) | xgrammar disable-any-whitespace Structured Output Backend | Accepted |
| ADR-0017 | [docs/adr/0017-selectable-main-model-runtime.md](adr/0017-selectable-main-model-runtime.md) | Selectable Gemma 4 Main-model Runtime | Accepted |
| ADR-0018 | [docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md](adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md) | 통합 GPU VRAM Admission과 Per-profile 런타임 이미지 | Accepted |
| ADR-0019 | [docs/adr/0019-audio-video-real-processing-ceiling-vs-spec.md](adr/0019-audio-video-real-processing-ceiling-vs-spec.md) | 오디오/비디오 실제 처리 한계와 공식 스펙 간 격차 | Accepted |

---

## Legacy 결정 요약 (D-001~D-009)

ADR 체계 도입 이전 결정이다. 일부는 ADR로 승격되었고, 나머지는 legacy decision으로 이 index에 보존한다.

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

---

## Product Policy Layer 처리

최종 허용/차단/리뷰 정책은 이 패키지의 Risk Adapter가 담당하지 않는다. 필요하면 별도 product policy layer에서 signal response를 해석한다.
