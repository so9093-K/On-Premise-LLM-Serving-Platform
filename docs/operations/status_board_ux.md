# 상태 보드 UX

상태 보드는 운영자가 Grafana 첫 화면에서 GPU 용량과 OOM 위험을 먼저 판단하고, 이어서 전체 서비스 상태를 확인하도록 구성한다.

## 기본 상태

| 상태 | 의미 |
|---|---|
| `green` | traffic, readiness, latency, error, GPU headroom 정상 |
| `yellow` | 사용 가능하지만 지연, queue, GPU pressure 등 주의 필요 |
| `red` | 사용자 경로, backend readiness, OOM/restart, contract invariant 실패 |
| `gray` | exporter 또는 metric mapping이 없어 데이터 없음 |

## 첫 화면 순서

1. GPU 여유분
2. GPU 메모리 사용량
3. GPU utilization
4. GPU 온도와 전력
5. OOM 또는 restart 이벤트
6. VRAM budget 대비 사용량
7. vLLM queue와 KV cache 압력

프롬프트나 생성 결과 원문은 metric label이나 dashboard text에 넣지 않는다.

## 상태 질문

첫 화면은 “지금 이 GPU에서 요청을 안전하게 계속 처리할 수 있는가?”에 답해야 한다. 이어서 `Executive Runtime Overview`의 `Overall Status`는 “지금 요청을 안전하게 처리할 수 있는가?”라는 전체 서비스 질문에 답한다. 장애 대응 상태는 `Action Required`, 데이터 부재 상태는 `No Runtime Data`로 표시한다.


## Dashboard 운영 질문

각 dashboard는 다음 운영 질문에 답한다.

| Dashboard | 운영 질문 |
|---|---|
| `gpu_capacity_and_oom_risk` | 지금 이 GPU에서 요청을 안전하게 계속 처리할 수 있는가? |
| `executive_runtime_overview` | 전체 서비스가 정상인가? 어디가 문제인가? |
| `chat_api_deep_dive` | Gateway path와 upstream path 중 어디가 병목인가? |
| `model_runtime_deep_dive` | 특정 모델의 queue, KV cache, token throughput, container resource 상태는? |
| `risk_signal_operations` | risk signal만으로 본 현재 detector 상태는? (prompt 없음) |

## Dashboard navigation

각 dashboard 상단 링크로 이동한다. `includeVars=true`로 현재 variable 값을 유지하며 이동한다.

```
gpu_capacity_and_oom_risk → executive_runtime_overview
executive_runtime_overview → gpu_capacity_and_oom_risk, chat_api_deep_dive, model_runtime_deep_dive, risk_signal_operations
chat_api_deep_dive → executive_runtime_overview, model_runtime_deep_dive
model_runtime_deep_dive → gpu_capacity_and_oom_risk, chat_api_deep_dive
risk_signal_operations → executive_runtime_overview
```

## Source of truth 및 UI 수정 정책

Dashboard JSON (`ops/grafana/dashboards/*.json`)이 source of truth다. Grafana UI에서 직접 수정한 내용은 JSON으로 자동 반영되지 않는다. 운영 변경은 JSON 수정 후 repository 커밋 → Grafana 재시작으로 적용한다. live datasource/render validation은 `make runtime-validate`로 별도 수행한다 (기본 CI gate가 아님).

## Operator status bundle

운영자용 정적 상태 번들은 `make operator-status`로 생성한다. 이 명령은 `reports/runtime/operator_status_bundle.json`과 `reports/runtime/operator_status_bundle.md`를 작성한다. 번들은 ModelRegistry projection을 기준으로 runtime targets, model inventory, storage paths, GPU budget, monitoring labels, readiness vocabulary, runtime validation matrix를 한 곳에 묶는다.

`make runtime-targets`는 더 작은 범위의 runtime target inventory만 생성한다. `make storage-paths`는 `.env`, `.runtime/`, `reports/runtime/`, `logs/`, `model_cache/huggingface/`의 저장 위치와 cleanup 정책을 생성한다. `make operator-status`는 그 정보를 포함해 GPU/resource budget과 monitoring/status vocabulary까지 함께 보여준다.

이 번들은 prompt, user text, model output, Authorization header, secret 값을 포함하지 않는다. 라이브 지표가 아니라 config/registry 기반 control-plane snapshot이므로, 실제 기동 상태 판단은 `make status READY_MODE=full`, `make runtime-validate`, 그리고 `make live-evidence` 결과와 함께 본다.

## 모니터링 projection 흐름

`make monitoring-projection`은 `reports/runtime/monitoring_projection.json`과 `reports/runtime/monitoring_projection.md`를 생성한다. live runtime 검증 전후에 Prometheus scrape job, vLLM model label, cAdvisor compose-service 정규식, Grafana variable 값을 점검할 때 사용한다.

## Live evidence 번들

`make live-evidence`는 `reports/runtime/operator_status_bundle.json`과 최신 `reports/runtime/runtime_validation_*.json` 리포트를 결합해 `reports/runtime/live_evidence_bundle.json`과 `reports/runtime/live_evidence_bundle.md`를 생성한다. 번들은 runtime 결과 상세를 sanitise하며 정적 operator bundle과 같은 개인정보 보호 계약을 유지한다. 원문 prompt, 사용자 텍스트, 모델 출력, Authorization header, secret 값은 포함하지 않는다.

`make release-check`는 이 흐름의 정적 release gate다. 서비스를 기동하지 않고 registry 기반 operator artifact를 재생성하고, 사용 가능한 최신 runtime validation report로 live evidence bundle을 작성하며, deterministic test를 실행한다.
