#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/load_env.sh
load_local_env .env

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
# Smoke tests always probe host-published ports; RISK_ADAPTER_BASE_URL in .env is the
# compose-internal URL (http://risk-adapter:9405) and must not be used here.
GATEWAY_BASE_URL="http://localhost:${GATEWAY_PORT:-9400}"
RISK_ADAPTER_BASE_URL="http://localhost:${RISK_ADAPTER_PORT:-9405}"
API_KEY="$(local_env_first_value .env API_KEY API_KEYS || true)"
SMOKE_MAX_REQUEST_SECONDS="${SMOKE_MAX_REQUEST_SECONDS:-30}"
SMOKE_MAX_LATENCY_MS="${SMOKE_MAX_LATENCY_MS:-0}"

AUTH_ARGS=()
if [[ -n "$API_KEY" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${API_KEY}")
fi
ADMIN_API_KEY="$(local_env_first_value .env ADMIN_API_KEY ADMIN_API_KEYS || true)"
ADMIN_AUTH_ARGS=()
if [[ -n "$ADMIN_API_KEY" ]]; then
  ADMIN_AUTH_ARGS=(-H "Authorization: Bearer ${ADMIN_API_KEY}")
fi

INTERNAL_SERVICE_TOKEN="${INTERNAL_SERVICE_TOKEN:-}"
INTERNAL_AUTH_ARGS=()
if [[ -n "$INTERNAL_SERVICE_TOKEN" ]]; then
  INTERNAL_AUTH_ARGS=(-H "Authorization: Bearer ${INTERNAL_SERVICE_TOKEN}")
fi

tmp_json="$(mktemp)"
trap 'rm -f "$tmp_json"' EXIT

now_ns() {
  "$PYTHON_BIN" - <<'PY'
import time
print(time.monotonic_ns())
PY
}

check_latency() {
  local name="$1"
  local elapsed_ms="$2"
  if [[ "$SMOKE_MAX_LATENCY_MS" != "0" && "$elapsed_ms" -gt "$SMOKE_MAX_LATENCY_MS" ]]; then
    echo "${name} exceeded latency threshold: ${elapsed_ms}ms > ${SMOKE_MAX_LATENCY_MS}ms" >&2
    exit 1
  fi
}

get_json() {
  local name="$1"
  local url="$2"
  local auth_mode="${3:-external}"
  local start end elapsed
  local -a selected_auth
  if [[ "$auth_mode" == "admin" ]]; then
    selected_auth=("${ADMIN_AUTH_ARGS[@]}")
  else
    selected_auth=("${AUTH_ARGS[@]}")
  fi
  start="$(now_ns)"
  curl --max-time "$SMOKE_MAX_REQUEST_SECONDS" -fsS "${selected_auth[@]}" "$url" > "$tmp_json"
  end="$(now_ns)"
  elapsed=$(( (end - start) / 1000000 ))
  check_latency "$name" "$elapsed"
}

post_json() {
  local name="$1"
  local url="$2"
  local body="$3"
  local auth_mode="${4:-external}"
  local start end elapsed
  local -a selected_auth
  if [[ "$auth_mode" == "internal" ]]; then
    selected_auth=("${INTERNAL_AUTH_ARGS[@]}")
  else
    selected_auth=("${AUTH_ARGS[@]}")
  fi
  start="$(now_ns)"
  curl --max-time "$SMOKE_MAX_REQUEST_SECONDS" -fsS -X POST "$url" \
    "${selected_auth[@]}" \
    -H 'Content-Type: application/json' \
    -d "$body" > "$tmp_json"
  end="$(now_ns)"
  elapsed=$(( (end - start) / 1000000 ))
  check_latency "$name" "$elapsed"
}

assert_json() {
  local check_name="$1"
  "$PYTHON_BIN" - "$check_name" "$tmp_json" <<'PY'
import json
import sys

check = sys.argv[1]
path = sys.argv[2]
with open(path, encoding="utf-8") as fh:
    doc = json.load(fh)

FORBIDDEN = {
    "allow", "review", "block", "decision", "action", "safe_to_send",
    "final_decision", "final_decision_owner", "policy_overrides",
}

def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"{check} failed: {message}")

if check == "health":
    require(doc.get("status") == "ok", "status must be ok")
    require(isinstance(doc.get("service"), str) and doc["service"], "service is required")
elif check == "ready":
    allowed = {"ready"}
    # Smoke tests are deployment gates. They fail on degraded/not_ready unless explicitly relaxed.
    import os
    if os.getenv("SMOKE_ALLOW_DEGRADED_READY") == "1":
        allowed.add("degraded")
    require(doc.get("status") in allowed, f"readiness status must be one of {sorted(allowed)}")
    require(isinstance(doc.get("dependencies"), list), "dependencies must be a list")
elif check == "models":
    require(doc.get("object") == "list", "object must be list")
    ids = {item.get("id") for item in doc.get("data", [])}
    require({"local-main", "local-embed", "risk-prompt"}.issubset(ids), "missing logical model id")
elif check == "risk":
    require(doc.get("assessment_id"), "assessment_id is required")
    require(doc.get("status") in {"completed", "partial", "failed"}, "invalid risk status")
    require(not (FORBIDDEN & set(doc)), "risk response includes forbidden policy fields")
elif check == "chat":
    require(doc.get("object") == "chat.completion", "object must be chat.completion")
    require(isinstance(doc.get("choices"), list) and doc["choices"], "choices must be non-empty")
elif check == "embedding":
    require(doc.get("object") == "list", "object must be list")
    data = doc.get("data")
    require(isinstance(data, list) and data, "data must be non-empty")
    embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
    require(isinstance(embedding, list) and embedding, "embedding vector must be non-empty")
else:
    raise SystemExit(f"unknown check: {check}")
PY
}

get_json gateway-health "$GATEWAY_BASE_URL/health"
assert_json health
get_json gateway-ready "$GATEWAY_BASE_URL/ready" admin
assert_json ready
get_json gateway-models "$GATEWAY_BASE_URL/v1/models"
assert_json models

post_json gateway-risk-aggregate "$GATEWAY_BASE_URL/v1/risk/assessments" \
  '{"prompt":"smoke test prompt"}'
assert_json risk

get_json risk-health "$RISK_ADAPTER_BASE_URL/health"
assert_json health
get_json risk-ready "$RISK_ADAPTER_BASE_URL/ready" admin
assert_json ready

post_json risk-prompt "$RISK_ADAPTER_BASE_URL/v1/risk/detectors/prompt/assessments" \
  '{"prompt":"smoke test prompt"}' internal
assert_json risk

post_json risk-aggregate "$RISK_ADAPTER_BASE_URL/v1/risk/assessments" \
  '{"prompt":"smoke test prompt"}' internal
assert_json risk

post_json chat "$GATEWAY_BASE_URL/v1/chat/completions" \
  '{"model":"local-main","messages":[{"role":"user","content":"Say OK only."}],"max_tokens":1,"temperature":0}'
assert_json chat

post_json embedding "$GATEWAY_BASE_URL/v1/embeddings" \
  '{"model":"local-embed","input":["smoke test embedding"]}'
assert_json embedding

echo "smoke tests completed"
