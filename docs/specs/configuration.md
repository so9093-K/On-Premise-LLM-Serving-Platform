# 설정 사양

설정은 `configs/model_serving.yaml`과 `.env`가 함께 만든다. `.env`의 명시 값이 config 기본값보다 우선한다.

## 핵심 환경 변수

| 변수 | 설명 |
|---|---|
| `APP_ENV` | `local`, `staging`, `production` 등 실행 성격 |
| `API_KEY_REQUIRED` | 사용자 API bearer auth 요구 여부 |
| `API_KEYS` / `API_KEY` | Gateway 사용자 API key |
| `ADMIN_API_KEY_REQUIRED` | `/ready`, `/metrics` admin auth 요구 여부 |
| `ADMIN_API_KEY` / `ADMIN_API_KEYS` | admin endpoint key |
| `INTERNAL_SERVICE_TOKEN` | Gateway가 Risk Adapter API 호출에 쓰는 내부 token |
| `FASTAPI_DOCS_ENABLED` | `/docs`, `/redoc`, `/openapi.json` 활성화 여부. 기본 `true` |
| `REQUEST_TIMEOUT_SECONDS` | Gateway 전체 timeout |
| `RISK_ADAPTER_TIMEOUT_SECONDS` | Risk Adapter 호출 timeout |
| `*_BASE_URL` | vLLM 또는 Risk Adapter endpoint |
| `*_MAX_CONCURRENCY` | 모델별 Gateway-side concurrency |
| `*_QUEUE_TIMEOUT_SECONDS` | 모델별 queue wait timeout |
| `READY_FULL_TIMEOUT_SECONDS` | `make ready-full`이 vLLM 모델 로딩 readiness를 기다리는 최대 시간. 기본 `1800` |
| `READY_FULL_INTERVAL_SECONDS` | `make ready-full` readiness polling 간격. 기본 `10` |

## 모니터링

Prometheus, Grafana, DCGM exporter는 compose reference에서 기본 활성화한다. 별도 enable/disable 의도 표시용 flag는 사용하지 않는다. 실제 축소 여부는 runtime validation과 운영 관찰 후 결정한다.

## `.env` 생성

```bash
make init-env-compose
make init-env-compose-force
```

기본 명령은 기존 `.env`를 덮어쓰지 않는다. force 명령은 generated secret과 `PROJECT_VERSION`을 새로 만들되 운영자가 수정한 포트, image tag, timeout, model URL, `HF_TOKEN`은 보존한다.


## Streaming timeout/proxy 설정

`stream=true`는 긴 HTTP 연결을 유지하므로 Gateway의 `X-Accel-Buffering: no` header만으로 충분하지 않을 수 있습니다. Nginx/Ingress 앞단에서는 `proxy_buffering off`와 충분한 read timeout을 함께 설정하세요. 자세한 운영 절차는 [streaming_runtime_operations.md](../operations/streaming_runtime_operations.md)를 참고합니다.
