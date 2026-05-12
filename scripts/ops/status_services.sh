#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

source scripts/lib/load_env.sh
load_local_env .env

MODE="${1:---local}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
GATEWAY_PROBE_HOST="${GATEWAY_PROBE_HOST:-${GATEWAY_BIND_ADDR:-localhost}}"
if [[ -z "$GATEWAY_PROBE_HOST" || "$GATEWAY_PROBE_HOST" == "0.0.0.0" ]]; then
  GATEWAY_PROBE_HOST="localhost"
fi
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT:-9400}}"
RISK_ADAPTER_BASE_URL="${RISK_ADAPTER_BASE_URL:-http://localhost:9405}"
ADMIN_API_KEY="$(local_env_first_value .env ADMIN_API_KEY ADMIN_API_KEYS || true)"

status_pid() {
  local name="$1"
  local pid_file="run/${name}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    echo "${name}: process running pid $(cat "$pid_file")"
  elif [[ -f "$pid_file" ]]; then
    echo "${name}: stale pid file $(cat "$pid_file")"
  else
    echo "${name}: process not tracked"
  fi
}

status_health() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 3 "$url/health" >/dev/null 2>&1; then
    echo "${name}: /health ok"
  else
    echo "${name}: /health unavailable"
  fi
}

status_ready() {
  local name="$1"
  local url="$2"
  local tmp
  tmp="$(mktemp)"
  local -a auth_args=()
  if [[ -n "$ADMIN_API_KEY" ]]; then
    auth_args=(-H "Authorization: Bearer ${ADMIN_API_KEY}")
  fi
  local http_code
  http_code="$(curl -sS --max-time 4 "${auth_args[@]}" -o "$tmp" -w '%{http_code}' "$url/ready" 2>/dev/null || true)"
  if [[ "$http_code" == "200" || "$http_code" == "503" ]]; then
    "$PYTHON_BIN" - "$name" "$tmp" "$http_code" <<'PY'
import json, sys
name, path, http_code = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    doc = json.load(open(path, encoding="utf-8"))
except Exception as exc:
    print(f"{name}: /ready HTTP {http_code} invalid json ({exc})")
    raise SystemExit(0)
status = doc.get("status", "unknown")
phase = doc.get("phase", "unknown")
not_ready = doc.get("not_ready_dependencies", [])
deps = doc.get("dependencies", [])
if isinstance(deps, list) and deps:
    dep_text = ", ".join(f"{d.get('name')}={d.get('status')}" for d in deps if isinstance(d, dict))
    suffix = f" not_ready={','.join(not_ready)}" if isinstance(not_ready, list) and not_ready else ""
    print(f"{name}: /ready HTTP {http_code} status={status} phase={phase} ({dep_text}){suffix}")
else:
    print(f"{name}: /ready HTTP {http_code} status={status} phase={phase}")
PY
  elif [[ "$http_code" == "401" || "$http_code" == "403" ]]; then
    echo "${name}: /ready HTTP ${http_code} unauthorized"
  else
    echo "${name}: /ready unavailable (HTTP ${http_code:-000})"
  fi
  rm -f "$tmp"
}

status_pid gateway
status_pid risk_adapter
status_health gateway "$GATEWAY_BASE_URL"
status_health risk_adapter "$RISK_ADAPTER_BASE_URL"

if [[ "$MODE" == "--full" || "$MODE" == "--ready" ]]; then
  status_ready gateway "$GATEWAY_BASE_URL"
  status_ready risk_adapter "$RISK_ADAPTER_BASE_URL"
elif [[ "$MODE" == "--local" ]]; then
  echo "status mode: local app health only. Use 'make ready-full' for strict vLLM readiness."
else
  echo "unknown status mode: $MODE" >&2
  exit 2
fi
