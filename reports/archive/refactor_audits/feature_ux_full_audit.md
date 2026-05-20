---
document_type: current_snapshot
status: historical_audit
audience: operator, release_engineer
note: "이 문서는 2026-05-11 감사 시점의 snapshot이다. dashboard 수는 현재 ops/grafana/dashboards/ 기준 5개다."
---

# Current Feature and UX Full Audit

Date: 2026-05-11
Baseline: `full_review.zip` re-expanded into a clean working directory.
Scope: application code, contracts, schemas, docs, build/clean/package flow, runtime validation, monitoring/Grafana UX, security/auth UX, model discovery UX, streaming UX, and generated reports.

## Executive conclusion

The project is now internally consistent as a reference release package. The major feature surfaces are covered by contract tests and governance validation:

- Gateway chat, embedding, and risk forwarding APIs.
- OpenAI-compatible `stream=true` SSE relay.
- `stream_options.include_usage` support, with the important constraint that it is valid only when `stream=true` is present.
- `/v1/models` parameter discovery.
- Risk Adapter signal-only response policy.
- Prometheus/Grafana monitoring with six Grafana dashboards, including `serving_cockpit` as the Grafana home dashboard.
- Build, clean, package, auth, and operator report workflows.

The review did find several drift/UX issues and one contract bug. They were corrected in this pass.

## Issues fixed in this pass

### 1. `stream_options` accepted without `stream=true`

The previous contract validated the shape of `stream_options`, but did not require `stream=true`. That allowed a non-streaming request to pass a streaming-only option through to upstream.

Fixed behavior:

- `stream=true` + `stream_options.include_usage=true`: accepted.
- `stream_options` without `stream=true`: rejected with 422.
- `stream=false` + `stream_options`: rejected with 422.
- non-boolean `include_usage`: rejected with 422.

Files updated:

- `src/ai_model_serving/contracts/chat.py`
- `specs/schemas/chat_completion_request.schema.json`
- `tests/unit/test_gateway_app.py`
- `src/ai_model_serving/governance_validation/schemas.py`
- `src/ai_model_serving/governance_validation/docs_ops.py`

### 2. API docs still called `/docs` Swagger UI

The runtime uses Scalar UI for `/docs`, but multiple docs still called it Swagger UI. This is a UX mismatch for new operators.

Files updated:

- `README.md`
- `docs/operations/endpoint_reference.md`
- `docs/specs/api.md`

### 3. `local-main` parameter table placed `stream` in the wrong mental bucket

`endpoint_reference.md` listed `stream=true` in the “not adjustable” column even though streaming is a user request parameter. The table now lists `stream` and `stream_options` in the adjustable column, while runtime/serving hyperparameters remain operator-only.

### 4. Streaming usage docs did not clearly state the `stream=true` precondition

The docs now consistently say that `stream_options.include_usage=true` must be used together with `stream=true`.

Files updated:

- `docs/operations/endpoint_reference.md`
- `docs/operations/model_parameter_discovery.md`
- `docs/operations/streaming_runtime_operations.md`
- `docs/refactor/phase31_model_parameter_discovery.md`
- `docs/specs/api.md`

### 5. Monitoring UX docs still reflected an older dashboard maturity level

`ux_ui_review.md` still described the earlier three-dashboard milestone and listed Grafana variable/template validation as a remaining weakness. It now distinguishes the historical milestone from the current five-dashboard (5개), variable-backed, Git-managed Grafana state. _(Historical note: "six" was a draft target; five were implemented and validated in `ops/grafana/dashboards/`.)_

`docs/refactor/refactor_backlog.md` no longer lists Grafana dashboard variable/template validation as open, because governance now checks dashboard existence, variable presence, datasource use, text runbooks, and key PromQL regressions.

## Feature-by-feature audit

### Gateway API UX

Status: good, with one fixed streaming-options contract bug.

- `/health`, `/ready`, `/metrics`, `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`, and `/v1/risk/*` are documented.
- OpenAPI generation injects checked-in JSON schemas, keeping `/docs` and `/openapi.json` aligned with runtime validators.
- Scalar UI is the current `/docs` surface; docs now use the same term.
- Streaming uses SSE with `text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`.
- Missing live-only verification: browser/API client test against a running vLLM instance.

### Streaming UX and debugging

Status: consistent statically; live validation still required.

- Gateway has a non-buffering streaming fast path.
- Standard chunks are relayed unchanged.
- Mid-stream failures emit SSE `event: error` followed by `[DONE]` rather than trying to switch to JSON after headers may have been committed.
- Usage chunk presence is counted without exporting prompt text, generated text, or numeric usage values as labels.
- Fixed: `stream_options` is now rejected unless `stream=true` is present.
- Remaining product instrumentation backlog: TTFT, stream duration, chunks per response, and client disconnect first-class counters/histograms.

### Model discovery UX

Status: good.

- `/v1/models` exposes capabilities and user-adjustable `request_parameters`.
- Risk models correctly expose no user-adjustable sampling parameters.
- Docs now treat `stream` and `stream_options` as user-adjustable chat parameters, while runtime hyperparameters stay operator-only.

### Risk signal UX

Status: good.

- Risk Adapter remains signal-only and avoids policy decision fields such as `allow`, `block`, `decision`, and `action`.
- Forbidden response field metrics are intentionally field-name only and do not include prompts or generated output.
- Dashboard UX covers assessment volume, detection rate, timeout/system signals, forbidden fields, latency, and readiness.

### Grafana/Prometheus UX

Status: strong for static reference; browser rendering remains live-only.

- Six dashboards are present and provisioned.
- Dashboards are Korean-first with English metric terms.
- Each dashboard starts with a Text panel runbook.
- Variables are present: `$datasource`, `$window`, `$model`, `$runtime_service`, `$route`, `$status_code`.
- Dashboards use datasource variable instead of implicit default datasource.
- Reference provisioning sets `allowUiUpdates=false`.
- Low-traffic dashboard ratios use `increase()` instead of clamping a `rate()` denominator to `1`.
- No prompt/generated text appears in metric labels or dashboard text.
- Remaining live-only verification: real Grafana rendering and Prometheus variable dropdown behavior.

### Auth/security UX

Status: acceptable reference baseline, with known operational warnings.

- `make auth-status`, `make auth-doctor`, `make auth-plan`, and `make auth-apply` provide operator-facing control-plane UX.
- Docs explain local/private/strict exposure expectations.
- Release check intentionally reports warnings for host-published monitoring ports in reference compose. These are not static failures because network boundary is deployment-owned.
- Remaining design backlog: app-level CIDR/admin mode if the deployment cannot rely on ingress/network policy.

### Build, clean, package UX

Status: good.

- `make guide`, `make first-run`, `make release-check`, `make release-check-full`, `make package`, `make remove-plan`, `make clean`, and `make clean-all` have clear operator roles.
- Runtime secrets and model caches require explicit purge flags.
- Release package excludes `.runtime`, `dist`, pycache, pytest cache, and generated local runtime artifacts.
- Remaining live-only verification: GPU/vLLM target host package and startup smoke.

### Documentation state

Status: current docs are aligned after this pass.

- Scalar vs Swagger wording was corrected where it affected operator-facing endpoint references.
- Streaming usage preconditions are now explicit.
- Grafana maturity/backlog docs no longer claim already-completed validation work is missing.
- Historical phase docs are retained as history, not active operating guidance.

## Side effects considered

| Change | Potential side effect | Mitigation |
|---|---|---|
| Reject `stream_options` without `stream=true` | Existing clients sending `stream_options` on non-stream requests now receive 422 | This matches the intended streaming-only contract; tests and schema now enforce it. |
| JSON Schema `allOf` rule added | OpenAPI consumers see a stricter contract | Runtime validator, schema, governance, and tests are now aligned. |
| Docs changed from Swagger to Scalar | Operators may search for Swagger wording | FastAPI/ReDoc policy docs still explain Scalar replaced FastAPI Swagger UI. |
| Backlog item removed | Could hide future Grafana automation work | UX review keeps JSON auto-generation as a separate future candidate. |

## Remaining checks that cannot be completed statically

1. Real vLLM streaming smoke with `curl -N`.
2. Grafana browser rendering of all six dashboards.
3. Prometheus live target scrape and dashboard variable dropdown verification.
4. Proxy/ingress buffering verification in the deployment environment.
5. GPU memory, queue, KV cache, temperature, and power readings on the target A6000 host.
6. TTFT/duration/client-disconnect instrumentation decision after observing real streaming traffic.
