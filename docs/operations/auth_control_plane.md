# 인증 제어 플레인 UX

이 프로젝트는 **인증 모드와 비인증 모드를 API 기능 차이 없이 운영**하도록 설계한다. 인증 제어 플레인은 endpoint를 없애거나 모델 동작을 바꾸는 기능이 아니라, 현재 flag 조합을 사람이 이해하고 계획하고 검증할 수 있게 만드는 관리 UX다.

## 핵심 구분: AUTH_MODE vs EXPOSURE_MODE

`AUTH_MODE`와 `EXPOSURE_MODE`는 서로 다른 책임을 갖는다.

| Flag | 책임 |
|---|---|
| `AUTH_MODE` | App-level 인증 책임만 표현한다. API key 요구 여부, admin token 요구 여부, 내부 서비스 token 요구 여부를 결정한다. |
| `EXPOSURE_MODE` | Compose host-published port/topology만 표현한다. 어떤 서비스가 host 네트워크에 노출되는지를 결정한다. |
| `APP_ENV` | Deploy class만 표현한다 (local, staging, production). |

Source-of-truth:
- `AUTH_MODE` profile 정의 → `configs/auth_profiles.yaml`
- `EXPOSURE_MODE` profile 정의 → `configs/exposure_profiles.yaml`

## 운영 원칙

- 기능 표면은 동일하게 유지한다. `/v1/*`, `/ready`, `/metrics`, `/docs`, `/openapi.json`의 존재 여부를 모드별로 임의 변경하지 않는다.
- 인증 모드는 접근 경계만 바꾼다. request/response schema, model id, risk signal-only contract는 그대로 둔다.
- 비인증은 기본적으로 로컬 개발용이다. staging/production에서 public API를 열어야 한다면 `edge_terminated`, `internal_trusted`, 또는 `custom`으로 의도를 명시한다.
- secret 값은 명령 출력에 노출하지 않는다. `auth-plan`, `auth-status`, `auth-doctor`는 flag와 상태만 보여준다.
- side effect는 감추지 않는다. `master_open` 모드의 Gateway bypass, vLLM direct access, Prometheus/DCGM/cAdvisor 직접 노출은 structured `diagnostics` 필드로 명시하고, `make exposure-status`와 `make auth-doctor`가 이를 기반으로 진단한다.

## 핵심 플래그

| Flag | 의미 |
|---|---|
| `AUTH_MODE` | 사람이 읽는 운영 profile. `local_open`, `internal_trusted`, `private_network`, `edge_terminated`, `strict`, `custom` 중 하나다. |
| `EXPOSURE_MODE` | Compose host-published port topology. `private_network`, `master_open`만 지원한다. |
| `API_KEY_REQUIRED` | Gateway public `/v1/*` Bearer token 요구 여부다. |
| `ADMIN_API_KEY_REQUIRED` | `/ready`, `/metrics` admin token 요구 여부다. |
| `ADMIN_ENDPOINTS_INTERNAL_ONLY` | ingress/firewall/VPN 등 네트워크 경계로 admin endpoint를 보호한다는 운영 선언이다. app-level CIDR 차단은 아직 구현하지 않았다. |
| `INTERNAL_SERVICE_AUTH_REQUIRED` | Gateway → Risk Adapter 내부 호출에 service token을 요구할지 결정한다. |
| `INTERNAL_SERVICE_TOKEN` | Gateway가 Risk Adapter 호출 시 사용하는 내부 token이다. |

## 인증 모드

| `AUTH_MODE` | 목적 | 인증 소유권 | Public Gateway `/v1/*` | Admin endpoint | Gateway → Risk Adapter | Docs |
|---|---|---|---|---|---|---|
| `local_open` | 외부 접근이 차단된 사내망 full-stack | network/operator | open | open | open | enabled |
| `internal_trusted` | production/staging 운영용 무인증 Gateway | caller_or_network | open (network boundary 인증) | ADMIN_ENDPOINTS_INTERNAL_ONLY=true 선언 | open | disabled |
| `private_network` | 사설망/VPN 서버 | app | API key 필요 | admin token 필요 | internal token 필요 | enabled |
| `edge_terminated` | 앞단 proxy/SSO/API Gateway가 public 인증 담당 | edge_proxy | app API key 선택 | admin token 필요 | internal token 필요 | disabled |
| `strict` | production 또는 internet-facing | app | API key 필요 | admin token 필요 | internal token 필요 | disabled |
| `custom` | 운영자가 직접 조합 관리 | operator | 명시 flag 기준 | 명시 flag 기준 | 명시 flag 기준 | 명시 flag 기준 |

### internal_trusted 상세

`internal_trusted`는 production/staging에서도 허용되는 운영용 무인증 Gateway profile이다. auth-doctor는 `APP_ENV=production + AUTH_MODE=internal_trusted + EXPOSURE_MODE=private_network` 조합을 무조건 FAIL로 보지 않는다. 대신 인증 소유권이 네트워크/호출자에 위임되어 있음을 INFO로 명시한다.

적용 조건:
- `API_KEY_REQUIRED=false` — Gateway API 인증을 네트워크/호출자에 위임
- `ADMIN_ENDPOINTS_INTERNAL_ONLY=true` — 네트워크 정책으로 admin endpoint를 보호한다고 선언
- `FASTAPI_DOCS_ENABLED=false` — API shape 노출 방지
- `INTERNAL_SERVICE_AUTH_REQUIRED=false` — 내부 서비스 인증도 네트워크 소유권에 위임

`make auth-doctor`는 이 조합을 FAIL 대신 `AUTH_DELEGATED_TO_NETWORK` INFO로 표시한다.

## 노출 모드 (EXPOSURE_MODE)

canonical EXPOSURE_MODE는 두 가지다. Source-of-truth: `configs/exposure_profiles.yaml`

| `EXPOSURE_MODE` | class | Host-published 서비스 | 주요 diagnostics |
|---|---|---|---|
| `private_network` | `default_private` | gateway, grafana | 모든 diagnostics false |
| `master_open` (`local_open` 기본값) | `diagnostic_full_stack` | gateway, 모든 vLLM runtime, risk_adapter, prometheus, grafana, dcgm_exporter, cadvisor | `gateway_bypass_possible`, `direct_model_runtime_access`, `direct_risk_adapter_access`, `direct_operations_endpoints` = true. `EXPOSURE_AUDIENCE` 필수 |

`master_open`은 외부 접근이 차단된 신뢰된 사내망의 full-stack 운영 모드다.
Gateway bypass와 vLLM direct access는 이 모드의 **의도된 특성**이다.
`AUTH_MODE=local_open`을 적용하면 `EXPOSURE_MODE=master_open`,
`EXPOSURE_AUDIENCE=private_lan`도 함께 적용된다. 인터넷 연결 가능 환경에서는
이 조합을 사용하지 않는다.

`EXPOSURE_MODE`는 `private_network`와 `master_open`만 지원한다.
`master_open`은 Gateway, vLLM runtime, Risk Adapter, Prometheus, Grafana, DCGM,
cAdvisor 등 전체 stack을 host에서 직접 확인하기 위한 full-stack diagnostic topology다.
이전 중간 설계에서 쓰였던 별도 open modes는 지원하지 않는다.

`make exposure-status` 또는 `make auth-doctor`로 현재 mode의 diagnostics를 확인한다.

## 기본 생성값

| 생성 명령 | AUTH Profile | EXPOSURE Profile | Public API | Admin endpoint | 내부 service | Docs |
|---|---|---|---|---|---|---|
| `make init-env-local` | `local_open` | (local, compose 무관) | open | open | open | enabled |
| `make init-env-compose` | `local_open` | `master_open` / `private_lan` | open | open | open | enabled |
| `make auth-apply MODE=internal_trusted` | `internal_trusted` | (별도 관리) | open (network auth) | INTERNAL_ONLY 선언 | open | disabled |
| `make auth-apply MODE=private_network` | `private_network` | (별도 관리) | API key | admin token | internal token | enabled |
| `make auth-apply MODE=strict` | `strict` | (별도 관리) | API key | admin token | internal token | disabled |

`make init-env-compose`는 기본값으로 `local_open` profile과
`master_open` / `private_lan` exposure를 생성한다. 즉 사내망 사용자는 Gateway와
vLLM endpoint를 모두 host에서 직접 사용할 수 있다.

## 운영 명령

```bash
make auth-status
make auth-doctor
make exposure-status
make exposure-status EXPOSURE_MODE=master_open
make auth-status ENV=/tmp/candidate.env
make auth-doctor ENV=/tmp/candidate.env
make auth-plan MODE=internal_trusted ENV=/tmp/candidate.env
make auth-apply MODE=internal_trusted ENV=/tmp/candidate.env
make compose-up EXPOSURE_MODE=master_open EXPOSURE_AUDIENCE=private_lan
make compose-up-master  # shorthand for master_open
python scripts/auth/auth_profile_sanity.py
```

| 명령 | 사용 시점 | 출력/동작 |
|---|---|---|
| `make auth-status` | 현재 인증 상태 확인 | public/admin/internal/observability/exposure 상태를 secret 없이 표시 |
| `make auth-doctor` | 위험 조합 진단 | profile mismatch, production 비인증, admin endpoint 노출, exposure side effect 등을 경고/실패로 표시 |
| `make exposure-status` | exposure profile 확인 | 현재 EXPOSURE_MODE의 host-published 서비스와 side effect 표시 |
| `make compose-up EXPOSURE_MODE=master_open` | master_open topology로 compose 기동 | base compose + exposure.master-open.yaml override. EXPOSURE_AUDIENCE 필수 |
| `make compose-config EXPOSURE_MODE=master_open` | compose 병합 결과 확인 | 기동 없이 merged config 출력 |
| `make auth-plan MODE=strict [ENV=<path>]` | 변경 전 검토 | 어떤 flag가 어떻게 바뀌는지 plan만 출력 |
| `make auth-apply MODE=strict [ENV=<path>]` | 관리 flag 적용 | secret은 보존하고 managed auth flag만 수정 |
| `auth_profile_sanity.py` | release gate | `setup_env.py`가 만드는 env와 `AUTH_MODE` 기대값 drift 방지 |

## UX 흐름

1. 운영자는 먼저 `make auth-status`로 현재 인증 상태를 본다.
2. `make exposure-status`로 현재 host-published 서비스와 side effect를 확인한다.
3. 변경이 필요하면 `make auth-plan MODE=strict`처럼 plan을 확인한다.
4. plan이 맞으면 `make auth-apply MODE=strict`로 flag만 적용한다.
5. compose topology 변경이 필요하면 `make compose-up EXPOSURE_MODE=master_open`처럼 EXPOSURE_MODE를 지정한다.
6. `make auth-doctor`로 위험 조합이 남았는지 확인한다.
7. 릴리스 전에는 `make validate`가 profile generation sanity를 다시 검증한다.

## 현재 한계와 후속 작업

`ADMIN_ENDPOINTS_INTERNAL_ONLY=true`는 현재 app-level CIDR allowlist를 설치하지 않는다. 따라서 non-local managed profile은 admin bearer token을 기본으로 요구한다. 네트워크 전용 admin 보호가 필요하면 `custom` 또는 `internal_trusted`를 사용하고 ingress, firewall, VPN, compose network boundary에서 별도 보호해야 한다.

Admin/Metrics/Docs 노출 정책은 [`admin_metrics_docs_exposure_policy.md`](admin_metrics_docs_exposure_policy.md)를 기준으로 본다.
