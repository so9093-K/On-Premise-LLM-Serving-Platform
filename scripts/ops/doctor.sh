#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

ok=1
check() {
  local name="$1"; shift
  if "$@" >/tmp/doctor_check.out 2>/tmp/doctor_check.err; then
    echo "[doctor] ok: ${name}"
  else
    ok=0
    echo "[doctor] warn: ${name}" >&2
    sed 's/^/[doctor]   /' /tmp/doctor_check.err >&2 || true
    sed 's/^/[doctor]   /' /tmp/doctor_check.out >&2 || true
  fi
}

check "python version" "$PYTHON_BIN" scripts/build/check_python.py --context doctor
check "required files/contracts" "$PYTHON_BIN" scripts/validation/validate_contracts.py
check "bash syntax" bash -c 'bash -n scripts/*.sh scripts/lib/*.sh'
check ".env present" test -f .env
check "status probe" bash scripts/ops/status_services.sh --local
check "docker daemon accessible" docker info
check ".runtime/prometheus/admin_api_key present (compose)" test -s .runtime/prometheus/admin_api_key

if [[ -f .env ]]; then
  hf_token="$(grep -E '^HF_TOKEN=' .env | cut -d= -f2- || true)"
  if [[ -z "$hf_token" ]]; then
    echo "[doctor] warn: HF_TOKEN is empty in .env; google/embeddinggemma-300m (gated) will fail to pull on make compose-up" >&2
    ok=0
  else
    echo "[doctor] ok: HF_TOKEN set in .env"
  fi
fi

echo "[doctor] guidance: use 'make init-env-local' for app-only, 'make init-env-compose' + 'make preflight-compose' for full-stack."
rm -f /tmp/doctor_check.out /tmp/doctor_check.err
if [[ "$ok" != "1" ]]; then
  exit 1
fi
