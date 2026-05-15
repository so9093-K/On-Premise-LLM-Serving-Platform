# Admin, Metrics, Docs 노출 정책

이 프로젝트는 인증 모드와 기능 표면을 분리한다. 인증 모드는 endpoint 존재 여부를 임의로 바꾸는 기능이 아니라, 누가 접근할 수 있는지를 제어하는 정책이다.

## Endpoint별 기본 정책

| Endpoint | 목적 | 기본 노출 정책 |
|---|---|---|
| `/health` | process liveness | 공개 가능. secret이나 dependency 상태를 노출하지 않는다. |
| `/ready` | dependency readiness | admin token 필요. 내부망만으로 보호한다면 `ADMIN_ENDPOINTS_INTERNAL_ONLY=true`를 명시한다. |
| `/metrics` | Prometheus scrape | admin token 또는 Prometheus 전용 bearer token 파일로 보호한다. public internet 직접 노출 금지. |
| `/docs`, `/redoc`, `/openapi.json` | 운영/개발 문서 | `local_open`, `private_network`에서는 enabled 가능. `edge_terminated`, `strict`에서는 기본 disabled. |
| `/v1/*` | public Gateway API | `AUTH_MODE`에 따라 API key 또는 edge 인증을 요구한다. |
| Risk Adapter `/v1/risk/*` | Gateway 내부 risk signal 호출 | 기본적으로 internal service token 필요. |

## 현재 구현 경계

- `/ready`, `/metrics`는 `ADMIN_API_KEY_REQUIRED=true`일 때 app-level admin bearer token을 요구한다.
- `ADMIN_ENDPOINTS_INTERNAL_ONLY=true`는 ingress, firewall, VPN, compose network boundary로 보호한다는 운영 선언이다. 현재 app-level CIDR allowlist는 구현하지 않았다.
- FastAPI docs UI 자체는 token을 발급하거나 API 호출 권한을 부여하지 않는다. 다만 public internet에 직접 노출하면 API shape가 노출되므로 `strict`/`edge_terminated` profile에서는 기본적으로 비활성화한다.
- Prometheus는 `.runtime/prometheus/admin_api_key` bearer token 파일을 Compose secret으로 `/run/secrets/admin_api_key`에 마운트해 사용한다. 이 파일은 package에 포함하지 않는다.
- Prometheus image는 non-root UID로 실행되므로 bearer token 파일은 일반 파일이며 컨테이너에서 읽을 수 있는 권한이어야 한다. `make sync-runtime-secrets`는 `.env`의 `ADMIN_API_KEY`를 파일에 쓰고 `0644` 권한으로 맞춘다.

## Compose 파일 구조

`ops/compose/full-stack.private-network.yaml`이 표준 compose 파일이다. `make compose-up`, `make compose-down`, `make compose-logs` 모두 이 파일을 사용한다.

```bash
make init-env-compose
make compose-up
make compose-down
```

`ops/compose/full-stack.private-network.yaml`은 vLLM과 Risk Adapter, Prometheus, cAdvisor, DCGM exporter를 compose network 내부로 유지한다. Gateway host publish bind는 `GATEWAY_BIND_ADDR`로 제어하며 shared/staging 환경에서는 175의 내부 interface IP를 명시한다. 전체 interface publish가 의도된 경우에만 `GATEWAY_BIND_ADDR=0.0.0.0`을 사용한다. Grafana host publish는 `GRAFANA_BIND_ADDR`(기본값 `0.0.0.0`)으로 제어하며, firewall/network policy로 접근 범위를 제한한다. `ADMIN_API_KEY_REQUIRED`, `STRICT_ADMIN_ENDPOINT_SECURITY`는 `.env`의 auth profile 설정을 따른다. 새 runtime service를 추가하면 `modelctl diff`, `runtime_validation --config-only`로 compose 파일 drift를 확인한다.

Gateway/Risk Adapter의 `/metrics` 응답은 Prometheus text format으로 제공한다. OpenMetrics Content-Type을 쓰려면 body가 `# EOF`로 끝나야 하므로, 현재 구현은 `prometheus_client.generate_latest()`와 맞는 `text/plain` Content-Type을 사용한다.

## 운영 점검 명령

```bash
make auth-status
make auth-doctor
make preflight-compose
make release-check
```

`make auth-status`는 docs/openapi, admin endpoint, observability host-published service 상태를 보여준다. `make auth-doctor`는 non-local 환경에서 public/admin/internal endpoint가 열리는 위험 조합을 경고 또는 실패로 표시한다.

## 후속 설계 후보

네트워크 기반 admin 보호를 app 내부에서 더 강하게 표현해야 한다면 다음 설정을 별도 phase에서 도입한다.

```text
ADMIN_AUTH_MODE=token|network|token_or_network|disabled
ADMIN_ALLOWED_CIDRS=127.0.0.1/32,10.0.0.0/8
```

이 기능을 도입하기 전까지 production-like profile에서는 admin token을 기본값으로 둔다.
