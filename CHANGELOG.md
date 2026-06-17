# 변경 이력

이 파일은 사용자와 운영자에게 의미 있는 버전별 릴리스 노트만 기록한다. 긴 내부 유지보수 기록은 `docs/archive/changelog/`에 보존한다.

## [Unreleased]

### Added

- 한국 전화번호(휴대폰 `01x-`, 서울 `02-`, 지역 `0[3-9]x-`)를 PII Protection detector가 D2 신호로 감지한다. 기존에 Presidio English recognizer가 한국 번호 포맷을 안정적으로 인식하지 못해 누락되던 케이스다.
- Anthropic API 키(`sk-ant-...`) 패턴을 Secret Exposure detector가 D4 신호로 감지한다. 이전에는 고엔트로피 generic candidate로만 잡혔다.
- `make sync-env` — `git pull` 이후 `.env`를 템플릿과 동기화한다. 누락 키를 추가하고 폐기 키를 제거하되, 기존 크리덴셜·이미지 태그·커스텀 값은 모두 보존한다. 시크릿을 재생성하지 않는다.
- `setup_env.py --env-file <path>` — `--sync-env` 실행 시 프로젝트 루트가 아닌 다른 경로의 `.env`를 대상으로 지정할 수 있다. 별도 배포 디렉터리의 `.env` 동기화에 사용한다.
- 문서 lifecycle, ownership, source-of-truth, 검증 방식을 추적하는 `docs/manifest.yaml`을 추가했다.

### Changed

- Main LLM runtime target을 `gpu_memory_utilization=0.76`, `max_model_len=20000`, `max_num_batched_tokens=20000`, `optimization_level=3`로 정렬했다. ModelRegistry projection, compose validation, model card, catalog, docs, tests가 같은 runtime policy를 검증한다. FP8 Dynamic checkpoint와 `kv_cache_dtype=fp8_e5m2` 조합은 현재 runtime image에서 boot 단계에서 거부되어 active target에서 제외했다. ([ADR-0015](docs/adr/0015-main-llm-20k-o3-runtime-target.md))
- Vision 이미지 한도를 Gemma 4 SigLIP2 아키텍처 기준으로 상향했다: `max_image_bytes` 750,000 → 7,000,000, `max_image_pixels` 1,048,576 → 6,422,528 (8타일 × 896²), `max_request_body_bytes` 1,250,000 → 10,000,000. 한도 source-of-truth는 `configs/gpu_budgets.yaml`이며 contract 테스트가 cross-config 일치를 동적으로 검증한다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- Vision 이미지 포맷 파서 선택을 MIME type 기반에서 magic bytes sequential detection으로 변경했다. MIME type allowlist(`image/jpeg`, `image/png`, `image/webp`) 검사는 유지되지만, 파서는 MIME type 선언과 무관하게 실제 바이트로 포맷을 판단한다. MIME type을 잘못 선언한 클라이언트의 불필요한 422가 제거된다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- 공통 error code 계약에 `DETECTOR_DISABLED`, `STREAM_LIMIT_EXCEEDED`를 반영하고, Gateway가 Risk Adapter의 `DETECTOR_DISABLED` 410 envelope를 보존하도록 했다.
- retrieval 내부 embedding 호출이 `truncate_prompt_tokens`를 전달하도록 정리했다. 확인되지 않은 `truncation_side`는 silent no-op 대신 422 validation error로 처리한다.
- non-local `local_open`/`custom`/`internal_trusted` auth profile과 production `SKIP_PREFLIGHT=1` 경로의 운영 hard-fail 조건을 강화했다.
- 운영 배포 동작 변경 없이 retrieval contract의 project root 탐색 의존을 runtime settings에서 분리하고, 계층 import boundary를 AST 계약 테스트로 고정했다.
- `bootstrap.sh`(`make rebuild-full`)이 `EXPOSURE_MODE`와 `EXPOSURE_AUDIENCE`를 기존 `.env`에서 읽어 재초기화 후 복원한다. 이전에는 `AUTH_MODE`만 보존되고 `EXPOSURE_MODE`는 초기화됐다.
- `deploy_gitlab_compose.sh` CI/CD 배포 시 `.env` 이미지 참조 업데이트 직후 `make sync-env`를 호출해 신규 템플릿 키를 서버 `.env`에 자동 반영한다.
- Grafana 운영 대시보드 UX를 Serving Home 중심 drill-down으로 정리했다. API/Risk/Runtime 상세 패널은 collapsed row로 내리고, idle 상태의 실패류 패널은 scrape가 살아 있으면 0으로 읽히도록 보정했다. Risk는 A1/A2 detection을 명시 카드로 분리하고 중복 Risk Types 상세 그래프를 제거했으며, Dashboard contract는 `configs/monitoring.yaml`에서 선언해 validator가 검증한다.
- Model Runtime Deep Dive와 API Delay Details에 평균 응답 시간 패널을 추가했다. 평균은 histogram `_sum/_count` 기반으로 `$window`를 따르며, 기존 p95 패널은 tail latency 확인용으로 유지한다.
- GPU Capacity and OOM Risk, Serving Home, Model Runtime Deep Dive의 token throughput 단위를 `tok/s`로 고치고, container CPU는 percentage가 아니라 `vLLM container CPU cores used`로 표시한다.
- Safe access log에 `client_ip_hash`, `forwarded_for_present`, `forwarded_proto`를 추가했다. Prometheus metric label에는 raw client IP를 넣지 않고, IP 기반 abuse 분석은 log correlation으로 수행한다.
- ADR canonical 위치를 `docs/adr/`로 통합하고 root `adr/`는 더 이상 사용하지 않는다.
- 설명형 request examples 문서를 `docs/examples/requests.md`로 이동했다.
- `reports/refactor/current_*`에는 실제 current state, handoff, inventory만 남기고 과거 audit snapshot은 archive로 분리했다.

### Fixed

- `json_schema` structured output 요청에서 whitespace가 `max_tokens`까지 반복 생성되던 버그를 수정했다. xgrammar의 `any_whitespace` 기능이 중첩 배열 닫는 `]` 이후 `}` 전이를 막아 stuck state에 진입하던 문제로, vLLM 공식 확인 버그다(PR #12744, #15316). non-stream 요청은 502 `UPSTREAM_SCHEMA_ERROR`, stream 요청은 200이지만 invalid JSON으로 나타났다. backend를 `xgrammar:disable-any-whitespace`로 변경해 해결했다. ([ADR-0016](docs/adr/0016-xgrammar-disable-any-whitespace.md))
- Main LLM `max_output_tokens`를 4096 → 8192로 상향했다. 복잡한 JSON Schema를 사용하는 structured output 요청이 `finish_reason: length`로 잘려 `UPSTREAM_SCHEMA_ERROR` 502를 유발하던 문제다. configs, model card, OpenAPI spec, JSON Schema, test 6개 파일에 분산된 하드코딩을 일괄 반영했다.
- `make validate` 중 OpenAPI contract 검증이 `ADMIN_API_KEY_REQUIRED` 미설정 환경에서 admin endpoint의 401 응답을 누락 감지하던 문제를 수정했다. validator가 strict auth env를 임시 적용해 spec을 생성한 후 복원한다.

## [0.0.1] - 2026-05-20

### Added

- Gateway 중심의 chat, embedding, retrieval, risk signal API 계약과 운영 문서 기준선을 제공한다.
- 모델 catalog, model cards, runtime config, OpenAPI/JSON Schema, monitoring projection 검증 흐름을 포함한다.
- Docker/GPU full-stack 운영을 위한 compose, Prometheus, Grafana, runtime validation report 생성 흐름을 제공한다.

### Changed

- CHANGELOG는 짧은 release history로 유지하고, 기존 `0.1.0-rc.1` 내부 maintenance 기록은 `docs/archive/changelog/maintenance_journal_0.1.0-rc.1.md`로 이동했다.

### Security

- 인증/인가 동작은 이 문서 재구조화에서 변경하지 않았다.
