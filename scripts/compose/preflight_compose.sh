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
EXPOSURE_MODE="${EXPOSURE_MODE:-private_network}"

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
config_fail=0
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

# Resolve EXPOSURE_MODE to canonical mode. Unknown modes exit immediately with a clear error.
# Deprecated aliases emit a warning to stderr and resolve to the canonical target.
CANONICAL_MODE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE")"
echo "[preflight] EXPOSURE_MODE=$EXPOSURE_MODE (canonical: $CANONICAL_MODE)"

# Validate EXPOSURE_AUDIENCE for diagnostic_full_stack profiles.
# This check uses the structured diagnostics.requires_exposure_audience field, not free-text.
EXPOSURE_AUDIENCE="${EXPOSURE_AUDIENCE:-}"
REQUIRES_AUDIENCE="$("$PYTHON_BIN" - "$CANONICAL_MODE" <<'PY'
import sys, yaml
from pathlib import Path
mode = sys.argv[1]
data = yaml.safe_load((Path.cwd() / "configs" / "exposure_profiles.yaml").read_text(encoding="utf-8"))
profile = data.get("profiles", {}).get(mode, {})
diag = profile.get("diagnostics", {})
print("true" if diag.get("requires_exposure_audience") else "false")
PY
)"

if [[ "$REQUIRES_AUDIENCE" == "true" ]]; then
  if [[ -z "$EXPOSURE_AUDIENCE" ]]; then
    echo "[preflight] fail: EXPOSURE_MODE=$CANONICAL_MODE requires EXPOSURE_AUDIENCE." >&2
    echo "[preflight] Set EXPOSURE_AUDIENCE=local_only|private_lan|vpn|public to declare who can reach host-published ports." >&2
    config_fail=1
  else
    # Validate EXPOSURE_AUDIENCE value against allowed_values in configs/exposure_profiles.yaml.
    AUDIENCE_VALID="$("$PYTHON_BIN" - "$EXPOSURE_AUDIENCE" <<'PY'
import sys, yaml
from pathlib import Path
audience = sys.argv[1]
data = yaml.safe_load((Path.cwd() / "configs" / "exposure_profiles.yaml").read_text(encoding="utf-8"))
allowed = data.get("exposure_audience", {}).get("allowed_values", [])
if not allowed:
    allowed = ["local_only", "private_lan", "vpn", "public"]
print("true" if audience in allowed else "false")
PY
    )"
    if [[ "$AUDIENCE_VALID" != "true" ]]; then
      echo "[preflight] fail: EXPOSURE_AUDIENCE='$EXPOSURE_AUDIENCE' is not a valid value." >&2
      echo "[preflight] Allowed values (from configs/exposure_profiles.yaml): local_only, private_lan, vpn, public" >&2
      config_fail=1
    else
      echo "[preflight] ok: EXPOSURE_AUDIENCE=$EXPOSURE_AUDIENCE"
      # local_only + 0.0.0.0 bind address conflict.
      # EXPOSURE_AUDIENCE=local_only declares host-published ports are only reachable from the local
      # machine (loopback), but default bind is 0.0.0.0 (all interfaces). This contradicts the declaration.
      if [[ "$EXPOSURE_AUDIENCE" == "local_only" ]]; then
        LOCAL_ONLY_BIND_CONFLICT="$("$PYTHON_BIN" - "$CANONICAL_MODE" <<'PY'
import sys, os, yaml
from pathlib import Path
mode = sys.argv[1]
root = Path(os.environ.get("PWD", "."))
data = yaml.safe_load((root / "configs" / "exposure_profiles.yaml").read_text(encoding="utf-8"))
profiles = data.get("profiles", {})
services = data.get("services", {})
profile = profiles.get(mode, {})
published = profile.get("host_published", [])
conflicts = []
for svc_name in published:
    svc = services.get(svc_name, {})
    bind_env = svc.get("host_env_bind", "")
    default_bind = svc.get("default_bind", "0.0.0.0")
    actual_bind = os.environ.get(bind_env, default_bind) if bind_env else default_bind
    if actual_bind == "0.0.0.0":
        compose_svc = svc.get("compose_service", svc_name)
        port_env = svc.get("host_env_port", "")
        port = os.environ.get(port_env, str(svc.get("default_host_port", ""))) if port_env else ""
        conflicts.append(f"{compose_svc}:{port}")
if conflicts:
    print(",".join(conflicts[:5]))
PY
        )"
        if [[ -n "$LOCAL_ONLY_BIND_CONFLICT" ]]; then
          echo "[preflight] fail: EXPOSURE_AUDIENCE=local_only but services bound to 0.0.0.0: $LOCAL_ONLY_BIND_CONFLICT" >&2
          echo "[preflight] Set *_BIND_ADDR=127.0.0.1 for all host-published services, or change EXPOSURE_AUDIENCE." >&2
          config_fail=1
        else
          echo "[preflight] ok: EXPOSURE_AUDIENCE=local_only — all host-published services bound to loopback"
        fi
      fi
      if [[ "$EXPOSURE_AUDIENCE" == "public" ]]; then
        ALLOW_PUBLIC="${ALLOW_PUBLIC_OPERATIONS_ENDPOINTS:-}"
        if [[ "$ALLOW_PUBLIC" != "1" && "$ALLOW_PUBLIC" != "true" ]]; then
          echo "[preflight] fail: EXPOSURE_AUDIENCE=public requires ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true as explicit opt-in." >&2
          echo "[preflight] EXPOSURE_MODE=$CANONICAL_MODE with public audience exposes vLLM APIs and operations endpoints without Gateway auth." >&2
          config_fail=1
        else
          echo "[preflight] warn: EXPOSURE_AUDIENCE=public + ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true — vLLM and ops endpoints publicly reachable." >&2
        fi
      fi
    fi
  fi
fi

# Phase 1 complete: exit immediately on config errors before running expensive checks.
if [[ "$config_fail" != "0" ]]; then
  echo "[preflight] configuration preflight failed; fix exposure config before runtime checks." >&2
  exit 1
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

# Determine which ports to check based on EXPOSURE_MODE and configs/exposure_profiles.yaml.
# vLLM runtime ports from configs/ports.yaml (9401, 9402, 9403, 9406) are compose-network
# `expose` ports in private_network topology, so another local host process on those ports
# does not block the full-stack deployment.
# Monitoring and ops ports follow EXPOSURE_MODE: only check what is actually host-published.
#
# For each published service, check the actual bind address (from *_BIND_ADDR env or default)
# rather than hardcoding 127.0.0.1. This catches mismatches between declared bind and actual availability.

host_published_services=()
while IFS= read -r line; do
  [[ -n "$line" ]] && host_published_services+=("$line")
done < <("$PYTHON_BIN" - "$CANONICAL_MODE" <<'PY'
import sys, os, yaml
from pathlib import Path

mode = sys.argv[1]
root = Path(os.environ.get("PWD", "."))
data = yaml.safe_load((root / "configs" / "exposure_profiles.yaml").read_text(encoding="utf-8"))
profiles = data.get("profiles", {})
services = data.get("services", {})
profile = profiles.get(mode, {})
published_service_names = profile.get("host_published", [])

for svc_name in published_service_names:
    svc = services.get(svc_name, {})
    port_env = svc.get("host_env_port", "")
    bind_env = svc.get("host_env_bind", "")
    default_port = str(svc.get("default_host_port", ""))
    default_bind = str(svc.get("default_bind", "0.0.0.0"))
    port = os.environ.get(port_env, default_port) if port_env else default_port
    bind = os.environ.get(bind_env, default_bind) if bind_env else default_bind
    if port:
        # Output: svc_name|port|bind
        print(f"{svc_name}|{port}|{bind}")
PY
)

for entry in "${host_published_services[@]+"${host_published_services[@]}"}"; do
  svc_name="${entry%%|*}"
  rest="${entry#*|}"
  port="${rest%%|*}"
  bind="${rest#*|}"

  [[ -n "$port" ]] || continue

  if port_owned_by_compose "$port"; then
    echo "[preflight] ok: port $port ($svc_name) held by current compose stack"
  else
    # Check bind address: use 0.0.0.0 if bind is 0.0.0.0, else use the specific address.
    check_addr="$bind"
    if [[ "$bind" == "0.0.0.0" ]]; then
      check_addr="0.0.0.0"
    fi
    if "$PYTHON_BIN" - "$port" "$check_addr" <<'PY'
import socket, sys
port = int(sys.argv[1])
bind_addr = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
# For 0.0.0.0, try binding to 0.0.0.0; for specific IPs, bind to that IP.
s = socket.socket()
s.settimeout(0.4)
try:
    s.bind((bind_addr, port))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
    then
      echo "[preflight] ok: port $port ($svc_name) available on $bind"
    else
      echo "[preflight] busy: port $port ($svc_name) is already in use on $bind (EXPOSURE_MODE=$CANONICAL_MODE)" >&2
      fail=1
    fi
  fi
done

# Report structured diagnostics for the current canonical exposure mode.
if [[ "$CANONICAL_MODE" != "private_network" ]]; then
  diag_output="$("$PYTHON_BIN" - "$CANONICAL_MODE" <<'PY'
import sys, yaml
from pathlib import Path
mode = sys.argv[1]
data = yaml.safe_load((Path.cwd() / "configs" / "exposure_profiles.yaml").read_text(encoding="utf-8"))
profile = data.get("profiles", {}).get(mode, {})
diag = profile.get("diagnostics", {})
for k, v in diag.items():
    if v:
        print(f"  [diagnostic] {k}: {v}")
PY
)"
  if [[ -n "$diag_output" ]]; then
    echo "[preflight] EXPOSURE_MODE=$CANONICAL_MODE structured diagnostics:"
    echo "$diag_output"
  fi
fi

# Port env vars referenced in comments — actual port values are read dynamically above from
# configs/exposure_profiles.yaml services section: PROMETHEUS_PORT, GRAFANA_PORT, DCGM_EXPORTER_PORT, CADVISOR_PORT

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
