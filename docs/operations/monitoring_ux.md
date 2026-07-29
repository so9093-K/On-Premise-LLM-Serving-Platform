# 운영 모니터링 UX

모니터링은 기본 활성화한다. 초기 운영 단계에서는 보이지 않는 것이 더 큰 위험이므로 Prometheus, Grafana, DCGM exporter, cAdvisor를 compose reference에 포함한다.

메인 모델 선택 상태(active profile/revision/runtime image/compatibility, request
gate, latest operation state, switch·rollback 누적 결과, 마지막 전환 시간)는 Gateway
Prometheus metric(`main_model_operation_state` 등)으로 확인한다. operation ID와 오류
문자열은 metric label로 사용하지 않는다.

| UI/endpoint | 기본 포트 | 용도 |
|---|---:|---|
| Prometheus | 9410 | scrape target과 metric 확인 |
| Grafana | 9411 | 운영 dashboard 확인 |
| DCGM exporter | 9412 | GPU metric 확인 |
| cAdvisor | 9413 | 컨테이너별 CPU/RAM metric 확인 |
| Gateway `/metrics` | 9400 | Gateway Prometheus metric |
| Risk Adapter `/metrics` | 9405 | Risk Adapter Prometheus metric |

## 원칙

- prompt, generated text, raw input은 metric label에 넣지 않는다.
- client IP는 Prometheus metric label에 넣지 않는다. abuse/rate-limit 분석은 access log의 `client_host`, `request_id`를 기준으로 수행한다. (`client_ip_hash`/`forwarded_for_present`/`forwarded_proto`는 이 배포에 reverse proxy가 없어 항상 같은 값만 찍히던 죽은 필드라 제거함 — Grafana `Request Log Explorer` 대시보드 정비 중 확인)
- metric name과 label value는 영어/ASCII를 유지한다.
- dashboard와 panel 제목은 영어를 사용하되 metric 이름보다 운영자가 판단하는 언어를 우선한다. 운영 문서는 한글 우선으로 작성하고, 내부 metric 용어는 panel description과 운영 문서에 둔다.
- no-data panel은 exporter가 없거나 metric mapping이 없을 때만 허용한다.
- Grafana 단일 운영 dashboard는 `gpu_capacity_and_oom_risk`이며 GPU headroom, VRAM, utilization, OOM/restart, KV cache를 바로 보여준다. 여기의 GPU Headroom/GPU Memory Used는 DCGM exporter 기반 live observed metric이며, configured budget/projection은 operator status bundle에서 확인한다.
- user traffic panel은 기본적으로 `/v1/chat/completions|/v1/embeddings|/v1/risk/.*`만 포함하고 `/health`, `/ready`, `/v1/models`, `/metrics`, `/docs`, `/openapi.json` 같은 control/observability/docs route를 제외한다.
- `User Requests in Window`는 public entrypoint 기준이므로 `service="gateway"`만 사용한다. `Request Rate by Service/Route`는 gateway public activity와 risk-adapter backend activity를 service label로 분리해서 보여주며, 두 값은 같지 않을 수 있다.
- 각 dashboard 상단에는 긴 guide/runbook 대신 1-row snapshot text panel만 둔다. 판단은 패널 제목, 값, 색, 배치가 하며 자세한 설명은 panel description과 운영 문서로 내린다.
- 첫 화면은 snapshot cards와 핵심 원인 패널만 둔다. Streaming, retrieval, embedding, risk distribution, derived monitoring signal 같은 상세 분석 패널은 collapsed row로 내린다.
- admin endpoint와 monitoring port는 token 또는 private network로 보호한다.

## Grafana dashboard 구성

| Dashboard | 목적 | 주요 사용자 |
|---|---|---|
| `gpu_capacity_and_oom_risk` | **안전 dashboard.** live GPU headroom, observed VRAM, utilization, OOM/restart, KV cache, queue | operator/runtime engineer |
| `usage_today` | **사용량 dashboard (glanceable).** GPU 가동, 모델별 요청 비중, 거부 요청, input/output 토큰 처리량, 모델별 요청 추세 — 판단이 색/제목에 박혀 가이드 없이 읽힘 | operator |

Dashboard JSON은 `ops/grafana/dashboards/*.json`에서 관리한다. `configs/monitoring.yaml`의 `dashboard_variables`는 monitoring projection용 공통 목록이다.

공통 dashboard variable:

| Variable | 용도 | 대상 |
|---|---|---|
| `$datasource` | Prometheus datasource portability | 전체 |
| `$window` | `1m`, `5m`, `15m`, `1h` 조회 window | 전체 |
| `$model` | `local-main`, `local-embed`, `risk-prompt` | gpu_capacity_and_oom_risk |
| `$runtime_service` | compose runtime service filter | gpu_capacity_and_oom_risk |

Prometheus target health의 expected critical target count는 `gateway + risk-adapter + vLLM runtime targets + dcgm-exporter + cadvisor`로 계산한다. runtime target 수가 바뀌면 `monitoring_projection`의 `expected_critical_target_count`가 함께 바뀐다.

## PromQL rate 계산 정책

저트래픽 dashboard에서 error ratio를 사람이 읽을 때는 `rate()` denominator를 `1`로 clamp하지 않는다. 초당 요청량이 1보다 낮은 서비스에서는 실제 오류율이 낮게 보일 수 있기 때문이다.

권장 dashboard query:

```promql
sum(increase(http_requests_total{service="gateway",status_code=~"5.."}[$window]))
/
clamp_min(sum(increase(http_requests_total{service="gateway"}[$window])), 1)
```

Risk detected ratio도 같은 원칙을 따른다.

```promql
sum(increase(risk_signal_detected_total[$window]))
/
clamp_min(sum(increase(risk_assessments_total[$window])), 1)
```

Risk detector 실패는 HTTP 200 응답 안의 system signal로도 반환될 수 있어 `status_code >= 400` 패널만으로는 보이지 않는다. 대시보드에는 아래 두 쿼리를 별도 오류 패널로 둔다.

```promql
sum by (detector, status) (increase(risk_assessments_total{status=~"failed|partial"}[$window]))
sum by (system_signal_code) (increase(risk_adapter_system_signal_total[$window]))
```

Recording rule은 장기 trend와 alert rule에서 `rate()`를 계속 사용할 수 있다. Dashboard는 운영자가 최근 window 내 이벤트 수를 직관적으로 읽는 것을 우선한다.

## 모델별 vLLM 관측성

Prometheus는 enabled vLLM runtime을 하나의 `vllm-runtimes` job으로 scrape하고, 각 target에 `model`과 `runtime_service` label을 붙인다. `job`을 모델별로 나누지 않고 label로 분리하면 dashboard와 alert rule을 같은 쿼리 구조로 유지할 수 있다.

| 모델 | Runtime service | Prometheus target |
|---|---|---|
| `local-main` | `main-llm-vllm` | `main-llm-vllm:9401` |
| `local-embed` | `embedding-vllm` | `embedding-vllm:9402` |
| `local-embed-ko` | `embedding-ko-vllm` | `embedding-ko-vllm:9406` |
| `risk-prompt` | `risk-prompt-vllm` | `risk-prompt-vllm:9403` |

대표 쿼리는 다음과 같다.

```promql
vllm_kv_cache_usage_ratio{model=~"$model",runtime_service=~"$runtime_service"}
vllm_queue_depth{model=~"$model",runtime_service=~"$runtime_service"}
vllm_token_throughput_per_second{model=~"$model",runtime_service=~"$runtime_service"}
vllm_container_memory_usage_bytes{container_label_com_docker_compose_service=~"$runtime_service"}
vllm_container_cpu_cores_used{container_label_com_docker_compose_service=~"$runtime_service"}
```

`vllm_token_throughput_per_second`는 request operations가 아니라 prompt+generation token/sec이다. `vllm_container_cpu_cores_used`는 host 전체 CPU percentage가 아니라 사용 중인 CPU core 수이다. DCGM exporter는 단일 GPU의 전체 VRAM, 온도, 전력, utilization을 본다. 모델별 실제 작업량과 병목은 vLLM metric과 cAdvisor recording rule을 우선 확인한다.

## Streaming fast path metric

`stream=true` chat 경로는 다음 metric으로 chunk relay, timing, accounting 상태를 확인한다. prompt와 generated text는 label에 포함하지 않는다. 상태 label은 `status`를 사용한다 (`result`가 아님).

```text
streaming_chunks_total{service="gateway",target="local-main"}
streaming_bytes_total{service="gateway",target="local-main"}
streaming_usage_events_total{service="gateway",target="local-main"}
streaming_errors_total{service="gateway",target="local-main",code="UPSTREAM_TIMEOUT",phase="mid_stream"}
streaming_requests_total{service="gateway",target="local-main",status="started"}
streaming_time_to_first_chunk_seconds_bucket{service="gateway",target="local-main",le="0.5"}
streaming_duration_seconds_bucket{service="gateway",target="local-main",status="completed",le="30.0"}
streaming_chunks_per_response_bucket{service="gateway",target="local-main",status="completed",le="100"}
streaming_client_disconnects_total{service="gateway",target="local-main",phase="before_first_chunk"}
```

Gateway streaming metric은 위 값을 사용해 다음을 계산한다.

- **Streaming Chunk Rate**: `streaming_chunks_total` rate
- **Streaming Byte Throughput**: `streaming_bytes_total` rate
- **Streaming Time to First Token p95**: `histogram_quantile(0.95, streaming_time_to_first_chunk_seconds_bucket)`
- **Streaming Duration p95**: `histogram_quantile(0.95, streaming_duration_seconds_bucket)`
- **Chunks per Response p95**: `histogram_quantile(0.95, streaming_chunks_per_response_bucket)`

`status` label 값: `started`, `completed`, `error`, `client_disconnect` (terminal category only; prompt/generated text는 포함하지 않음).

## OOM/restart metric source

`GPU Capacity and OOM Risk`의 OOM/restart panel은 reference package 내부에서 정의되지 않은 site-specific `backend_restart_total` 또는 `gpu_oom_events_total`을 사용하지 않는다. 대신 cAdvisor source metric을 직접 사용한다.

| Signal | Query source | 해석 |
|---|---|---|
| Container OOM events | `container_oom_events_total{container_label_com_docker_compose_service=~"gateway|risk-adapter|main-llm-vllm|embedding-vllm|embedding-ko-vllm|risk-prompt-vllm"}` | 컨테이너 OOM event counter. No Data면 cAdvisor source metric 부재를 의미할 수 있다. |
| Container restart signals | `changes(container_start_time_seconds{container_label_com_docker_compose_service=~"gateway|risk-adapter|main-llm-vllm|embedding-vllm|embedding-ko-vllm|risk-prompt-vllm"}[$window])` | 선택 window 내 container start time 변화 수. 재시작 counter가 아니라 start-time change signal이다. |

운영자는 이 panel의 No Data를 0으로 해석하면 안 된다. 먼저 Prometheus target 상태와 cAdvisor scrape 상태를 확인한다.

## No Data vs 0 구분 정책

| 상황 | 표시 | 해석 |
|---|---|---|
| metric이 등록되어 있고 이벤트가 없음 | 0 | 정상. 이벤트 없음 |
| exporter/metric 자체가 없음 | No Data | scrape 문제. Prometheus target 상태 확인 |
| or vector(0)로 강제된 0 | 0 | 주의: exporter 부재를 숨길 수 있음 |

- Container OOM / Restart Signals: cAdvisor의 `container_oom_events_total`과 `container_start_time_seconds`를 사용한다. No Data는 cAdvisor scrape 또는 metric source 부재일 수 있으므로 0으로 대체하지 않는다.
- Forbidden response fields: `forbidden_response_field_total`은 risk-adapter 기동 시 항상 등록되므로 risk-adapter가 up이면 0은 "위반 없음"을 의미한다.
- **원칙**: No Data가 운영상 더 안전한 panel은 `or vector(0)`로 강제하지 않는다.

## Version/Build/Runtime info backlog

현재 dashboard에는 다음 정보가 없다. 향후 metric 추가 후 panel로 제공할 수 있다.

```text
gateway_build_info{version, commit, image}
model_runtime_info{model, runtime_service, image, vllm_version}
model_catalog_info{model, revision, quantization}
```

백로그 포함 정보: Gateway image/version/commit, vLLM image/version, served model name, model revision, GPU name, max_model_len, max_num_seqs, tuned config 적용 여부.

## Instrumentation backlog

이번 변경에서는 기존 metric label schema를 바꾸지 않는다. 다음 metric은 향후 관측성을 더 정확하게 만들기 위한 backlog다.

```text
ai_gateway_active_requests{route,model}
ai_runtime_active_requests{model,runtime_service}
ai_gateway_last_user_request_timestamp_seconds{route,model}
ai_synthetic_probe_success{probe,model}
ai_synthetic_probe_duration_seconds_bucket{probe,model}
ai_synthetic_probe_last_success_timestamp_seconds{probe,model}
ai_readiness_last_checked_timestamp_seconds{service}
ai_model_ready{model,runtime_service}
ai_critical_metric_freshness_seconds{metric,job}
```

`service_readiness_status`와 `overall_runtime_status`는 `/ready` 호출 시점에 갱신되는 readiness evidence다. warm residency를 더 강하게 신뢰하려면 readiness freshness(`ai_readiness_last_checked_timestamp_seconds`) 또는 synthetic probe metric이 필요하다.

## Live PromQL validation

`scripts/validation/validate_grafana_promql.py`는 dashboard JSON에서 PromQL을 추출하고 Prometheus `/api/v1/query`로 syntax check를 수행하는 선택적 runtime validation 도구다. live Prometheus가 필요하므로 기본 CI gate가 아니라 optional runtime validation으로 실행한다.

```bash
python3 scripts/validation/validate_grafana_promql.py \
  --allow-no-data
```

기본 URL은 `configs/services.yaml`의 Prometheus host 포트를 사용한다. 다른 환경은
`PROMETHEUS_BASE_URL` 또는 `--prometheus-url`로 명시한다.

Idle/dev 환경에서는 traffic이 없어 일부 panel query가 no-data를 반환할 수 있으므로 `--allow-no-data`로 datasource 연결과 PromQL syntax를 먼저 확인한다.
운영 traffic이 있는 환경에서 no-data까지 실패로 보고 싶으면 `--allow-no-data` 없이 실행한다.

## Provisioning 정책

Reference release의 Grafana dashboard는 Git-managed artifact다.

- `ops/grafana/provisioning/datasources/prometheus.yml`는 datasource UID를 `prometheus`로 고정한다.
- compose는 `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/gpu_capacity_and_oom_risk.json`으로 Grafana home dashboard를 고정한다.
- dashboard panel은 `$datasource` variable을 통해 Prometheus datasource를 참조한다.
- reference release에서는 `allowUiUpdates: false`를 사용한다.
- local 실험이 필요하면 별도 local override 또는 exported JSON을 사용한다.

Grafana UI에서 저장한 provisioned dashboard 변경은 JSON source로 자동 반영되지 않는다. 따라서 운영 기준 dashboard는 **repository JSON (`ops/grafana/dashboards/*.json`)을 source of truth**로 둔다. UI에서 수정한 내용을 운영에 반영하려면 JSON을 export하고 repository에 커밋한 뒤 Grafana를 재시작해야 한다. live datasource/render validation은 별도 runtime check (`make runtime-validate`)이며 기본 CI merge gate가 아니다.

## Dashboard phrase 기준

- No prompt leakage: prompt와 generated text는 metric label과 dashboard에 노출하지 않는다.
- No Runtime Data: exporter 또는 scrape가 없을 때 gray 상태로 표시한다.
- Healthy / Attention / Action Required: 모든 panel description은 정상, 주의, 조치 필요, No Data 해석을 포함한다.

## Request Log Explorer (Loki 기반 로그 대시보드)

`ops/grafana/dashboards/request_log_explorer.json`은 Prometheus 집계가 아니라 Loki 원본
로그를 요청 단위로 조회하는 대시보드다. `gpu_capacity_and_oom_risk`/`usage_today`와 달리
panel description은 운영자가 화면에서 바로 훑을 수 있는 짧은 요약만 담고, 설계 근거는
여기 문서에 둔다(패널 description을 문단형 근거 설명으로 채우면 좁은 hover 툴팁에서 아무도
안 읽는다는 걸 실사용 리뷰로 확인함).

**패널 구성**

- `Gateway Request Log`: gateway/risk-adapter 구조화 JSON 로그 전체 요청.
- `Errors (status_code >= 400)`: 위와 같은 소스에서 4xx/5xx만.
- `Non-JSON Container Errors`: vLLM 등 uvicorn access log(JSON 아님)의 4xx/5xx를 텍스트
  파싱해서 본다.
- `Raw Container Log`: 파싱 없는 원본 전체.

**request_id/error_code/error_message가 `Gateway Request Log`엔 없고 `Errors`에만 있는 이유**

`X-Request-Id`/`X-Error-Code`/`X-Error-Message` 응답 헤더는 에러 응답에서만 채워진다
(`errors.py`의 `error_response_headers`). 200 성공 응답은 클라이언트가 직접
`x-request-id`를 보내지 않는 한 이 값들이 항상 비어있다(`logging_policy.py`). 전체 요청을
보여주는 `Gateway Request Log`에 이 세 컬럼을 넣으면 대부분 빈 칸이라, 실제로 채워지는
`Errors` 패널에만 남겼다.

**`/metrics`, `/health`를 `route_filter` 기본값이 아니라 쿼리에 고정 제외한 이유**

Loki(LogQL)의 정규식 엔진은 Go RE2라 부정 lookahead(`(?!...)`)를 지원하지 않는다. 그래서
"route_filter 기본값으로 이 두 라우트만 제외"는 애초에 못 만든다. 대신 쿼리에
`| route != "/metrics" | route != "/health"`를 고정 조건으로 추가했다 — `route_filter`
자체는 그대로 편집 가능하고, 이 두 라우트가 실패하면 status_code와 무관하게 `Errors`
패널에서 그대로 잡힌다.

**`Non-JSON Container Errors`의 RegExp 포맷: 슬래시로 감싸야 한다**

Grafana "Extract fields" 트랜스폼의 RegExp 포맷은 정규식을 `/pattern/`처럼 슬래시로 감싸야
named capturing group(`(?<name>...)`)을 인식한다. 슬래시 없이 넣으면 파싱이 조용히 깨져서
모든 필드가 `NewField`라는 이름 하나로 합쳐진 채 원본 Line 전체가 복사된다(에러 메시지 없이
실패하므로 스크래치 환경에서 직접 렌더링을 확인하지 않으면 알아채기 어렵다).

**`route`/`error_code`는 Loki 라벨이 아니라서 드롭다운으로 못 바꾼다**

두 필드 다 `| json`으로 로그 내용에서 그때그때 뽑아내는 파생 필드지, Loki가 인덱싱한 실제
라벨이 아니다(`/loki/api/v1/labels`로 확인 가능한 라벨은 `container_id`, `filename`,
`job`, `service_name`, `stream`뿐). Grafana 대시보드 변수 편집기의 "Label values" 쿼리
타입은 실제 인덱싱된 라벨만 선택지로 보여주므로(직접 만들어서 확인함), route/error_code를
드롭다운 변수로 만들려면 Promtail 쪽에서 이 필드들을 실제 라벨로 승격시키는 pipeline stage
변경이 필요하다 — 저카디널리티라 안전할 가능성이 높지만 스트림 분할(카디널리티) 영향을
별도로 검토해야 하는 인프라 변경이라 보류 중이다. 지금은 `route_filter`/`error_code_filter`
모두 자유 입력 regex textbox다.

**`container_id`는 전용 변수가 아니라 Grafana 기본 ad-hoc filter로 좁힌다**

`Raw Container Log` 패널에서 로그 줄을 펼쳐 `container_id` 필드 옆 돋보기(+) 아이콘을
누르면 모든 패널에 공통 적용되는 ad-hoc filter로 좁혀진다. 전용 template 변수를 따로 두지
않는다 — 같은 라벨에 서로 다른 값을 요구하는 ad-hoc filter와 전용 변수가 동시에 걸리면
충돌해서 아무것도 안 보이는 문제가 있었다(운영 중 실제로 발생, 수정 완료). `Non-JSON
Container Errors`는 테이블 타입으로 바뀌면서 이 돋보기 아이콘 자체가 없어져,
container_id로 좁히는 진입점은 `Raw Container Log` 하나뿐이다. docker.sock을 쓰지 않아서
컨테이너 이름이 아니라 container_id(전체 해시)로만 구분 가능하다.

## Monitoring Projection 흐름

ModelRegistry와 monitoring config에서 Prometheus/Grafana 운영 산출물을 자동 생성한다. 운영자가 dashboard 설정을 수동으로 맞추지 않도록, 다음 값을 projection한다.

- Prometheus scrape job
- vLLM runtime label
- recording rule 기대값
- Grafana variable 값
- operator status bundle의 monitoring summary

```bash
make monitoring-projection
```

| 산출물 | 경로 |
|---|---|
| 기계가 읽는 projection | `reports/runtime/monitoring_projection.json` |
| 운영자가 읽는 요약 | `reports/runtime/monitoring_projection.md` |

`monitoring_projection`은 `operator_status_bundle`과 `live_evidence_bundle`의 입력 중 하나다. 전체 운영 산출물을 갱신하려면 `make operator-reports`를 사용한다. report에는 원문 prompt, 사용자 텍스트, 모델 출력, Authorization header, secret 값을 포함하지 않는다.
