---
id: operator.start_here
title: 어디서 시작할지 모르겠다면
audience: operator, developer, release_engineer
status: current
document_type: source
owner: operations
related_commands:
  - make help
  - make guide
  - make doctor
---

# 어디서 시작할지 모르겠다면

> 빠른 답이 필요하면 바로 아래 상황을 골라라.
> 전체 문서 목록이 필요하면 [docs/README.md](README.md)를 본다.
> 명령어 레퍼런스는 `make help`, 상황별 추천은 `make guide`를 실행한다.

---

## 상황을 골라라

### 처음 압축을 풀고 실행만 해보고 싶다

**목표:** 코드가 정상인지, API가 응답하는지 확인한다.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python3.12 -m pip install -r requirements.lock
python3.12 -m pip install --no-deps -e ".[contract]"
make init-env-local
make validate
make test
make start
make ready-local
```

- GPU 없이 동작한다.
- `/health`가 성공하면 정상이다.
- 📄 [operations/day0_quickstart.md](operations/day0_quickstart.md)
- ⚠️ app-only에서 `make ready`(full-stack 전용)를 쓰면 실패한다. 반드시 `make ready-local`을 사용한다.

---

### GPU 없이 API와 코드만 확인하고 싶다

**목표:** vLLM 없이 Gateway와 Risk Adapter의 API contract, schema, unit/contract test를 검증한다.

```bash
make init-env-local
make validate
make test
make start
make ready-local
make auth-status
```

- 📄 [operations/day0_quickstart.md#1-app-only-확인](operations/day0_quickstart.md#1-app-only-확인)
- 📊 생성 report: 없음 (static validation만)

---

### GPU 서버에서 full-stack을 올리고 싶다

**목표:** vLLM 3개 + Prometheus + Grafana + DCGM + cAdvisor를 compose로 올린다.

**사전 조건:** Docker, NVIDIA Container Toolkit, GPU 48GiB, HuggingFace token

```bash
HF_TOKEN=hf_xxx AUTH_MODE=local_open make first-run
source .venv/bin/activate
make compose-up
make ready-full
make runtime-validate
make operator-reports
```

- 📄 [operations/day0_quickstart.md#2-full-stack-확인](operations/day0_quickstart.md#2-full-stack-확인)
- 📄 [operations/full_stack_runtime.md](operations/full_stack_runtime.md)
- 📊 생성 report: `reports/runtime/`
- ⚠️ 먼저 `make auth-doctor`로 위험 조합을 확인한다.

---

### 보안 profile을 선택하고 싶다

**목표:** 환경에 맞는 인증 모드(`local_open`, `private_network`, `edge_terminated`, `strict`)를 고른다.

| 내 상황 | 추천 AUTH_MODE |
|---|---|
| 로컬 노트북/단일 사용자 개발 | `local_open` |
| 내부 GPU 서버, 팀 공용, VPN/사설망 | `private_network` |
| edge proxy(SSO/API GW)가 public 인증 담당 | `edge_terminated` |
| public endpoint, internet-facing | `strict` |
| 직접 flag 조합 관리 | `custom` |

```bash
make auth-status              # 현재 상태 확인
make auth-doctor              # 위험 조합 진단
make auth-plan MODE=strict    # 변경 전 계획 확인
make auth-apply MODE=strict   # flag만 적용 (secret 보존)
```

> `ADMIN_ENDPOINTS_INTERNAL_ONLY`는 현재 app-level CIDR enforcement가 아니다.
> 네트워크 경계(ingress/firewall/VPN)로 admin endpoint를 보호한다는 선언이며,
> app-level 차단은 구현되지 않았다. 이 사실은 `make auth-status`와 `make auth-doctor`에서 확인할 수 있다.

- 📄 [operations/auth_control_plane.md](operations/auth_control_plane.md)
- 📄 [operations/admin_metrics_docs_exposure_policy.md](operations/admin_metrics_docs_exposure_policy.md)
- ⚠️ `local_open` + non-local APP_ENV + API key off + public bind 조합은 `make auth-doctor`가 WARN/FAIL로 표시한다.

---

### 모델을 추가/제거하고 싶다

**목표:** 새 모델을 등록하거나 기존 모델을 제거한다. 바로 파일을 수정하지 않고 계획부터 만든다.

```bash
make model-list
make model-validate
make model-diff
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm
make model-propose-remove ID=local-main
```

- 이 명령은 파일을 쓰지 않는다. 영향 파일, 충돌, GPU budget 경고를 출력한다.
- 📄 [operations/model_runtime_control.md](operations/model_runtime_control.md)
- 📄 [operations/model_parameter_discovery.md](operations/model_parameter_discovery.md)

---

### `stream=true` streaming 운영 정책을 보고 싶다

```bash
# API 호출 예시
curl -N http://localhost:9400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local-main","messages":[{"role":"user","content":"안녕"}],"stream":true}'
```

- 📄 [operations/streaming_runtime_operations.md](operations/streaming_runtime_operations.md)

---

### Grafana / Prometheus를 보고 싶다

full-stack compose 기동 후:

| 서비스 | URL |
|---|---|
| Grafana | `http://localhost:9411` (기본 dashboard: `GPU Capacity and OOM Risk`) |
| Prometheus | `http://localhost:9410` |

```bash
make monitoring-projection      # Prometheus/Grafana projection 리포트
make operator-reports           # 전체 운영 리포트 통합 생성
```

현재 dashboard는 6개다: `serving_home`, `gpu_capacity_and_oom_risk`, `api_experience`, `model_runtime_deep_dive`, `risk_signal_operations`, `observability_data_quality`.

- 📄 [operations/monitoring_ux.md](operations/monitoring_ux.md)
- 📄 [operations/grafana_status_board.md](operations/grafana_status_board.md)
- 📊 생성 report: `reports/runtime/monitoring_projection.md`

---

### 장애 대응을 해야 한다

```bash
make doctor                   # Python/contracts/bash/env 진단
make compose-diagnostics      # compose 상태 + vLLM 장애 패턴 요약
READY_MODE=full make status   # 전체 서비스 상태
make auth-doctor              # 인증 설정 위험 조합 확인
```

| 증상 | 먼저 확인 |
|---|---|
| `make ready-full` 실패 | `make compose-diagnostics` |
| embedding model 로드 실패 | `HF_TOKEN`, Gemma 라이선스 동의 여부 |
| Prometheus admin token 오류 | `make sync-runtime-secrets` |
| auth mismatch | `make auth-doctor` |
| Risk vLLM config 오류 | `make risk-vllm-config-check` |

- 📄 [operations/full_stack_troubleshooting.md](operations/full_stack_troubleshooting.md)

---

### 릴리스 패키지를 만들고 싶다

```bash
make release-check            # 정적 gate (파일 생성 포함)
make release-check-full       # 정적 gate + deterministic test
make refresh-generated-reports # generated report 재생성
make package                  # refresh → validate → dist/ ZIP 생성
```

> `make release-check`는 현재 report generator를 포함하므로 완전한 read-only가 아니다.
> `make package`를 실행하기 전 `make refresh-generated-reports`는 자동 실행된다.

- 📄 [release/release_checklist.md](release/release_checklist.md)
- 📄 [development/build_ux.md](development/build_ux.md)
- 📊 생성 report: `dist/ai_model_serving_platform_<version>.zip`

---

### 버전을 변경하고 싶다

**package version과 package-aligned image tag를 변경한다.** config schema version과 historical changelog/report는 별도로 관리된다.

```bash
make reset-version NEW_VERSION=0.1.0
```

다음이 함께 변경된다: `VERSION`, `version_manifest.json`, `pyproject.toml`, `specs/openapi.*.yaml`, `README.md`, `.env.example`, `.env.local.example`, `.env.compose.example`, `configs/recommended_images.yaml`(platform/risk_vllm 이미지 tag), `docs/release/versioning_policy.md`

변경되지 않는다: config schema version(`configs/model_catalog.yaml`, `configs/monitoring.yaml`, `configs/storage_paths.yaml`의 `version: 0.1.0`), CHANGELOG 과거 항목, historical reports

- 📄 [release/versioning_policy.md](release/versioning_policy.md)

---

### 문서/리포트 drift를 확인하고 싶다

```bash
make project-inventory        # 파일/문서/ownership inventory 생성
make operator-reports         # 전체 운영 리포트 갱신
make validate                 # 계약·스키마·정책·문서 정적 검증
make release-check            # 릴리스 gate 전체 실행
```

- 📄 [operations/project_management_workflow.md](operations/project_management_workflow.md)
- 📊 생성 report: `reports/refactor/project_inventory_current.md`

---

## 문서 유형 안내

| 유형 | 위치 | 설명 |
|---|---|---|
| **source** | `docs/` | 사람이 읽는 상세 문서의 단일 홈 |
| **decision** | `docs/adr/` | Architectural Decision Records. canonical decision source-of-truth |
| **examples** | `docs/examples/` | 설명형 API examples. 실행 가능한 sample payload가 추가되면 root `examples/`에 둔다 |
| **archive** | `docs/archive/` | historical context. 현재 운영 기준으로 쓰지 않는다 |
| **generated** | `reports/runtime/` | 스크립트가 생성하는 runtime evidence. 직접 수정하지 않는다 |
| **handoff** | `reports/refactor/current_*`, `reports/refactor/project_inventory_current.*` | 현재 상태/handoff/inventory artifact |
| **changelog** | `CHANGELOG.md` | root의 짧은 버전별 릴리스 노트 |

---

## 명령 분류

| 목적 | 명령 |
|---|---|
| 전체 명령 목록 | `make help` |
| 상황별 추천 | `make guide` |
| 정적 검증 | `make validate` |
| 테스트 | `make test` |
| 릴리스 gate | `make release-check` |
| 운영 리포트 생성 | `make operator-reports` |
| 릴리스 ZIP | `make package` |
