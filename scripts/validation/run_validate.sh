#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$(command -v python3.12 || command -v python3 || command -v python || true)"
fi
export PYTHONDONTWRITEBYTECODE=1

VALIDATE_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/validate-output.XXXXXX")"
trap 'rm -f "$VALIDATE_OUTPUT"' EXIT HUP INT TERM
VALIDATE_PASSED=0
VALIDATE_TOTAL=0

emit_details() {
  if [[ -s "$VALIDATE_OUTPUT" ]]; then
    sed 's/^/[validate]   /' "$VALIDATE_OUTPUT"
  fi
}

run_check() {
  local label="$1"
  local status
  shift
  VALIDATE_TOTAL=$((VALIDATE_TOTAL + 1))
  if "$@" >"$VALIDATE_OUTPUT" 2>&1; then
    printf '[validate] %-24s PASS\n' "$label"
    VALIDATE_PASSED=$((VALIDATE_PASSED + 1))
    if [[ "${VALIDATE_VERBOSE:-0}" == "1" ]]; then
      emit_details
    fi
    return 0
  else
    status=$?
  fi

  printf '[validate] %-24s FAIL (exit=%d)\n' "$label" "$status" >&2
  if [[ -s "$VALIDATE_OUTPUT" ]]; then
    emit_details >&2
  else
    printf '[validate]   no diagnostic output\n' >&2
  fi
  return "$status"
}

if [[ -z "$PYTHON_BIN" ]]; then
  printf '[validate] %-24s FAIL (exit=2)\n' "python compatibility" >&2
  printf '[validate]   Python 3.12, 3.13, or 3.14 was not found\n' >&2
  exit 2
fi

run_check "python compatibility" "$PYTHON_BIN" scripts/build/check_python.py --context validate
run_check "contracts" "$PYTHON_BIN" scripts/validation/validate_contracts.py
run_check "shell syntax" "$PYTHON_BIN" scripts/validation/validate_shell_syntax.py
run_check "exposure profiles" "$PYTHON_BIN" scripts/validation/validate_exposure_profiles.py --strict
run_check "compose overrides" "$PYTHON_BIN" scripts/compose/render_exposure_overrides.py --check
run_check "environment contract" "$PYTHON_BIN" scripts/validation/validate_env_contract.py --strict
run_check "runtime assets" "$PYTHON_BIN" scripts/render_runtime_assets.py --check
run_check "OpenAPI snapshot" "$PYTHON_BIN" scripts/validation/openapi_snapshot_diff.py

printf '[validate] complete: %d/%d checks passed\n' "$VALIDATE_PASSED" "$VALIDATE_TOTAL"
