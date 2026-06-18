#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
"$PYTHON_BIN" scripts/build/check_python.py --context ready-local >/dev/null

# ready_local always checks localhost regardless of RISK_ADAPTER_BASE_URL, which
# may be a compose internal hostname (http://risk-adapter:9405) in compose .env files.
GATEWAY_BASE_URL="http://localhost:${GATEWAY_PORT:-9400}"
RISK_ADAPTER_BASE_URL="http://localhost:${RISK_ADAPTER_PORT:-9405}"

fail=0
check_health() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 3 "$url/health" >/dev/null 2>&1; then
    echo "${name}: /health ok"
  else
    echo "${name}: /health unavailable" >&2
    fail=1
  fi
}

check_health gateway "$GATEWAY_BASE_URL"
check_health risk_adapter "$RISK_ADAPTER_BASE_URL"

if [[ "$fail" != "0" ]]; then
  echo "ready-local failed: app-only services are not healthy. Run 'make start' or inspect 'make status'." >&2
  exit 1
fi

echo "ready-local passed: app-only /health checks are healthy. Use 'make ready-full' for vLLM dependency readiness."
