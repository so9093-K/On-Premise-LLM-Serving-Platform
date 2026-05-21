# Admin, Metrics, Docs 노출 정책

이 프로젝트는 인증 모드(`AUTH_MODE`)와 노출 모드(`EXPOSURE_MODE`)를 분리한다. 인증 모드는 endpoint 접근 제어를, 노출 모드는 Compose host-published port topology를 각각 독립적으로 제어한다.

Source-of-truth: `configs/exposure_profiles.yaml` (canonical_modes, diagnostics, host-published references) + `configs/services.yaml` (compose service/port/bind mapping)

## Endpoint별 기본 정책

| Endpoint | 목적 | 기본 노출 정책 |
|---|---|---|
| `/health` | process liveness | 공개 가능. secret이나 dependency 상태를 노출하지 않는다. |
| `/ready` | dependency readiness | admin token 필요. 내부망만으로 보호한다면 `ADMIN_ENDPOINTS_INTERNAL_ONLY=true`를 명시한다. |
| `/metrics` | Prometheus scrape | admin token 또는 Prometheus 전용 bearer token 파일로 보호한다. public internet 직접 노출 금지. |
| `/docs`, `/redoc`, `/openapi.json` | 운영/개발 문서 | `local_open`, `private_network`에서는 enabled 가능. `edge_terminated`, `strict`, `internal_trusted`에서는 기본 disabled. |
| `/v1/*` | public Gateway API | `AUTH_MODE`에 따라 API key 또는 edge/network 인증을 요구한다. |
| Risk Adapter `/v1/risk/*` | Gateway 내부 risk signal 호출 | 기본적으로 internal service token 필요. |

## EXPOSURE_MODE × Host-published 서비스 매트릭스

canonical EXPOSURE_MODE는 두 가지다.

| 서비스 | private_network | master_open |
|---|---|---|
| gateway | ✓ host | ✓ host |
| grafana | ✓ host | ✓ host |
| main-llm-vllm | compose only | ✓ host |
| embedding-vllm | compose only | ✓ host |
| embedding-ko-vllm | compose only | ✓ host |
| risk-prompt-vllm | compose only | ✓ host |
| risk-adapter | compose only | ✓ host |
| prometheus | compose only | ✓ host |
| dcgm-exporter | compose only | ✓ host |
| cadvisor | compose only | ✓ host |

`EXPOSURE_MODE`는 `private_network`와 `master_open`만 지원한다.
`master_open`은 Gateway, vLLM runtime, Risk Adapter, Prometheus, Grafana, DCGM,
cAdvisor 등 전체 stack을 host에서 직접 확인하기 위한 full-stack diagnostic topology다.
이전 중간 설계에서 쓰였던 별도 open modes는 지원하지 않는다.

### Structured Diagnostics

exposure profile별 diagnostics는 구조화된 boolean 필드로 표현된다. `make exposure-status`와 `make auth-doctor`가 이 필드를 기반으로 진단한다.

| Diagnostic | private_network | master_open |
|---|---|---|
| `gateway_bypass_possible` | false | **true** |
| `direct_model_runtime_access` | false | **true** |
| `direct_risk_adapter_access` | false | **true** |
| `direct_operations_endpoints` | false | **true** |
| `requires_exposure_audience` | false | **true** |

`master_open`의 Gateway bypass와 vLLM direct access는 **의도된 특성**이다. 별도 모드로 회피하는 대신, `EXPOSURE_AUDIENCE`로 대상 네트워크를 명시하고 preflight/doctor가 구조적으로 진단한다.

### EXPOSURE_AUDIENCE

`master_open` 사용 시 `EXPOSURE_AUDIENCE` 설정이 필수다. 이 값은 누가 host-published 포트에 접근할 수 있는지를 선언한다.

| 값 | 의미 |
|---|---|
| `local_only` | 로컬 호스트에서만 접근 |
| `private_lan` | 사설망/LAN |
| `vpn` | VPN 범위 내 |
| `public` | public internet — 추가 보호 없이 사용 금지 |

`make exposure-status`는 EXPOSURE_AUDIENCE 설정 여부와 remediation을 표시한다.

## 현재 구현 경계

- `/ready`, `/metrics`는 `ADMIN_API_KEY_REQUIRED=true`일 때 app-level admin bearer token을 요구한다.
- `ADMIN_ENDPOINTS_INTERNAL_ONLY=true`는 ingress, firewall, VPN, compose network boundary로 보호한다는 운영 선언이다. 현재 app-level CIDR allowlist는 구현하지 않았다.
- FastAPI docs UI 자체는 token을 발급하거나 API 호출 권한을 부여하지 않는다. 다만 public internet에 직접 노출하면 API shape가 노출되므로 `strict`/`edge_terminated`/`internal_trusted` profile에서는 기본적으로 비활성화한다.
- Prometheus는 `.runtime/prometheus/admin_api_key` bearer token 파일을 Compose secret으로 `/run/secrets/admin_api_key`에 마운트해 사용한다. 이 파일은 package에 포함하지 않는다.
- Prometheus image는 non-root UID로 실행되므로 bearer token 파일은 일반 파일이며 컨테이너에서 읽을 수 있는 권한이어야 한다. `make sync-runtime-secrets`는 `.env`의 `ADMIN_API_KEY`를 파일에 쓰고 `0644` 권한으로 맞춘다.

## Compose 파일 구조

`ops/compose/full-stack.private-network.yaml`이 base compose 파일이다. 모든 exposure override는 이 파일 위에 overlay된다. override 파일은 `scripts/compose/render_exposure_overrides.py`가 `configs/exposure_profiles.yaml`과 `configs/services.yaml`에서 자동 생성한다.

```bash
# private_network (기본값)
make compose-up

# master_open (full-stack diagnostic — vLLM, Risk Adapter, Prometheus, DCGM, cAdvisor host-published)
make compose-up EXPOSURE_MODE=master_open EXPOSURE_AUDIENCE=private_lan
make compose-up-master  # shorthand

# 병합 결과 확인 (기동 없음)
make compose-config EXPOSURE_MODE=master_open
```

Override 파일 위치: `ops/compose/overrides/`
- `exposure.master-open.yaml` — master_open override (GENERATED, do not edit manually)

Drift 검사: `python scripts/compose/render_exposure_overrides.py --check`

## 운영 점검 명령

```bash
make auth-status
make auth-doctor
make exposure-status
make exposure-status EXPOSURE_MODE=master_open
make preflight-compose
make release-check
```

`make auth-status`는 docs/openapi, admin endpoint, observability host-published service 상태와 EXPOSURE_MODE 정보를 보여준다. `make auth-doctor`는 non-local 환경에서 공개/admin/내부 endpoint가 열리는 위험 조합과 exposure diagnostics를 경고 또는 실패로 표시한다.

## 후속 설계 후보

네트워크 기반 admin 보호를 app 내부에서 더 강하게 표현해야 한다면 다음 설정을 별도 phase에서 도입한다.

```text
ADMIN_AUTH_MODE=token|network|token_or_network|disabled
ADMIN_ALLOWED_CIDRS=127.0.0.1/32,10.0.0.0/8
```

이 기능을 도입하기 전까지 production-like profile에서는 admin token을 기본값으로 둔다.
