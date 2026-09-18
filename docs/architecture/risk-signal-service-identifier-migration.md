# Risk Signal Service identifier migration

## Scope

This note turns ADR-0030's Risk Signal Service namespace decision into an implementation sequence. It does not change the public `/v1/risk/*` API.

The legacy identifiers `risk-adapter`, `risk_adapter`, and `RISK_ADAPTER_*` are not one compatibility class. Persistent `.env` keys require a migration bridge; internal Python/config/process identifiers can cut over atomically once all producers and consumers move in the same release.

## Invariants

1. `/v1/risk/*` remains the public Risk domain API.
2. Existing persistent `.env` values are never silently discarded.
3. A canonical and legacy persistent key with different values fails closed before mutation.
4. Runtime code reads canonical keys only after the corresponding persistent key is registered in `configs/env_contract.yaml:renamed_keys` and `sync-env` can migrate it.
5. Legacy persistent keys are migration inputs only; new examples, generated env, runtime projections, and runtime consumers use canonical keys.
6. Internal/process identifiers do not gain new compatibility aliases solely because they existed historically.
7. Each implementation PR must keep producer, consumer, validator, and tests consistent and pass CI Gate before merge.

## Implementation sequence

### 1. Host exposure keys

Migrate the bounded host/process exposure trio first:

- `RISK_ADAPTER_HOST` -> `RISK_SIGNAL_SERVICE_HOST`
- `RISK_ADAPTER_PORT` -> `RISK_SIGNAL_SERVICE_PORT`
- `RISK_ADAPTER_BIND_ADDR` -> `RISK_SIGNAL_SERVICE_BIND_ADDR`

The PR must update `configs/env_contract.yaml` renamed keys, example env files, `configs/services.yaml`, Compose exposure overrides, and local ops scripts together. The service ID remains `risk-signal-service`. The `risk_adapter` registry key/category is not part of this first PR unless all of its consumers are also migrated atomically.

Exit condition: repository runtime/template/config consumers use only the three canonical keys; the three legacy keys appear only in migration declarations/tests/history. `sync-env` migrates legacy-only values and rejects conflicting canonical+legacy values.

### 2. Application URL/settings keys

Migrate remaining persistent Risk Signal Service application settings as one producer/consumer set. Register every renamed persistent key before removing its legacy runtime read. Do not introduce permanent dual-read behavior.

Exit condition: application runtime and service-env projections consume canonical settings only; legacy persistent names remain only as `sync-env` migration inputs/tests/history.

### 3. Python/config identifiers

Rename `risk_adapter` Python modules, factories, router modules, endpoint-spec symbols, config sections, test fixtures, and internal script identifiers to Risk Signal Service terminology in an atomic internal cutover. Preserve `/v1/risk/*` routes and response schemas.

Exit condition: executable code/config contains no `risk_adapter` identifier except an explicitly documented migration parser, if one is still required. Historical ADR/CHANGELOG text is not rewritten.

## Verification

Prefer current-contract assertions: canonical env ownership, migration conflict behavior, service-registry consistency, and equality of Gateway/Risk Signal Service public risk route sets. Do not add tests whose only purpose is to assert that an old identifier once existed.
