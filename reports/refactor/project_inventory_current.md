# 프로젝트 인벤토리와 파일 검토 — Current

이 리포트는 현재 source tree에서 생성되며 로컬 runtime/cache/build 디렉터리는 제외한다.

- 검토 파일 수: **285**
- CSV matrix: `reports/refactor/project_inventory_current.csv`
- JSON matrix: `reports/refactor/project_inventory_current.json`

## 영역별 파일 수

| 영역 | 수 |
|---|---:|
| `.dockerignore` | 1 |
| `.env` | 1 |
| `.env.compose.example` | 1 |
| `.env.example` | 1 |
| `.env.local.example` | 1 |
| `.gitignore` | 1 |
| `.gitlab-ci.yml` | 1 |
| `.python-version` | 1 |
| `CHANGELOG.md` | 1 |
| `Dockerfile` | 1 |
| `Makefile` | 1 |
| `README.md` | 1 |
| `VERSION` | 1 |
| `adr` | 3 |
| `assets` | 1 |
| `configs` | 12 |
| `contracts` | 2 |
| `docs` | 49 |
| `examples` | 1 |
| `harness` | 4 |
| `model_cards` | 3 |
| `ops` | 15 |
| `pyproject.toml` | 1 |
| `reports` | 20 |
| `requirements.lock` | 1 |
| `requirements.runtime.lock` | 1 |
| `scripts` | 55 |
| `specs` | 11 |
| `src` | 67 |
| `tests` | 25 |
| `version_manifest.json` | 1 |

## 주요 진입점

- `README.md`
- `docs/README.md`
- `docs/operations/configuration_lifecycle.md`
- `docs/operations/day0_quickstart.md`
- `docs/operations/operator_workflows.md`
- `docs/operations/project_management_ux.md`
- `docs/operations/storage_paths.md`
- `docs/release/release_checklist.md`
- `reports/refactor/current_refactor_state.md`

## 줄 수 기준 큰 파일

| 경로 | 줄 수 | 담당 | 검토 메모 |
|---|---:|---|---|
| `specs/openapi.gateway.yaml` | 3624 | api-contracts | supporting project file |
| `reports/refactor/project_inventory_current.json` | 3394 | handoff-reporting | handoff/review artifact; avoid stale phase snapshots in active package |
| `specs/openapi.risk-adapter.yaml` | 1630 | api-contracts | supporting project file |
| `tests/unit/test_gateway_app.py` | 927 | quality | test coverage; keep deterministic and avoid live GPU dependency |
| `ops/grafana/dashboards/gpu_capacity_and_oom_risk.json` | 761 | operations | supporting project file |
| `ops/grafana/dashboards/chat_api_deep_dive.json` | 739 | operations | supporting project file |
| `ops/grafana/dashboards/executive_runtime_overview.json` | 606 | operations | supporting project file |
| `src/ai_model_serving/governance_validation/docs_ops.py` | 604 | governance-validation | application code; preserve public API behavior and compatibility facades |
| `ops/grafana/dashboards/risk_signal_operations.json` | 585 | operations | supporting project file |
| `src/ai_model_serving/domain/model_registry.py` | 577 | domain-registry | registry/domain code; monitor size as projections grow |
| `reports/runtime/operator_status_bundle.json` | 564 | handoff-reporting | generated operator report; regenerate through make operator-reports before handoff |
| `ops/grafana/dashboards/model_runtime_deep_dive.json` | 555 | operations | supporting project file |
| `scripts/models/modelctl.py` | 551 | operator-ux | operator/developer command; expose through Makefile/help if user-facing |
| `src/ai_model_serving/apps/gateway.py` | 475 | application | application code; preserve public API behavior and compatibility facades |
| `README.md` | 422 | project-maintenance | primary navigation or current-state entrypoint; keep synchronized with Makefile and reports |

## 관리 해석

- 사용자-facing 명령은 `Makefile`, `make help`, `docs/operations/operator_workflows.md`에 노출되어야 한다.
- 설정 원천 파일은 `docs/operations/configuration_lifecycle.md`에 반영하고 governance validation으로 보호해야 한다.
- 생성 runtime report는 `make operator-reports`로 갱신하고, timestamp가 붙은 live runtime validation evidence는 릴리스 패키지에 포함하지 않는다.

이 inventory는 파일 목록 자체보다 관리 경계 확인이 목적이다. `src/`는 application/control-plane code, `configs/`는 설정 source of truth, `docs/`는 운영자가 읽는 문서, `reports/runtime/`은 재생성 가능한 운영 evidence, `reports/refactor/`는 current handoff를 담는다. 새 파일을 추가할 때는 이 경계에 맞는 위치인지 먼저 확인한다.

오래된 phase snapshot이나 날짜가 붙은 current-state 파일은 active handoff에 다시 넣지 않는다. 현재 상태는 stable path인 `reports/refactor/current_refactor_state.md`, `reports/refactor/current_handoff_summary.md`, `reports/refactor/project_inventory_current.md`만 기준으로 본다.

유지보수 관점에서는 큰 파일이 늘어날 때 책임 경계를 먼저 본다. 운영자가 직접 실행하는 스크립트는 Makefile에 노출되어야 하고, 설정 파일은 문서와 release gate가 함께 따라와야 한다. 문서만 추가하고 검증을 추가하지 않거나, 스크립트만 추가하고 운영 문서에 쓰지 않으면 UX drift로 본다.

이 파일은 새 담당자가 프로젝트를 받았을 때 어디부터 봐야 하는지 알려주는 관리 지도다. 코드 검토는 큰 파일만 보는 것이 아니라, 사용자가 실제로 실행하는 흐름과 문서가 같은 순서를 말하는지 확인하는 방식으로 진행한다.
