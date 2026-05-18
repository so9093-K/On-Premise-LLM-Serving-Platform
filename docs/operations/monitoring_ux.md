# 운영 모니터링 UX

모니터링은 기본 활성화한다. 초기 운영 단계에서는 보이지 않는 것이 더 큰 위험이므로 Prometheus, Grafana, DCGM exporter, cAdvisor를 compose reference에 포함한다.

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
- metric name과 label value는 영어/ASCII를 유지한다.
- dashboard와 panel 제목은 영어를 사용하고, operator guide 본문과 운영 문서는 한글 우선 + 영어 metric 용어 병기를 사용한다.
- no-data panel은 exporter가 없거나 metric mapping이 없을 때만 허용한다.
- Grafana 첫 화면은 `gpu_capacity_and_oom_risk`이며 GPU headroom, VRAM, utilization, OOM/restart, KV cache를 바로 보여준다. 상세 트리아지(verdict banner, evidence cards, Needs Attention table)는 `serving_home` drill-down에서 확인한다.
- user traffic panel은 기본적으로 `/v1/chat/completions|/v1/embeddings|/v1/risk/.*`만 포함하고 `/health`, `/ready`, `/v1/models`, `/metrics`, `/docs`, `/openapi.json` 같은 control/observability/docs route를 제외한다.
- `User Requests in Window`는 public entrypoint 기준이므로 `service="gateway"`만 사용한다. `Request Rate by Service/Route`는 gateway public activity와 risk-adapter backend activity를 service label로 분리해서 보여주며, 두 값은 같지 않을 수 있다.
- 각 dashboard 상단에는 tooltip을 열지 않아도 읽히는 Text panel runbook을 둔다.
- admin endpoint와 monitoring port는 token 또는 private network로 보호한다.

## Grafana dashboard 구성

| Dashboard | 목적 | 주요 사용자 |
|---|---|---|
| `gpu_capacity_and_oom_risk` | **기본 home dashboard.** GPU headroom, VRAM budget, utilization, OOM/restart, KV cache, queue | operator/runtime engineer |
| `serving_home` | Verdict banner, Evidence cards, Needs Attention triage, GPU warm, drill-down routing | operator |
| `executive_runtime_overview` | 전체 상태, 트래픽, latency, error, GPU headroom, scrape health | reviewer/operator |
| `api_experience` | `/v1/chat/completions`, `/v1/embeddings`, streaming relay, TTFT, duration histograms | gateway/runtime engineer |
| `model_runtime_deep_dive` | model/runtime_service별 queue, KV cache, throughput, container resource | runtime engineer |
| `risk_signal_operations` | signal-only risk monitoring | safety/policy reviewer |
| `observability_data_quality` | scrape target visibility, up vs absent 구분, recording rule health, vLLM metric availability | operator/platform engineer |

Dashboard JSON은 `ops/grafana/dashboards/*.json`에서 관리한다. 공통 dashboard variable과 Serving Home 전용 variable을 구분한다. `configs/monitoring.yaml`의 `dashboard_variables`는 monitoring projection용 Home-inclusive 목록이며, 모든 dashboard가 `$user_route`를 갖는다는 뜻은 아니다.

공통 dashboard variable:

| Variable | 용도 |
|---|---|
| `$datasource` | Prometheus datasource portability |
| `$window` | `1m`, `5m`, `15m`, `1h` 조회 window |
| `$model` | `local-main`, `local-embed`, `risk-prompt` |
| `$runtime_service` | compose runtime service filter |
| `$route` | Gateway route filter |
| `$status_code` | HTTP status filter |

Serving Home 전용 variable:

| Variable | 용도 |
|---|---|
| `$user_route` | Serving Home user traffic regex. 선택지는 All user routes, Chat, Embeddings, Risk이며 기본값은 `/v1/chat/completions|/v1/embeddings|/v1/risk/.*` |

## Serving mode 정의

`serving_home`은 user traffic이 없는 상태를 장애나 빈 화면으로 보이지 않게 하기 위해 다음 mode를 사용한다. `ai_serving_verdict_code` recording rule로 verdict를 자동 계산하며, readiness, scrape, GPU, OOM/restart evidence를 priority chain으로 조합한다.

Prometheus target health의 expected critical target count는 `gateway + risk-adapter + vLLM runtime targets + dcgm-exporter + cadvisor`로 계산한다. 현재 reference topology에서는 이 값이 7이지만, runtime target 수가 바뀌면 `monitoring_projection`의 `expected_critical_target_count`와 dashboard validation이 함께 바뀌어야 한다.

| Mode | 의미 |
|---|---|
| `ACTIVE` | user request activity가 있고 readiness, scrape, GPU headroom, OOM/restart evidence가 정상이다. |
| `IDLE WARM` | user traffic은 없지만 runtime readiness/scrape/GPU headroom/OOM-restart evidence가 stack이 warm and ready임을 보여준다. |
| `IDLE COLD` | user traffic이 없고 runtime readiness, scrape, GPU warm residency 중 하나 이상이 비어 있다. |
| `DEGRADED` | readiness, scrape, latency, error, GPU, OOM/restart, capacity chain 중 하나 이상이 주의 또는 조치 필요 상태다. |
| `NO DATA` | Prometheus scrape 또는 metric mapping이 없어 evidence chain을 신뢰할 수 없다. 숫자 0과 다르게 취급한다. |

Traffic class는 다음처럼 분리한다.

| Class | Route |
|---|---|
| user | `/v1/chat/completions`, `/v1/embeddings`, `/v1/risk/.*` |
| control | `/health`, `/ready`, `/v1/models` |
| observability | `/metrics` |
| docs | `/docs`, `/openapi.json` |

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

Recording rule은 장기 trend와 alert rule에서 `rate()`를 계속 사용할 수 있다. Dashboard는 운영자가 최근 window 내 이벤트 수를 직관적으로 읽는 것을 우선한다.

## 모델별 vLLM 관측성

Prometheus는 enabled vLLM runtime을 하나의 `vllm-runtimes` job으로 scrape하고, 각 target에 `model`과 `runtime_service` label을 붙인다. `job`을 모델별로 나누지 않고 label로 분리하면 dashboard와 alert rule을 같은 쿼리 구조로 유지할 수 있다.

| 모델 | Runtime service | Prometheus target |
|---|---|---|
| `local-main` | `main-llm-vllm` | `main-llm-vllm:9401` |
| `local-embed` | `embedding-vllm` | `embedding-vllm:9402` |
| `risk-prompt` | `risk-prompt-vllm` | `risk-prompt-vllm:9403` |

대표 쿼리는 다음과 같다.

```promql
vllm_kv_cache_usage_ratio{model=~"$model",runtime_service=~"$runtime_service"}
vllm_queue_depth{model=~"$model",runtime_service=~"$runtime_service"}
vllm_token_throughput_per_second{model=~"$model",runtime_service=~"$runtime_service"}
vllm_container_memory_usage_bytes{container_label_com_docker_compose_service=~"$runtime_service"}
vllm_container_cpu_usage_ratio{container_label_com_docker_compose_service=~"$runtime_service"}
```

DCGM exporter는 단일 GPU의 전체 VRAM, 온도, 전력, utilization을 본다. 모델별 실제 작업량과 병목은 vLLM metric과 cAdvisor recording rule을 우선 확인한다.

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

`api_experience` dashboard는 위 metric을 사용해 다음을 제공한다.

- **Streaming Chunk Rate**: `streaming_chunks_total` rate
- **Streaming Byte Throughput**: `streaming_bytes_total` rate
- **Streaming Time to First Token p95**: `histogram_quantile(0.95, streaming_time_to_first_chunk_seconds_bucket)`
- **Streaming Duration p95**: `histogram_quantile(0.95, streaming_duration_seconds_bucket)`
- **Chunks per Response p95**: `histogram_quantile(0.95, streaming_chunks_per_response_bucket)`

`status` label 값: `started`, `completed`, `error`, `client_disconnect` (terminal category only; prompt/generated text는 포함하지 않음).

## Dashboard navigation 흐름

각 dashboard에는 Grafana 상단 링크로 관련 dashboard 이동 버튼이 있다. `includeVars=true`로 현재 variable 값을 유지하며 이동한다. 이 표는 dashboard JSON link graph와 일치해야 한다.

| 출발 dashboard | 이동 대상 |
|---|---|
| `serving_home` | `gpu_capacity_and_oom_risk`, `executive_runtime_overview`, `api_experience`, `model_runtime_deep_dive`, `risk_signal_operations`, `observability_data_quality` |
| `gpu_capacity_and_oom_risk` | `serving_home`, `executive_runtime_overview` |
| `executive_runtime_overview` | `serving_home`, `gpu_capacity_and_oom_risk`, `api_experience`, `model_runtime_deep_dive`, `risk_signal_operations` |
| `api_experience` | `serving_home`, `executive_runtime_overview`, `model_runtime_deep_dive` |
| `model_runtime_deep_dive` | `serving_home`, `gpu_capacity_and_oom_risk`, `api_experience` |
| `risk_signal_operations` | `serving_home`, `executive_runtime_overview` |
| `observability_data_quality` | `serving_home`, `executive_runtime_overview` |

권장 drill-down 순서: Serving Home → GPU Capacity/OOM → Executive Overview → API Experience → Model Runtime Deep Dive → Risk Signal Operations → Observability Data Quality

## OOM/restart metric source

`Serving Home`과 `GPU Capacity and OOM Risk`의 OOM/restart panel은 reference package 내부에서 정의되지 않은 site-specific `backend_restart_total` 또는 `gpu_oom_events_total`을 사용하지 않는다. 대신 cAdvisor source metric을 직접 사용한다.

| Signal | Query source | 해석 |
|---|---|---|
| Container OOM events | `container_oom_events_total{container_label_com_docker_compose_service=~"gateway|risk-adapter|main-llm-vllm|embedding-vllm|colbert-ko-vllm|risk-prompt-vllm"}` | 컨테이너 OOM event counter. No Data면 cAdvisor source metric 부재를 의미할 수 있다. |
| Container restart signals | `changes(container_start_time_seconds{container_label_com_docker_compose_service=~"gateway|risk-adapter|main-llm-vllm|embedding-vllm|colbert-ko-vllm|risk-prompt-vllm"}[$window])` | 선택 window 내 container start time 변화 수. 재시작 counter가 아니라 start-time change signal이다. |

운영자는 이 panel의 No Data를 0으로 해석하면 안 된다. 먼저 `Prometheus Target Health`와 cAdvisor scrape 상태를 확인한다.

## No Data vs 0 구분 정책

| 상황 | 표시 | 해석 |
|---|---|---|
| metric이 등록되어 있고 이벤트가 없음 | 0 | 정상. 이벤트 없음 |
| exporter/metric 자체가 없음 | No Data | scrape 문제. Scrape Health panel 확인 |
| or vector(0)로 강제된 0 | 0 | 주의: exporter 부재를 숨길 수 있음 |

- Container OOM / Restart Signals: cAdvisor의 `container_oom_events_total`과 `container_start_time_seconds`를 사용한다. No Data는 cAdvisor scrape 또는 metric source 부재일 수 있으므로 0으로 대체하지 않는다.
- Forbidden Field Violations: `forbidden_response_field_total`은 risk-adapter 기동 시 항상 등록되므로 risk-adapter가 up이면 0은 "위반 없음"을 의미한다.
- **원칙**: No Data가 운영상 더 안전한 panel은 `or vector(0)`로 강제하지 않는다.

## Fixed window recording rule 사용 panel

다음 panel은 recording rule이 5m 고정 window를 사용하므로 `$window` variable 변경에 영향받지 않는다.

| Panel | Dashboard | Recording rule | Window |
|---|---|---|---|
| `p95 Latency (5m)` | `executive_runtime_overview` | `model_runtime_http_p95_latency_seconds` | 5m 고정 |
| `Upstream p95 Latency (5m)` | `model_runtime_deep_dive` | `model_runtime_upstream_p95_latency_seconds` | 5m 고정 |

panel title에 `(5m)`을 표시하여 `$window` 선택과 무관함을 명시한다. 더 짧거나 긴 window가 필요하면 recording rule 대신 raw histogram query를 사용한다.

## Version/Build/Runtime info backlog

현재 dashboard에는 다음 정보가 없다. 향후 metric 추가 후 panel로 제공할 수 있다.

```text
gateway_build_info{version, commit, image}
model_runtime_info{model, runtime_service, image, vllm_version}
model_catalog_info{model, revision, quantization}
```

백로그 포함 정보: Gateway image/version/commit, vLLM image/version, served model name, model revision, GPU name, max_model_len, max_num_seqs, tuned config 적용 여부.

## Serving Home instrumentation backlog

이번 1차 변경에서는 기존 metric label schema를 바꾸지 않는다. 다음 metric은 dashboard UX를 더 정확하게 만들기 위한 backlog다.

```text
ai_gateway_active_requests{route,model}
ai_runtime_active_requests{model,runtime_service}
ai_gateway_last_user_request_timestamp_seconds{route,model}
ai_synthetic_probe_success{probe,model}
ai_synthetic_probe_duration_seconds_bucket{probe,model}
ai_synthetic_probe_last_success_timestamp_seconds{probe,model}
ai_readiness_last_checked_timestamp_seconds{service}
ai_model_ready{model,runtime_service}
ai_serving_mode
ai_critical_metric_freshness_seconds{metric,job}
```

`service_readiness_status`와 `overall_runtime_status`는 `/ready` 호출 시점에 갱신되는 readiness evidence다. `Serving Home`의 GPU Warm State Evidence 섹션은 readiness/scrape evidence이지 아직 full serving mode metric이 아니다. IDLE WARM을 더 강하게 신뢰하려면 readiness freshness(`ai_readiness_last_checked_timestamp_seconds`) 또는 synthetic probe metric이 필요하다.

현재 단계에서 IDLE WARM은 "readiness and scrape evidence exists"이지 “fresh active probe succeeded”가 아니다 (verdict code 3).

## Live PromQL validation

`scripts/validation/validate_grafana_promql.py`는 dashboard JSON에서 PromQL을 추출하고 Prometheus `/api/v1/query`로 syntax check를 수행하는 선택적 runtime validation 도구다. live Prometheus가 필요하므로 기본 CI gate가 아니라 optional runtime validation으로 실행한다.

```bash
python3 scripts/validation/validate_grafana_promql.py \
  --prometheus-url http://localhost:9410 \
  --allow-no-data
```

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
- Risk Signal Health: Risk Adapter와 detector별 signal 상태를 별도 panel로 본다.
- No Runtime Data: exporter 또는 scrape가 없을 때 gray 상태로 표시한다.
- Healthy / Attention / Action Required: 모든 panel description은 정상, 주의, 조치 필요, No Data 해석을 포함한다.

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
