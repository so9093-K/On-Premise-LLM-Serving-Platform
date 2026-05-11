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
- Grafana 첫 화면은 `gpu_capacity_and_oom_risk`이며 GPU headroom, VRAM, utilization, OOM/restart, queue, KV cache 압력을 먼저 보여준다.
- 각 dashboard 상단에는 tooltip을 열지 않아도 읽히는 Text panel runbook을 둔다.
- admin endpoint와 monitoring port는 token 또는 private network로 보호한다.

## Grafana dashboard 구성

| Dashboard | 목적 | 주요 사용자 |
|---|---|---|
| `gpu_capacity_and_oom_risk` | 기본 home dashboard. 단일 GPU 용량, VRAM budget, utilization, OOM/restart, queue, KV cache | runtime engineer |
| `executive_runtime_overview` | 전체 상태, 트래픽, latency, error, GPU headroom, scrape health | reviewer/operator |
| `chat_api_deep_dive` | `/v1/chat/completions`와 streaming relay 상태 | gateway/runtime engineer |
| `model_runtime_deep_dive` | model/runtime_service별 queue, KV cache, throughput, container resource | runtime engineer |
| `risk_signal_operations` | signal-only risk monitoring | safety/policy reviewer |

Dashboard JSON은 `ops/grafana/dashboards/*.json`에서 관리한다. 모든 dashboard는 다음 variable을 제공한다.

| Variable | 용도 |
|---|---|
| `$datasource` | Prometheus datasource portability |
| `$window` | `1m`, `5m`, `15m`, `1h` 조회 window |
| `$model` | `local-main`, `local-embed`, `risk-prompt`, `risk-siren` |
| `$runtime_service` | compose runtime service filter |
| `$route` | Gateway route filter |
| `$status_code` | HTTP status filter |

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

Prometheus는 4개 vLLM runtime을 하나의 `vllm-runtimes` job으로 scrape하고, 각 target에 `model`과 `runtime_service` label을 붙인다. `job`을 모델별로 나누지 않고 label로 분리하면 dashboard와 alert rule을 같은 쿼리 구조로 유지할 수 있다.

| 모델 | Runtime service | Prometheus target |
|---|---|---|
| `local-main` | `main-llm-vllm` | `main-llm-vllm:9401` |
| `local-embed` | `embedding-vllm` | `embedding-vllm:9402` |
| `risk-prompt` | `risk-prompt-vllm` | `risk-prompt-vllm:9403` |
| `risk-siren` | `risk-siren-vllm` | `risk-siren-vllm:9404` |

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

`stream=true` chat 경로는 다음 metric으로 chunk relay와 accounting 상태를 확인한다. prompt와 generated text는 label에 포함하지 않는다.

```text
streaming_chunks_total{service="gateway",target="local-main"}
streaming_bytes_total{service="gateway",target="local-main"}
streaming_usage_events_total{service="gateway",target="local-main"}
streaming_errors_total{service="gateway",target="local-main",code="UPSTREAM_TIMEOUT",phase="mid_stream"}
```

현재 dashboard는 위 metric으로 streaming chunk/byte/error/usage event를 표시한다. 다음 항목은 현재 metric만으로 정확히 계산하지 않는다.

- TTFT p50/p95
- full stream duration
- chunks per response
- client disconnect count as a first-class counter

이 항목이 필요하면 다음 metric을 추가한다.

```text
streaming_requests_total{service,target,result}
streaming_time_to_first_chunk_seconds_bucket{service,target}
streaming_duration_seconds_bucket{service,target,result}
streaming_chunks_per_response_bucket{service,target}
streaming_client_disconnects_total{service,target}
```

## Provisioning 정책

Reference release의 Grafana dashboard는 Git-managed artifact다.

- `ops/grafana/provisioning/datasources/prometheus.yml`는 datasource UID를 `prometheus`로 고정한다.
- compose는 `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/gpu_capacity_and_oom_risk.json`로 Grafana home dashboard를 고정한다.
- dashboard panel은 `$datasource` variable을 통해 Prometheus datasource를 참조한다.
- reference release에서는 `allowUiUpdates: false`를 사용한다.
- local 실험이 필요하면 별도 local override 또는 exported JSON을 사용한다.

Grafana UI에서 저장한 provisioned dashboard 변경은 JSON source로 자동 반영되지 않는다. 따라서 운영 기준 dashboard는 repository JSON을 source of truth로 둔다.

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
