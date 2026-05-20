# 문서 안내

이 디렉터리는 ai_model_serving_platform의 전체 문서를 담는다.

> **처음 왔다면:** [START_HERE.md](START_HERE.md)에서 상황을 골라 바로 이동하거나, 아래 표에서 목적에 맞는 문서를 열어라.

## 문서 유형

| 유형 | 위치 | 설명 |
|---|---|---|
| **source** | `docs/` | 사람이 읽는 상세 문서의 단일 홈 |
| **decision** | `docs/adr/` | canonical Architectural Decision Records |
| **examples** | `docs/examples/` | 설명형 API examples. 실행 가능한 sample payload가 추가되면 root `examples/`에 둔다 |
| **archive** | `docs/archive/` | historical context. 현재 운영 기준으로 쓰지 않는다 |
| **generated** | `reports/runtime/` | 스크립트가 생성하는 runtime evidence. 직접 수정하지 않는다 |
| **handoff** | `reports/refactor/current_*`, `reports/refactor/project_inventory_current.*` | 현재 상태/handoff/inventory artifact |
| **release history** | `CHANGELOG.md` | root의 짧은 버전별 릴리스 노트 |

## 운영자 (Operators)

| 상황 | 문서 |
|---|---|
| **어디서 시작할지 모르겠다** | [START_HERE.md](START_HERE.md) |
| 패키지를 처음 받았다 / 전체 흐름을 알고 싶다 | [operations/first_project_guide.md](operations/first_project_guide.md) |
| 빠른 실행 명령만 보고 싶다 | [operations/day0_quickstart.md](operations/day0_quickstart.md) |
| GPU 없이 코드·API만 확인하고 싶다 | [operations/day0_quickstart.md §1](operations/day0_quickstart.md#1-app-only-확인) |
| GPU 서버에서 full-stack을 올리고 싶다 | [operations/day0_quickstart.md §2](operations/day0_quickstart.md#2-full-stack-확인) |
| 전체 초기화·재빌드를 하고 싶다 | [operations/day0_quickstart.md §4](operations/day0_quickstart.md#4-전체-초기화--재빌드-ux) |
| **시크릿·API 토큰을 웹 UI로 관리하고 싶다** | [operations/day0_quickstart.md §6](operations/day0_quickstart.md#6-시크릿-관리-infisical-선택) |
| 인증·admin/metrics/docs 노출 정책을 보고 싶다 | [operations/auth_control_plane.md](operations/auth_control_plane.md), [operations/admin_metrics_docs_exposure_policy.md](operations/admin_metrics_docs_exposure_policy.md) |
| runtime validation URL/env 우선순위를 확인하고 싶다 | [operations/runtime_validation_operations.md#설정-우선순위](operations/runtime_validation_operations.md#설정-우선순위) |
| 상황별 명령을 고르고 싶다 | [operations/operator_workflows.md](operations/operator_workflows.md) |
| 통합 설정·관리·빌드·제거 흐름을 보고 싶다 | [operations/configuration_lifecycle.md](operations/configuration_lifecycle.md) |
| 통합 프로젝트 관리 흐름을 보고 싶다 | [operations/project_management_workflow.md](operations/project_management_workflow.md) |
| 로컬 저장 경로·모델 캐시 위치를 보고 싶다 | [operations/storage_paths.md](operations/storage_paths.md) |
| 장애가 났다 / 서비스가 안 뜬다 | [operations/full_stack_troubleshooting.md](operations/full_stack_troubleshooting.md) |
| GitLab CI/CD로 175 GPU 서버에 배포하고 싶다 | [operations/gitlab_cicd_deployment.md](operations/gitlab_cicd_deployment.md) |
| Grafana·Prometheus 모니터링을 설정한다 | [operations/monitoring_ux.md](operations/monitoring_ux.md) |
| **서비스 URL·API endpoint·모니터링 주소를 한눈에 보고 싶다** | [operations/endpoint_reference.md](operations/endpoint_reference.md) |
| 사용자 조정 가능 모델 파라미터를 보고 싶다 | [operations/model_parameter_discovery.md](operations/model_parameter_discovery.md) |
| `stream=true` 운영, proxy buffering, timeout 정책을 보고 싶다 | [operations/streaming_runtime_operations.md](operations/streaming_runtime_operations.md) |
| 모델 구성·리소스 계획을 보고 싶다 | [models/model_cards.md](models/model_cards.md), [resources/gpu_resource_requirements_48gb.md](resources/gpu_resource_requirements_48gb.md) |
| GPU 리소스 배분 기준을 보고 싶다 | [resources/gpu_resource_plan.md](resources/gpu_resource_plan.md) |
| 모델 runtime 제어 기준을 보고 싶다 | [operations/model_runtime_control.md](operations/model_runtime_control.md) |
| Grafana 상태 보드 구성을 보고 싶다 | [operations/grafana_status_board.md](operations/grafana_status_board.md) |
| Risk vLLM patch 관리 정책을 보고 싶다 | [operations/risk_vllm_patch_lifecycle.md](operations/risk_vllm_patch_lifecycle.md) |
| 전체 서비스 컴포넌트 목록을 보고 싶다 | [operations/full_stack_runtime.md](operations/full_stack_runtime.md) |
| 릴리스 버전 정책을 확인하고 싶다 | [release/versioning_policy.md](release/versioning_policy.md) |
| 릴리스 전 체크리스트를 보고 싶다 | [release/release_checklist.md](release/release_checklist.md) |

## 개발자 (Developers)

| 상황 | 문서 |
|---|---|
| API 스펙을 확인하고 싶다 | [specs/api.md](specs/api.md) |
| API request 예시 설명을 보고 싶다 | [examples/requests.md](examples/requests.md) |
| 빌드·패키징 명령 의미를 이해하고 싶다 | [development/build_ux.md](development/build_ux.md) |
| Python 버전 호환성을 확인하고 싶다 | [development/python_compatibility.md](development/python_compatibility.md) |
| 테스트 전략을 보고 싶다 | [development/test_strategy.md](development/test_strategy.md) |
| 릴리스 전 체크리스트가 필요하다 | [development/final_checklist.md](development/final_checklist.md) |
| 로깅 정책을 보고 싶다 | [development/logging_policy.md](development/logging_policy.md) |
| 아키텍처·설계 배경을 알고 싶다 | [06_architecture.md](06_architecture.md), [01_project_background.md](01_project_background.md) |
| 결정 기록(ADR)을 보고 싶다 | [02_decision_register.md](02_decision_register.md) (index) → [`docs/adr/`](adr/) (canonical) |
| 문서 관리 정책을 보고 싶다 | [governance/document_management.md](governance/document_management.md) |

## 디렉터리 구조

```
docs/
├── README.md                        ← 지금 읽는 파일 (진입점)
├── manifest.yaml                    ← 문서 lifecycle/owner/source-of-truth registry
├── adr/                             ← canonical decision records
├── examples/                        ← 설명형 API examples
├── archive/                         ← historical context
├── operations/                      ← 운영자 문서
│   ├── first_project_guide.md       ← 처음 프로젝트를 받았을 때 전체 가이드
│   ├── day0_quickstart.md           ← 빠른 시작 명령
│   ├── endpoint_reference.md        ← 서비스 URL·API·모니터링 주소 모음
│   ├── operator_workflows.md        ← 상황별 명령 선택 가이드
│   ├── configuration_lifecycle.md   ← 설정·관리·빌드·제거 통합 UX
│   ├── project_management_workflow.md
│   ├── storage_paths.md             ← 로컬 저장소·모델 캐시·cleanup 정책
│   ├── full_stack_runtime.md        ← 전체 서비스 컴포넌트 목록
│   ├── gitlab_cicd_deployment.md    ← GitLab CI/CD와 175 배포 가이드
│   ├── full_stack_troubleshooting.md
│   ├── monitoring_ux.md
│   ├── grafana_status_board.md      ← Grafana 상태 보드 구성
│   ├── runtime_validation_operations.md
│   ├── model_runtime_control.md     ← 모델 runtime 제어 기준
│   ├── risk_vllm_patch_lifecycle.md ← Risk vLLM patch 관리 정책
│   └── streaming_runtime_operations.md
├── development/                     ← 개발자 문서
│   ├── README.md                    ← 개발 가이드 진입점
│   ├── build_ux.md                  ← make 명령 의미론 및 빌드 흐름
│   ├── python_compatibility.md      ← Python 버전 호환성
│   ├── test_strategy.md             ← 테스트 전략
│   ├── final_checklist.md           ← 개발 완료 체크리스트
│   └── logging_policy.md            ← 로깅 정책
├── specs/
│   ├── api.md
│   ├── configuration.md
│   └── risk_signal_contract.md
├── models/
│   └── model_cards.md
├── resources/
│   ├── gpu_resource_requirements_48gb.md
│   └── gpu_resource_plan.md         ← GPU 리소스 배분 기준
├── release/
│   ├── versioning_policy.md
│   └── release_checklist.md         ← 릴리스 전 체크리스트
└── governance/
    ├── document_management.md
    └── policies/
```

## 빠른 참조: 핵심 명령

```bash
make help          # 전체 명령 목록
make guide         # 상황별 명령 추천
make doctor        # 환경 진단
make build-pipeline # 통합 파이프라인 빌드 (서비스 기동 없음)
make remove-plan   # 삭제 대상 미리 보기
make reset         # 통합 제거/초기화 (서비스·플랫폼/risk 이미지·아티팩트)
make first-run     # 처음 full-stack 준비 (make bootstrap 별칭)
make rebuild-full  # 전체 재빌드 (.venv·deps·env·validate·test·platform/risk image)
make start         # 로컬 app-only 기동
make compose-up    # full-stack compose 기동
make ready-local   # 로컬 health 확인
make ready         # full-stack readiness 확인
make infisical-up  # 시크릿 관리 UI 기동 (선택)
make secrets-push  # .env → Infisical 동기화
make project-inventory # 전체 파일·문서·관리 inventory 생성
make operator-reports # 운영 산출물 통합 생성
```
