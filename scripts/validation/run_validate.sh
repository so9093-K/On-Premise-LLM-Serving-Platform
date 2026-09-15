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
  printf '[validate]   Python 3.12 or 3.13 was not found\n' >&2
  exit 2
fi

run_check "python compatibility" "$PYTHON_BIN" scripts/build/check_python.py --context validate
run_check "contracts" "$PYTHON_BIN" scripts/validation/validate_contracts.py
run_check "shell syntax" "$PYTHON_BIN" scripts/validation/validate_shell_syntax.py
run_check "access / exposure" "$PYTHON_BIN" scripts/validation/validate_exposure_profiles.py --strict
run_check "compose overrides" "$PYTHON_BIN" scripts/compose/render_exposure_overrides.py --check
run_check "environment contract" "$PYTHON_BIN" scripts/validation/validate_env_contract.py --strict
run_check "performance contract" "$PYTHON_BIN" scripts/validation/validate_performance_contract.py
# 검사 하나에 run_check 한 줄씩 둔다. 여러 command를 함수로 묶어 넘기면,
# run_check가 그 함수를 `if "$@"` 조건으로 부르는 순간 함수 안에서 errexit이
# 꺼진다 -- 앞 command가 실패해도 함수는 계속 돌고 마지막 command의 status가
# 결과가 된다. 실제로 OpenAPI drift를 주입했더니 PASS로 보고됐다.
# --check가 생성 문서 전체를 구조 비교하므로 OpenAPI drift는 여기서 전부 잡힌다.
# 예전엔 openapi_snapshot_diff.py를 이어서 돌렸지만, 그쪽이 비교하던 계약 스키마는
# 생성 문서에도 같은 파일에서 주입되는 값이라 자기 자신과 비교하고 있었다.
run_check "generated artifacts" "$PYTHON_BIN" scripts/render_runtime_assets.py --check
# Console build 자체는 별도 Linux/Node job이 수행한다. Python validation에서는
# checked-in dist/manifest/lock/source 정책만 검증해 app-contract matrix가 Node에
# 의존하지 않으면서도 package/release에서 누락된 UI를 fail-closed로 잡는다.
run_check "console artifacts" "$PYTHON_BIN" scripts/validation/validate_console_assets.py
# /docs 번들은 vendoring 되어 있고 CDN 폴백이 없다. 파일이 없거나 잘리면 배포된
# 문서가 조용히 빈 화면이 되므로 로컬 해시만 확인한다(네트워크 불필요).
run_check "docs bundles" "$PYTHON_BIN" scripts/build/fetch_docs_assets.py --check

printf '[validate] complete: %d/%d checks passed\n' "$VALIDATE_PASSED" "$VALIDATE_TOTAL"
