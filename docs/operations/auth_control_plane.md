# 인증 제어 플레인 UX

이 프로젝트는 **인증 모드와 비인증 모드를 API 기능 차이 없이 운영**하도록 설계한다. 인증 제어 플레인은 endpoint를 없애거나 모델 동작을 바꾸는 기능이 아니라, 현재 flag 조합을 사람이 이해하고 계획하고 검증할 수 있게 만드는 관리 UX다.

## 운영 원칙

- 기능 표면은 동일하게 유지한다. `/v1/*`, `/ready`, `/metrics`, `/docs`, `/openapi.json`의 존재 여부를 모드별로 임의 변경하지 않는다.
- 인증 모드는 접근 경계만 바꾼다. request/response schema, model id, risk signal-only contract는 그대로 둔다.
- 비인증은 기본적으로 로컬 개발용이다. staging/production에서 public API를 열어야 한다면 `edge_terminated` 또는 `custom`으로 의도를 명시한다.
- secret 값은 명령 출력에 노출하지 않는다. `auth-plan`, `auth-status`, `auth-doctor`는 flag와 상태만 보여준다.

## 핵심 플래그

| Flag | 의미 |
|---|---|
| `AUTH_MODE` | 사람이 읽는 운영 profile. `local_open`, `private_network`, `edge_terminated`, `strict`, `custom` 중 하나다. |
| `API_KEY_REQUIRED` | Gateway public `/v1/*` Bearer token 요구 여부다. |
| `ADMIN_API_KEY_REQUIRED` | `/ready`, `/metrics` admin token 요구 여부다. docs/OpenAPI는 `FASTAPI_DOCS_ENABLED`와 배포/네트워크 경계 정책으로 관리한다. |
| `ADMIN_ENDPOINTS_INTERNAL_ONLY` | ingress/firewall/VPN 등 네트워크 경계로 admin endpoint를 보호한다는 운영 선언이다. app-level CIDR 차단은 아직 구현하지 않았다. |
| `INTERNAL_SERVICE_AUTH_REQUIRED` | Gateway → Risk Adapter 내부 호출에 service token을 요구할지 결정한다. |
| `INTERNAL_SERVICE_TOKEN` | Gateway가 Risk Adapter 호출 시 사용하는 내부 token이다. |

`INTERNAL_SERVICE_AUTH_REQUIRED`는 `API_KEY_REQUIRED`와 분리한다. public Gateway가 edge proxy 뒤에서 열려 있어도 Risk Adapter 내부 API는 계속 보호할 수 있어야 하기 때문이다.

## 인증 모드

| `AUTH_MODE` | 목적 | Public Gateway `/v1/*` | Admin endpoint | Gateway → Risk Adapter |
|---|---|---|---|---|
| `local_open` | 로컬 app-only 개발 | open | open | open |
| `private_network` | 사설망/VPN 서버 | API key 필요 | admin token 필요 | internal token 필요 |
| `edge_terminated` | 앞단 proxy/SSO/API Gateway가 public 인증 담당 | app API key 선택 | admin token 필요 | internal token 필요 |
| `strict` | production 또는 internet-facing | API key 필요 | admin token 필요 | internal token 필요 |
| `custom` | 운영자가 직접 조합 관리 | 명시 flag 기준 | 명시 flag 기준 | 명시 flag 기준 |

`auth-doctor`는 profile 기대값과 실제 flag가 다르면 알려준다. 단, 운영자가 직접 `custom`을 선택한 경우에는 자동으로 값을 고치지 않는다.

## 기본 생성값

| 생성 명령 | Profile | Public API | Admin endpoint | 내부 service | Docs |
|---|---|---|---|---|---|
| `make init-env-local` | `local_open` | open | open | open | enabled |
| `make init-env-compose` | `private_network` | API key | admin token | internal token | enabled |
| `make auth-apply MODE=strict` | `strict` | API key | admin token | internal token | disabled 권장 |

`make init-env-compose`가 staging-like `.env`를 만들 때 public API를 열어 두지 않는 것이 현재 안전 기본값이다. 이전 phase에서 발견된 `AUTH_MODE=private_network` + `API_KEY_REQUIRED=false` 조합은 더 이상 생성 기본값이 아니다.

## 운영 명령

```bash
make auth-status
make auth-doctor
make auth-status ENV=/tmp/candidate.env
make auth-doctor ENV=/tmp/candidate.env
make auth-plan MODE=strict ENV=/tmp/candidate.env
make auth-apply MODE=strict ENV=/tmp/candidate.env
python scripts/auth/auth_profile_sanity.py
```

| 명령 | 사용 시점 | 출력/동작 |
|---|---|---|
| `make auth-status` | 현재 인증 상태 확인 | public/admin/internal/observability 노출 상태를 secret 없이 표시 |
| `make auth-doctor` | 위험 조합 진단 | profile mismatch, production 비인증, admin endpoint 노출 등을 경고/실패로 표시 |
| `make auth-status ENV=<path>` | 후보 env 점검 | root `.env`에 반영하기 전 생성 env를 읽어 상태 표시 |
| `make auth-doctor ENV=<path>` | 후보 env 진단 | `setup_env.py --output` 결과를 바로 진단 |
| `make auth-plan MODE=<profile> [ENV=<path>]` | 변경 전 검토 | 어떤 flag가 어떻게 바뀌는지 plan만 출력 |
| `make auth-apply MODE=<profile> [ENV=<path>]` | 관리 flag 적용 | secret은 보존하고 managed auth flag만 수정 |
| `auth_profile_sanity.py` | release gate | `setup_env.py`가 만드는 env와 `AUTH_MODE` 기대값 drift 방지 |

## UX 흐름

1. 운영자는 먼저 `make auth-status`로 현재 상태를 본다. 후보 env 파일은 `make auth-status ENV=<path>`로 먼저 확인한다.
2. 변경이 필요하면 `make auth-plan MODE=strict ENV=<path>`처럼 plan을 확인한다.
3. plan이 맞으면 `make auth-apply MODE=strict ENV=<path>`로 flag만 적용한다.
4. `make auth-doctor` 또는 `make auth-doctor ENV=<path>`로 위험 조합이 남았는지 확인한다.
5. 릴리스 전에는 `make release-check`가 profile generation sanity를 다시 검증한다.

이 흐름의 목적은 운영자가 `API_KEY_REQUIRED`, `ADMIN_API_KEY_REQUIRED`, `INTERNAL_SERVICE_AUTH_REQUIRED`를 각각 외워 조합하지 않게 만드는 것이다.

## 현재 한계와 후속 작업

`ADMIN_ENDPOINTS_INTERNAL_ONLY=true`는 현재 app-level CIDR allowlist를 설치하지 않는다. 따라서 non-local managed profile은 admin bearer token을 기본으로 요구한다. 네트워크 전용 admin 보호가 필요하면 `custom`을 사용하고 ingress, firewall, VPN, compose network boundary에서 별도 보호해야 한다.

Admin/Metrics/Docs 노출 정책은 [`admin_metrics_docs_exposure_policy.md`](admin_metrics_docs_exposure_policy.md)를 기준으로 본다. 후속으로는 `ADMIN_AUTH_MODE=token|network|token_or_network|disabled`와 CIDR allowlist를 도입하는 것이 좋다.
