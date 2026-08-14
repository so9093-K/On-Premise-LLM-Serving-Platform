#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
DOCTOR_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ai-model-serving-doctor.XXXXXX")"
trap 'rm -rf "$DOCTOR_TMP"' EXIT

ok=1
check() {
  local name="$1"; shift
  if "$@" >"$DOCTOR_TMP/out" 2>"$DOCTOR_TMP/err"; then
    echo "[doctor] ok: ${name}"
  else
    ok=0
    echo "[doctor] warn: ${name}" >&2
    sed 's/^/[doctor]   /' "$DOCTOR_TMP/err" >&2 || true
    sed 's/^/[doctor]   /' "$DOCTOR_TMP/out" >&2 || true
  fi
}

check "python version" "$PYTHON_BIN" scripts/build/check_python.py --context doctor
check "env file present" test -f "$ENV_FILE"
check "status probe" env ENV_FILE="$ENV_FILE" bash scripts/ops/status_services.sh --local
check "docker daemon accessible" docker info
check ".runtime/prometheus/admin_api_key present (compose)" test -s .runtime/prometheus/admin_api_key

if [[ -f "$ENV_FILE" ]]; then
  hf_token="$("$PYTHON_BIN" scripts/env/env_get.py --env-file "$ENV_FILE" HF_TOKEN || true)"
  if [[ -z "$hf_token" ]]; then
    echo "[doctor] warn: HF_TOKEN is empty in $ENV_FILE; gated models will fail to pull on make compose-up" >&2
    ok=0
  else
    echo "[doctor] ok: HF_TOKEN set in $ENV_FILE"
  fi
fi

echo "[doctor] guidance: use 'make init-env-local' for app-only, 'make init-env-compose' + 'make preflight-compose' for full-stack."
if [[ "$ok" != "1" ]]; then
  exit 1
fi
