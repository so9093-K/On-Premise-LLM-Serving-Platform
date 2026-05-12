# Endpoint 참조

운영자·개발자가 서비스에 접근할 때 참조하는 주소 모음이다.  
포트 기본값은 `.env` 또는 `.env.example`의 `*_PORT` 변수로 재정의할 수 있다.

---

## 서비스 접근 주소 (localhost 기준 기본 포트)

| 서비스 | 주소 | 용도 |
|---|---|---|
| **Gateway API** | `http://localhost:9400` | 외부 API 진입점 |
| Gateway Scalar UI | `http://localhost:9400/docs` | 브라우저 API 탐색·테스트 (Scalar) |
| Gateway ReDoc | `http://localhost:9400/redoc` | 읽기 전용 API 문서 |
| Gateway OpenAPI JSON | `http://localhost:9400/openapi.json` | OpenAPI 스펙 다운로드 |
| **Risk Adapter API** | compose 내부 전용 (9405) | 내부 risk signal adapter |
| **Grafana** | `http://localhost:9411` | 운영 대시보드 |
| **Prometheus** | compose 내부 전용 (9090) | Metrics 수집·쿼리 |
| DCGM Exporter | compose 내부 전용 (9400) | GPU raw metrics |
| **Infisical** | `http://localhost:9420` | 시크릿 관리 웹 UI (선택) |

> `full-stack.private-network.yaml` 기준: vLLM runtime(9401–9403), Risk Adapter(9405), Prometheus, cAdvisor, DCGM Exporter는 compose 내부 네트워크 전용이며 host에서 직접 접근하지 않는다.  
> Prometheus에 직접 접근하려면 SSH 포트 포워딩(`ssh -L 9410:localhost:9090 <host>`)을 사용한다. Grafana는 Prometheus 데이터를 UI로 제공하므로 대부분의 metrics 조회는 Grafana를 통한다.  
> Infisical은 선택 서비스로 `make infisical-up`으로 별도 기동한다.

---

## Gateway API Endpoint

### 운영 endpoints

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| GET | `/health` | 없음 | Liveness — 프로세스 생존 여부 |
| GET | `/ready` | Admin Bearer | Readiness — 모든 upstream 준비 여부 |
| GET | `/metrics` | Admin Bearer | Prometheus metrics |
| GET | `/docs` | 없음 | Scalar UI |
| GET | `/redoc` | 없음 | ReDoc |
| GET | `/openapi.json` | 없음 | OpenAPI JSON |

### 사용자 API endpoints

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/v1/models` | 노출 모델 catalog. 로딩 상태는 `/ready`에서 확인 |
| POST | `/v1/chat/completions` | Chat completion (`local-main`) |
| POST | `/v1/embeddings` | Embedding 생성 (`local-embed`) |
| POST | `/v1/risk/detectors/prompt/assessments` | Prompt risk signal |
| POST | `/v1/risk/detectors/siren/assessments` | Siren risk signal (retired — 호출 시 410 Gone 반환) |
| POST | `/v1/risk/assessments` | 통합 risk signal |

사용자 API는 `Authorization: Bearer <API_KEY>` 필요.  
Admin endpoints는 `Authorization: Bearer <ADMIN_API_KEY>` 필요.

`/v1/models`는 live readiness를 반영해 모델을 숨기지 않는다. 모델 로딩 중 사용자 traffic을 막는 기준은 `/ready` HTTP status와 `not_ready_dependencies`다.

### `/v1/models` parameter discovery

`/v1/models`는 모델 id와 capability만 반환하지 않는다. 각 item의 `request_parameters`를 통해 사용자가 조정할 수 있는 parameter와 제약 조건을 함께 제공한다.

| 모델 | 조정 가능 | 조정 불가 |
|---|---|---|
| `local-main` | sampling, token limit, seed, stop, `n`(1 고정), tool-call 관련 parameter, `stream`, `stream_options` | runtime/serving 하이퍼파라미터 |
| `local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens` | runtime/serving 하이퍼파라미터 |
| `risk-prompt` | 없음. `prompt` 입력만 받음 | detector sampling parameter는 adapter가 고정 |

클라이언트가 모델 선택 UI를 만든다면 `/v1/models`의 `capabilities`와 `request_parameters`를 함께 사용한다. `fixed_parameters`가 있으면 내부 adapter/runtime이 고정하는 값이므로 사용자 입력 form으로 노출하지 않는다.

---

## 인증 키 확인

`.env` 기준:

```bash
# 사용자 API 키
grep ^API_KEY= .env

# Admin 키 (readiness·metrics 접근용)
grep ^ADMIN_API_KEY= .env
```

curl 예시:

```bash
# Health (인증 없음)
curl http://localhost:9400/health

# Readiness (admin 키 필요)
curl -H "Authorization: Bearer $(grep ^ADMIN_API_KEY= .env | cut -d= -f2)" \
  http://localhost:9400/ready

# Chat completion
curl -H "Authorization: Bearer $(grep ^API_KEY= .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model":"local-main","messages":[{"role":"user","content":"안녕"}]}' \
  http://localhost:9400/v1/chat/completions
```

---

## 모니터링 접근

### Grafana (`http://localhost:9411`)

로그인 정보는 `.env`의 `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`를 확인한다.

```bash
grep -E "^GRAFANA_ADMIN_(USER|PASSWORD)=" .env
```

> `GRAFANA_ADMIN_PASSWORD`는 최초 `make first-run`/`make bootstrap` 또는 `make init-env-compose` 실행 시 한 번 생성되며, 이후 `--force` 재실행에도 변경되지 않는다.  
> 비밀번호가 분실된 경우: Grafana 컨테이너를 force-recreate하면 `.env` 값으로 재설정된다.  
> ```bash
> docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env \
>   up -d --no-deps --force-recreate grafana
> ```

| 대시보드 | uid | 용도 |
|---|---|---|
| GPU 용량 및 OOM 위험 / GPU Capacity and OOM Risk | `gpu_capacity_and_oom_risk` | 기본 home dashboard. GPU 메모리, budget line, utilization, temperature, power, OOM/restart |
| 운영 상황판 / Executive Runtime Overview | `executive_runtime_overview` | 전체 상태, 트래픽, latency, error, GPU headroom, readiness, scrape health |
| local-main Chat API 상세 / Chat API Deep Dive | `chat_api_deep_dive` | `/v1/chat/completions`와 streaming relay 상태 |
| 모델 런타임 상세 / Model Runtime Deep Dive | `model_runtime_deep_dive` | model/runtime_service별 queue, KV cache, throughput, container resource |
| Risk 신호 운영 / Risk Signal Operations | `risk_signal_operations` | Risk signal, detector timeout/error, forbidden field, readiness |

### Prometheus (compose 내부 전용; SSH 포트 포워딩으로 접근)

Prometheus는 compose 내부 네트워크 전용이다. 직접 접근하려면 SSH 포트 포워딩을 사용한다.

```bash
# 175에서 Prometheus에 SSH 포트 포워딩으로 접근하는 예시
ssh -L 9410:localhost:9090 <deploy-host>
# 이후 브라우저에서: http://localhost:9410
```

주요 쿼리 예시:

```promql
# 전체 readiness
max(overall_runtime_status)

# GPU 여유 메모리
gpu_memory_headroom_bytes

# p95 응답 지연
model_runtime_http_p95_latency_seconds

# 5xx 에러율
# Dashboard에서 사람이 최근 window를 읽을 때는 저트래픽 왜곡을 피하기 위해 increase 기반 ratio를 사용한다.
sum(increase(http_requests_total{service="gateway",status_code=~"5.."}[5m]))
  / clamp_min(sum(increase(http_requests_total{service="gateway"}[5m])), 1)
```

Prometheus 자체 `/targets` 페이지에서 scrape 상태를 확인한다 (포트 포워딩 후 `http://localhost:9410/targets`).

---

---

## 시크릿 관리 (Infisical)

Infisical은 선택적 자체 호스팅 시크릿 관리 서비스다. 웹 UI에서 API 토큰·비밀번호를 조회·관리하고 감사 로그를 확인할 수 있다.

```bash
# Infisical 스택 기동
make infisical-up           # → http://localhost:9420

# 초기 설정 가이드
make infisical-init

# .env 시크릿 → Infisical 동기화
make secrets-push           # 전체 동기화
make secrets-push-sensitive # 토큰·비밀번호 등 민감 값만

# Infisical → .env 갱신
make secrets-pull

# 현재 동기화 상태 확인
make secrets-status
```

Infisical 없이도 `.env`만으로 정상 운영된다. `make first-run`/`make bootstrap` 실행 시 Infisical이 설정되어 있으면 자동으로 push까지 처리한다.

---

## 빠른 상태 확인

```bash
# 서비스 전체 상태 (Make)
make status

# Gateway health 직접 확인
curl -s http://localhost:9400/health | python3 -m json.tool

# 전체 readiness (admin 키 자동 주입)
make ready
```


## Streaming 운영 참고

`stream=true`는 SSE fast path입니다. 표준 OpenAI chunk는 `object: "chat.completion.chunk"`, `choices[].delta`, `finish_reason`을 포함하며 마지막에는 `data: [DONE]`이 옵니다. `stream_options.include_usage=true`는 `stream=true`와 함께 사용할 때 upstream이 지원하는 최종 usage chunk를 요청하고 Gateway는 이를 수정하지 않고 relay합니다. Proxy buffering, 중간 실패 SSE error event, usage accounting, timeout tuning은 [streaming_runtime_operations.md](streaming_runtime_operations.md)를 따릅니다.


> Grafana dashboard source of truth는 `ops/grafana/dashboards/*.json`이다. Reference release에서는 `allowUiUpdates=false`로 Git-managed dashboard를 유지한다. UI에서 직접 수정한 내용은 JSON source로 자동 반영되지 않는다.
