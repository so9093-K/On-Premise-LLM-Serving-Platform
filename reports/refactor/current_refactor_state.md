---
document_type: current_snapshot
status: current
audience: operator, release_engineer
note: "이 문서는 특정 시점의 리팩터링 상태를 요약한 current_snapshot이다. 현재 package version은 VERSION 파일을 기준으로 한다."
---

# 현재 리팩터링 상태

## 현재 기준

현재 기준선은 streaming, Grafana 운영 UX, 문서 정합성, first-run/clean/package 재감사 상태다. `/v1/models` parameter discovery 위에 `stream=true` SSE relay, `stream_options.include_usage`, 6개 dashboard Grafana baseline, 그리고 package hygiene guard가 포함된다.

> **Historical note:** 이 문서를 작성할 당시의 내부 작업 tag는 `0.1.0-rc.1`이었으나, 현재 package version은 `VERSION` 파일을 기준으로 한다. config schema version(0.1.0)은 package version과 독립적으로 관리되며, platform/risk_vllm image tag 기본값은 package version과 정렬된다. 상세 내용은 `docs/release/versioning_policy.md`를 참조한다.

## 완료된 핵심 축

| 영역 | 상태 |
|---|---|
| Gateway/Risk Adapter | FastAPI app과 service layer 분리, checked-in JSON schema injection 유지 |
| 인증 제어 | `local_open`, `private_network`, `edge_terminated`, `strict`, `custom` profile과 status/doctor/plan/apply UX 정리 |
| OpenAPI | static spec, generated schema injection, error response surface, snapshot diff gate 유지 |
| 모델 관리 | `ModelRegistry`가 model list, runtime target, monitoring projection, contract projection, operator report를 파생 |
| 모델 parameter discovery | `/v1/models`가 `capabilities`, `request_parameters`, risk `fixed_parameters`를 반환 |
| Streaming API | `stream=true` SSE relay, `stream_options.include_usage`, streaming error event/metrics 정책 유지 |
| Monitoring/Grafana | 6개 dashboard baseline (`serving_cockpit`, `gpu_capacity_and_oom_risk`, `executive_runtime_overview`, `chat_api_deep_dive`, `model_runtime_deep_dive`, `risk_signal_operations`), Korean-first/English metric terms, common datasource/window/model/runtime/route/status variables, Serving Cockpit 전용 user_route, Git-managed provisioning |
| Risk vLLM patch | Dockerfile inline patch 제거, `ops/patches` script/metadata/label/verify/removal-check로 lifecycle 관리 |
| 문서 | 한국어 중심 단일 문서 흐름 유지, Day-0 가이드와 운영 문서 최신화 |
| 패키징 | `make package` 전 generated report 재생성, secret/cache/timestamped runtime report 제외 |

## `/v1/models` 현재 응답 정책

- `local-main`: chat/sampling/tool/streaming 관련 parameter를 `request_parameters`에 노출한다.
- `local-embed`: embedding dimension/truncation 관련 parameter를 `request_parameters`에 노출한다.
- `risk-prompt`, `risk-siren`: 사용자 조정 가능 parameter 없음. `request_parameters`는 `{}`이고 detector 내부 고정값은 `fixed_parameters`다.
- `stream_options`는 `stream=true`와 함께 사용할 때만 유효하다.
- serving/runtime 하이퍼파라미터는 사용자 API에서 조정하지 않는다.

## 변경 경계

Phase 31은 API path, 기존 request schema 의미, model id, compose service topology, Risk vLLM runtime/patch 동작, model add/remove write-mode를 바꾸지 않았다. `/v1/models` response metadata가 확장되었으므로, strict client는 새 field 허용 여부를 확인해야 한다.

## 검증 기준

```bash
python scripts/validation/validate_contracts.py
python scripts/validation/openapi_snapshot_diff.py
python scripts/validation/runtime_validation.py --config-only
python scripts/compose/validate_vllm_compose.py
python scripts/auth/auth_profile_sanity.py
python scripts/models/modelctl.py validate
python scripts/models/modelctl.py diff
python scripts/validation/run_tests.py -q
python scripts/validation/release_check.py --step-timeout-seconds 60
python -m compileall -q src scripts tests
bash -n scripts/*.sh scripts/lib/*.sh
```

## 대상 서버 후속 검증

Docker/GPU/vLLM 실측은 target host에서 수행한다.

```bash
make rebuild-risk-vllm
make risk-vllm-config-check
make risk-vllm-patch-removal-check
make compose-up
make ready-full
make runtime-validate
make operator-reports
make release-check-full
```
