# Current First-Run, Clean, and Package Flow Audit

Date: 2026-05-11
Baseline: `project_audit.zip` re-expanded as a clean release candidate.
Scope: all project files, first-run/operator entrypoints, validation, deletion/cleaning, generated reports, and release packaging.

## Executive conclusion

The project is internally consistent as a static release candidate after the streaming, monitoring, documentation, and UX work. A clean clone/new-operator path is understandable and guarded by tests/governance:

1. read `README.md` / `docs/README.md` / `make help`,
2. initialize env via `make init-env-local` or `make init-env-compose`,
3. run static validation with `make validate` or `make release-check`,
4. run deterministic tests with `make test` or `make release-check-full`,
5. clean generated artifacts with `make clean` / `make clean-all`,
6. build a distributable ZIP with `make package`.

No active source, doc, or test file was removed in this pass. The only safe deletion targets remain generated/local artifacts such as `dist/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, `logs/`, optional `.runtime/`, and optional model cache directories.

## Full-file review coverage

Reviewed file groups:

- root metadata: `README.md`, `CHANGELOG.md`, `VERSION`, `Makefile`, `Dockerfile`, `pyproject.toml`, env examples, lockfiles
- source: `src/ai_model_serving/**`
- scripts: `scripts/**`
- tests: `tests/unit/**`, `tests/contract/**`
- contracts/schemas: `contracts/**`, `specs/**`, `harness/**`
- runtime config: `configs/**`, `model_cards/**`
- ops: `ops/compose/**`, `ops/grafana/**`, `ops/prometheus/**`, `ops/patches/**`
- docs and reports: `docs/**`, `reports/refactor/**`, `reports/runtime/**`, `adr/**`, `examples/**`

## First-run flow debug

Primary entrypoints are consistent:

| User need | Entry point | Result |
|---|---|---|
| Discover commands | `make help`, `make guide` | Korean-first command guide with first-run/build/clean sections. |
| Prepare local app env | `make init-env-local` | Creates local profile env, skips existing env unless force target is used. |
| Prepare compose/full-stack env | `make init-env-compose` | Creates compose profile env and runtime secret files. |
| Static validation only | `make validate` | Runs Python compatibility and contract/governance validation. |
| Deterministic test | `make test` | Runs pytest with bytecode/plugin side effects reduced. |
| Static release gate | `make release-check` | Runs contract, runtime config, compose, OpenAPI, registry, monitoring, auth, model, and operator projection checks. |
| Full static + tests | `make release-check-full` | Adds deterministic tests. |
| Release ZIP | `make package` | Regenerates stable generated reports and packages with exclusion policy. |

Live Docker/GPU/vLLM commands remain target-host only and are not claimed as completed by static review: `make compose-up`, `make ready-full`, `make runtime-validate`, `make smoke`.

## Deletion and cleanup flow debug

Observed cleanup behavior:

```bash
make clean-dry-run
make clean
make clean-all
PURGE_RUNTIME_SECRETS=1 make clean-all
PURGE_MODEL_CACHE=1 make clean-all
PURGE_RUNTIME_SECRETS=1 PURGE_MODEL_CACHE=1 make clean-all
```

Deletion policy is safe:

| Target | Default clean | `make clean-all` | Extra purge flag | Reason |
|---|---:|---:|---:|---|
| `dist/`, `build/`, `outputs/`, `run/`, `.pytest_cache/` | removed | removed | none | Local generated artifacts. |
| `__pycache__/`, `*.pyc`, `*.egg-info/` | removed | removed | none | Python/test artifacts. |
| `logs/` | kept | removed | none | Logs are more destructive than normal clean, but safe in all-clean. |
| `.runtime/` | kept | kept | `PURGE_RUNTIME_SECRETS=1` | Contains local runtime secret/token files. |
| `model_cache/`, top-level `models/` | kept | kept | `PURGE_MODEL_CACHE=1` | Potentially large/expensive model cache. |
| `docs/build/`, `docs/models/` | kept | kept | none | Source documentation, not top-level generated folders. |

`clean_all.sh` refuses to clean if local service PID files indicate running services, unless `FORCE_CLEAN_RUNNING=1` is intentionally set.

## Package flow debug

The correct delivery flow is:

```bash
make clean-all
PYTHONPATH=src make package
```

`make package` runs:

1. `scripts/reports/refresh_generated_reports.py`
2. Python compatibility check for package context
3. `scripts/validation/validate_contracts.py`
4. `scripts/build/package_release.sh`

Package hygiene verified:

- excludes `.env` and `.env.*` while keeping safe examples
- excludes `.runtime/`, `dist/`, `build/`, `logs/`, `outputs/`, `run/`
- excludes top-level `model_cache/` and `models/`
- excludes `__pycache__/`, `*.pyc`, `*.pyo`, `*.egg-info/`
- excludes timestamped `reports/runtime/runtime_validation_*.json|md`
- regenerates packaged `live_evidence_bundle` as a static placeholder if live timestamped evidence was excluded
- preserves executable mode bits in ZIP metadata
- writes deterministic timestamps in the ZIP entries

The packaged ZIP contains six Grafana dashboards and no local secret/cache/test artifacts.

## Validation results from this pass

```bash
python -m compileall -q src scripts tests
PYTHONPATH=src python scripts/validation/validate_contracts.py
PYTHONPATH=src python scripts/validation/openapi_snapshot_diff.py
PYTHONPATH=src python scripts/validation/release_check.py --step-timeout-seconds 60
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/unit tests/contract
```

Observed result:

- contract validation: PASS
- OpenAPI snapshot diff: PASS
- release check: PASS
- deterministic tests: `198 passed`

## Legacy and stale-content review

Findings:

- `stream=true is not supported` no longer appears in active code/docs.
- OpenAI-compatible streaming and `stream_options.include_usage` are documented with the `stream=true` precondition.
- Dashboard inventory is six dashboards, not the previous three-dashboard baseline.
- Dashboard ratio docs use `increase()`-based low-traffic-friendly examples.
- `docs/refactor/phase*.md` are historical records and are intentionally not edited as current operating instructions.
- Compatibility facades are intentionally retained: `src/ai_model_serving/validation.py`, `scripts/validation/runtime_validation.py`, `scripts/validation/validate_contracts.py`.

No additional active file was identified as safe to delete. Deleting historical phase docs, compatibility facades, or governance tests would reduce traceability or break operator compatibility.

## Side effects considered

| Area | Risk | Result |
|---|---|---|
| Running validation before packaging | Creates generated runtime reports and pycache | `make package` refreshes stable reports; package policy excludes pycache and timestamped runtime validation artifacts. |
| Running `clean-all` | Could remove secrets or large model cache | Secrets/cache require explicit purge flags. |
| Adding Grafana dashboards | Could drift from provisioning/docs | Governance now checks dashboard inventory and variables. |
| Streaming contract tightening | Non-stream clients with `stream_options` now get 422 | Intentional; `stream_options` is streaming-only. |
| Static package review | Cannot prove live vLLM/proxy/Grafana behavior | Live checks remain explicit target-host tasks. |

## Remaining target-host checks

These cannot be completed by static package review:

1. Real `curl -N` streaming smoke test against vLLM through Gateway.
2. Grafana browser render of all six dashboards.
3. Prometheus variable dropdown checks with live targets.
4. Proxy/Ingress buffering-off verification.
5. GPU host readings for VRAM, KV cache, queue, power, and temperature.
6. Optional instrumentation for TTFT, full stream duration, chunks per response, and client disconnects.
