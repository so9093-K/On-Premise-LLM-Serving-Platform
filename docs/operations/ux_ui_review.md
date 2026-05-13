---
document_type: source
status: historical_review
audience: operator, developer
note: "이 문서는 UX 개선 이력을 기록한 리뷰 문서다. 각 섹션 제목의 괄호 표기는 해당 개선이 이루어진 내부 작업 단계를 나타내며, 현재 package version과 무관하다. 현재 운영 기준 5개 dashboard 목록은 docs/operations/monitoring_ux.md를 참조한다."
---

# 운영 UX/UI 검토

## 좋은 점

- `make validate`, `make test`, `make start`, `make ready`, `make package` 흐름이 명확하다.
- Prometheus, Grafana, DCGM exporter, cAdvisor가 compose reference에서 기본으로 올라온다.
- `make clean-all`은 model cache 삭제에 `PURGE_MODEL_CACHE=1`을 요구해 실수 삭제를 막는다.
- 한국어 운영자 기준 API 문서 UX: 태그 영어, 설명 한국어, Scalar UI 적용.

## 개선한 항목

### API 문서 (0.1.0-rc.1)
- `/docs`를 FastAPI 기본 Swagger UI에서 **Scalar UI**로 교체. CDN 기반으로 추가 패키지 없음.
- API 태그를 영어로 통일 (`Operations`, `Monitoring`, `Models`, `Chat`, `Embeddings`, `Risk`).
- `GATEWAY_DESCRIPTION` 첫 줄의 불필요한 서비스 소개 문구 제거.
- 예시 요청을 한국어 콘텐츠 기반으로 교체, `dimensions: 768` 같은 미지원 파라미터 제거.

### 환경 초기화 (0.1.0-rc.1)
- `make init-env-*`가 기존 `.env`를 기본적으로 덮어쓰지 않도록 변경.
- 강제 재생성은 `make init-env-*-force`로 분리.
- `GRAFANA_ADMIN_PASSWORD`를 `GENERATED_SECRET_KEYS`에서 제외 — bootstrap 재실행 시 Grafana 비밀번호가 변경되던 버그 수정.
- `.env`에 `SECRETS_GENERATED_AT` 타임스탬프 기록으로 마지막 갱신 시각 추적 가능.

### Bootstrap 자동화 (0.1.0-rc.1)
- `make bootstrap` 완료 후 실행 중인 스택의 `gateway`/`risk-adapter`를 자동 재시작 (갱신된 토큰 즉시 반영).
- Infisical 설정 시 토큰 갱신 후 자동 push.

### 시크릿 관리 (0.1.0-rc.1)
- Infisical 자체 호스팅 스택 추가 (`ops/compose/infisical.yaml`).
- `make infisical-up/down/init`, `make secrets-push/pull/status` 타겟 추가.
- `.env.compose.example`의 Infisical 키와 `scripts/config/infisical_sync.py` 동기화 스크립트 추가. 별도 Infisical 전용 env 파일은 유지하지 않고, `make init-env-compose`가 생성하는 `.env`를 단일 설정 파일로 사용한다.

### 모니터링 UX (0.1.0-rc.1)
- 초기 Grafana 3개 대시보드에 한국어 설명, `timezone: browser`, 대시보드 간 네비게이션 링크를 추가했고, 이후 운영 기준은 5개 dashboard와 variable-backed Git-managed JSON으로 확장했다.
- Grafana 로그인 페이지 기본 대시보드 자동 이동 설정.
- Prometheus scrape 타겟 및 recording rule 추가.

### Runtime 수정 (0.1.0-rc.1)
- `LlamaConfig.validate_architecture` 패치: Kanana risk 모델의 `head_dim` 명시 시 validation 오류 수정.
- vLLM이 반환하는 `tool_calls: []`를 gateway validation에서 정상 처리.
- smoke test: `RISK_ADAPTER_BASE_URL`이 compose 내부 hostname이어도 항상 localhost로 오버라이드.

## 남은 검증

Docker/GPU/vLLM이 있는 host에서 full-stack compose와 Grafana real-data rendering을 확인해야 한다.

## Phase 15 UX review update

이번 UX 점검의 결론은 기능 부족보다 **진입점 과다와 명령 선택 비용**이 더 큰 문제라는 것이다. 각 명령은 역할이 분명하지만, 처음 온 운영자는 `make help`의 긴 목록만 보고 현재 상황에 맞는 최소 경로를 고르기 어렵다.

개선 사항:

- `make guide` 추가: local, full-stack, reports, release, cleanup 상황별 명령 추천을 출력한다.
- `docs/operations/operator_workflows.md` 추가: `make help`보다 짧은 scenario-first 가이드.
- `docs/operations/configuration_lifecycle.md` 추가: 설정 원천, 환경 profile, 빌드/기동 분리, 삭제 범위를 한 문서에 통합.
- `make operator-reports` 추가: `runtime-targets`, `monitoring-projection`, `operator-status`, `live-evidence`를 한 번에 실행한다.
- `make remove-plan` 추가: `make clean-dry-run`의 읽기 쉬운 alias로 삭제 UX를 명확히 한다.
- `docs/development/build_ux.md`의 bootstrap 설명 중 risk vLLM 기본 빌드 설명을 바로잡았다. 기본 `make bootstrap`은 risk vLLM 이미지를 항상 재빌드하고, 반복 개발 최적화는 `SKIP_RISK_VLLM_IMAGE_BUILD=auto make rebuild-full`을 사용한다.

남은 UX 개선 후보:

- `make help`를 compact/full 모드로 나누는 방안.
- Grafana dashboard variable/template은 현재 governance에서 강하게 검증한다. 남은 후보는 registry projection에서 JSON을 자동 생성하는 방안이다.
- `ModelRegistry` projection 파일을 더 작게 분리해 개발자 탐색성을 높이는 방안.

## FastAPI Docs UX 개선 (docs-ux)

이번 작업의 목표는 `/docs` 첫 화면을 짧고 명확하게 만들고, 고급 정책은 endpoint description과 별도 docs page로 분리하는 것이다.

개선 사항:

- `GATEWAY_DESCRIPTION_TEMPLATE` 압축: Quick Start, Auth guide, 모델별 파라미터 안내, Readiness 설명으로 요약. 상세 정책(json_schema subset, logit_bias, capability_gate 등)은 `docs/specs/api.md`와 operation description으로 이동.
- `/v1/chat/completions` operation description 재정리: 핵심 동작과 참고 문서 링크 위주.
- Chat OpenAPI examples 정리: 기존 `with_system_prompt` 예시는 유지하고 `json_object` 예시를 추가했다 (총 12개). json_schema+tools 같은 고급 조합은 docs page로.
- Embedding OpenAPI examples 보강: basic 외에 `with_dimensions`, `truncate_prompt_tokens` 추가 (3개). `encoding_format: base64`는 미지원이므로 제외.
- Gateway Risk OpenAPI examples 보강: prompt_injection, prompt_leak, indirect_injection, tool_abuse 추가.
- `PLAYGROUND_PARAMS` 제거: 미사용 상수 제거. `/playground` 구현 방향은 TODO 주석과 `docs/specs/fastapi_docs_ux.md` 3.4절에 기록.
- 주요 route에 `operation_id` 명시 (`getGatewayHealth`, `createChatCompletion`, `listModels` 등). SDK/client generation UX 개선 및 route 이름 변경에 무관한 안정적 method name 확보.
- `docs/specs/fastapi_docs_ux.md`에 parameter grouping 권장 (3.3절), `/playground` 설계 TODO (3.4절), docs asset CDN/self-host 운영 정책 (4절) 추가.
- `docs/specs/api.md`에 operation_id 목록과 runtime validation 정책 섹션 추가.
- `docs/operations/endpoint_reference.md` endpoint 표에 operation_id 컬럼 추가.
- `docs/operations/model_parameter_discovery.md`에 UI parameter grouping 권장 섹션 추가.

FastAPI docs는 model-aware playground가 아니라 API reference다. 모델별 form UI는 `/v1/models` 기반으로 구성한다. live vLLM runtime validation은 현재 merge gate가 아니다.

## Chat UX / API docs update

이번 점검의 핵심 발견은 Chat request schema와 `/v1/models` parameter discovery가 같은 사용자 입력 표면을 바라봐야 한다는 점이다. `n`은 OpenAI-compatible client 호환을 위해 request schema에 존재하지만, 런타임 정책의 `supported_parameters`에 빠져 있으면 Gateway가 `n: 1` 요청도 422로 거부한다.

개선 사항:

- `local-main.request_parameters`에 `n: {min: 1, max: 1}`을 노출한다.
- Chat UI는 `n`을 일반 슬라이더로 노출하지 않고 숨기거나 읽기 전용 `1`로 표시한다.
- `parallel_tool_calls`는 현재 `false` 고정이다. UI는 toggle을 비활성화하거나 표시하지 않는다.
- `stream_options.include_usage`는 `stream=true`일 때만 활성화한다.
- Governance validation이 `configs/model_serving.yaml`의 chat `supported_parameters`와 `specs/schemas/chat_completion_request.schema.json`의 optional request field 목록을 비교한다.

남은 live-only 확인:

- Gateway 재기동 후 `/v1/chat/completions`에서 `n: 1`은 통과하고 `n: 2`는 422인지 확인한다.
- running stack 기준 `/docs`, `/openapi.json`, `/v1/models`가 모두 같은 chat parameter surface를 보여주는지 확인한다.
