#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/load_env.sh
load_local_env .env
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
"$PYTHON_BIN" scripts/build/check_python.py --context preflight-compose >/dev/null
"$PYTHON_BIN" scripts/compose/validate_vllm_compose.py

COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"

compose_file_dir_host() {
  if [[ "$COMPOSE_FILE" = /* ]]; then
    dirname "$COMPOSE_FILE"
  else
    dirname "$ROOT/$COMPOSE_FILE"
  fi
}

resolve_compose_relative_path() {
  local raw="$1"
  local base_dir
  base_dir="$(compose_file_dir_host)"

  if [[ "$raw" = /* ]]; then
    printf '%s\n' "$raw"
  else
    raw="${raw#./}"
    printf '%s/%s\n' "$base_dir" "$raw"
  fi
}

fail=0
require_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "[preflight] ok: $1 found"
  else
    echo "[preflight] missing: $1" >&2
    fail=1
  fi
}

require_cmd docker
if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    echo "[preflight] ok: docker compose available"
  else
    echo "[preflight] missing: docker compose plugin" >&2
    fail=1
  fi
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[preflight] ok: nvidia-smi found"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | sed 's/^/[preflight] gpu: /' || true
else
  echo "[preflight] warn: nvidia-smi not found; GPU/vLLM full-stack cannot be validated on this host" >&2
fi

# Collect host ports already occupied by the current compose project.
# docker compose up -d does not rebind ports for already-running containers,
# so ports owned by our own stack must not block the preflight.
compose_owned_ports=()
if command -v docker >/dev/null 2>&1 && [[ -f "$COMPOSE_FILE" ]]; then
  while IFS= read -r p; do
    [[ -n "$p" ]] && compose_owned_ports+=("$p")
  done < <(docker compose -f "$COMPOSE_FILE" --env-file ".env" ps --format "{{.Ports}}" 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+|0\.0\.0\.0:[0-9]+' \
    | grep -oE '[0-9]+$' || true)
fi

port_owned_by_compose() {
  local p="$1"
  for cp in "${compose_owned_ports[@]+"${compose_owned_ports[@]}"}"; do
    [[ "$cp" == "$p" ]] && return 0
  done
  return 1
}

# Check only host-published ports. vLLM runtime ports 9401-9403 are internal
# compose-network ports (`expose`), so another local host process on those ports
# does not block the full-stack compose deployment. Monitoring ports must follow
# .env/configured host port overrides rather than being hard-coded here.
host_published_ports=(
  "${GATEWAY_PORT:-9400}"
  "${RISK_ADAPTER_PORT:-9405}"
  "${PROMETHEUS_PORT:-9410}"
  "${GRAFANA_PORT:-9411}"
  "${DCGM_EXPORTER_PORT:-9412}"
  "${CADVISOR_PORT:-9413}"
)
for port in "${host_published_ports[@]}"; do
  [[ -n "$port" ]] || continue
  if port_owned_by_compose "$port"; then
    echo "[preflight] ok: port $port held by current compose stack"
  elif "$PYTHON_BIN" - "$port" <<'PY'
import socket, sys
port=int(sys.argv[1])
s=socket.socket()
s.settimeout(0.4)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
  then
    echo "[preflight] ok: port $port available on 127.0.0.1"
  else
    echo "[preflight] busy: port $port is already in use on 127.0.0.1" >&2
    fail=1
  fi
done

if [[ -n "${HF_TOKEN:-}" || -n "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then
  echo "[preflight] ok: Hugging Face token env present"
else
  echo "[preflight] warn: no HF_TOKEN/HUGGING_FACE_HUB_TOKEN found; private model pulls may fail" >&2
fi

HF_CACHE_DIR_RESOLVED="${HF_CACHE_DIR:-./model_cache/huggingface}"
HF_CACHE_PATH="$(resolve_compose_relative_path "$HF_CACHE_DIR_RESOLVED")"
mkdir -p "$HF_CACHE_PATH"
if [[ -d "$HF_CACHE_PATH" && -w "$HF_CACHE_PATH" ]]; then
  echo "[preflight] ok: HF cache dir writable: $HF_CACHE_PATH"
  echo "[preflight] relative HF_CACHE_DIR values are resolved from compose file directory: $(compose_file_dir_host)"
else
  echo "[preflight] missing: HF cache dir is not writable: $HF_CACHE_PATH" >&2
  fail=1
fi

if [[ "${SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK:-0}" != "1" ]]; then
  if bash scripts/models/check_risk_vllm_image_config.sh; then
    echo "[preflight] ok: risk vLLM image loads Kanana HF configs"
  else
    echo "[preflight] risk vLLM image config check failed; build/check a Kanana-compatible RISK_VLLM_IMAGE or set SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK=1 only for non-runtime local checks" >&2
    fail=1
  fi
else
  echo "[preflight] skip: risk vLLM image config check disabled by SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK=1" >&2
fi

if [[ -f .runtime/prometheus/admin_api_key && -s .runtime/prometheus/admin_api_key ]]; then
  echo "[preflight] ok: Prometheus admin bearer-token file present"
else
  echo "[preflight] missing or invalid: .runtime/prometheus/admin_api_key must be a non-empty file; run 'make sync-runtime-secrets'" >&2
  fail=1
fi

if [[ "$fail" != "0" ]]; then
  echo "[preflight] full-stack compose preflight failed; fix the items above before 'make compose-up'." >&2
  exit 1
fi

echo "[preflight] full-stack compose preflight passed"
