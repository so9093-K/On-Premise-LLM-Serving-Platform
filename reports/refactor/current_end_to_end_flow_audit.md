# Current End-to-End Flow Audit

Date: 2026-05-11
Scope: full project package after streaming and Grafana operations work.

## 검토 범위

- Application code: `src/**`
- Contracts and schemas: `contracts/**`, `specs/**`
- Runtime configuration: `configs/**`, `harness/**`
- Operations assets: `ops/**`
- Grafana/Prometheus: `ops/grafana/**`, `ops/prometheus/**`, `configs/monitoring.yaml`
- Documentation: `docs/**`, `adr/**`, `reports/**`
- Build, clean, package scripts: `Makefile`, `scripts/**`
- Tests: `tests/**`

## 전체 흐름 점검 결과

| Flow | Result | Notes |
|---|---|---|
| Contract validation | PASS | JSON schema, OpenAPI snapshot, registry projection checks are consistent. |
| Streaming contract | PASS | `stream=true` and `stream_options.include_usage` are accepted and documented. |
| Streaming transport | PASS | Gateway SSE fast path relays upstream bytes and records chunk/error/usage-event metrics. |
| Grafana UX | PASS with runtime-render caveat | Six provisioned dashboards exist with datasource/window/model/runtime/route/status variables and operator Text panels. Browser rendering still requires a live Grafana check. |
| Prometheus rules | PASS | Recording rules align with monitoring projection and dashboard references. |
| Build/package hygiene | PASS | Runtime secrets, `dist/`, `.pytest_cache`, bytecode, and `__pycache__` are excluded from release artifacts. |
| Delete/clean flow | PASS | Clean policy preserves source/docs/tests and separates runtime secret purge from normal clean. |
| Legacy stream policy | PASS | No active docs or contract path claims `stream=true` is unsupported. |

## 이번 재검토에서 발견한 문제와 수정

### 1. Endpoint reference dashboard inventory drift

`docs/operations/endpoint_reference.md` still described only three dashboards even though the project now provisions six dashboards.

Fixed by documenting all current dashboard UIDs:

- `executive_runtime_overview`
- `chat_api_deep_dive`
- `model_runtime_deep_dive`
- `gpu_capacity_and_oom_risk`
- `risk_signal_operations`

A governance guard now fails if the endpoint reference omits any required dashboard UID.

### 2. Low-traffic error-rate example drift

The same endpoint reference still showed the old dashboard query pattern that divided a 5xx `rate()` by a denominator clamped to `1`. That can understate error rate when traffic is below 1 request/sec. The documented example now uses an `increase()`-based dashboard ratio:

```promql
sum(increase(http_requests_total{service="gateway",status_code=~"5.."}[5m]))
  / clamp_min(sum(increase(http_requests_total{service="gateway"}[5m])), 1)
```

Governance now blocks this stale example from returning to the endpoint reference.

### 3. Chat API dashboard upstream path label mismatch

`chat_api_deep_dive` used the public route path for `upstream_request_duration_seconds_bucket`, but Gateway records upstream paths as `chat/completions` and `chat/completions:stream`.

Fixed query:

```promql
histogram_quantile(
  0.95,
  sum(rate(upstream_request_duration_seconds_bucket{service="gateway",target=~"$model",path=~"chat/completions(:stream)?"}[$window])) by (le, target)
)
```

A dashboard governance guard now checks this label contract.

## 레거시/삭제 후보 판단

No additional active source, docs, or tests were deleted in this round. The only safe deletions remain generated/local artifacts:

- `.runtime/`
- `dist/`
- `.pytest_cache/`
- `__pycache__/`
- `*.pyc`
- timestamped one-off runtime validation files generated during local checks

Retained intentionally:

- `docs/refactor/phase*.md`: historical snapshots, not current operating guidance.
- `src/ai_model_serving/validation.py`: compatibility facade.
- `scripts/validation/runtime_validation.py` and `scripts/validation/validate_contracts.py`: stable operator entrypoints.
- Governance tests: protect contract, monitoring, packaging, and retired-source invariants.

## Side-effect review

| Change area | Side effect considered | Result |
|---|---|---|
| Endpoint reference update | Could diverge from `configs/monitoring.yaml` dashboard list | Added governance guard. |
| PromQL example update | Could conflict with recording rules that still use `rate()` | Accepted: docs distinguish human dashboard ratio from recording-rule trend math. |
| Upstream path query update | Could drop non-stream data | Regex includes both `chat/completions` and `chat/completions:stream`. |
| Governance guard additions | Could push `docs_ops.py` over module-size guard | Kept module under 600 lines. |
| Packaging after local release checks | Could include `.runtime` or `dist` | Cleaned generated artifacts before packaging. |

## Remaining runtime checks

These require live services and were not claimed as statically completed:

1. Browser-render all six Grafana dashboards.
2. Verify dashboard variables against a live Prometheus datasource.
3. Run real `curl -N` streaming traffic through Gateway and confirm chunk/error/usage panels move.
4. Confirm proxy buffering is off in the deployment ingress/proxy.
5. Add TTFT, stream duration, chunks-per-response, and client-disconnect instrumentation if product dashboards require those panels.
