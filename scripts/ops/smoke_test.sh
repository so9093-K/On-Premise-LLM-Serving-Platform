#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
"$PYTHON_BIN" scripts/build/check_python.py --context smoke-test >/dev/null
# Smoke test는 항상 host에 노출된 포트를 대상으로 probe한다. .env의
# RISK_ADAPTER_BASE_URL은 compose 내부용 URL(http://risk-adapter:9405)이므로
# 여기서 사용하면 안 된다.
GATEWAY_PROBE_HOST="${GATEWAY_PROBE_HOST:-${GATEWAY_BIND_ADDR:-localhost}}"
if [[ -z "$GATEWAY_PROBE_HOST" || "$GATEWAY_PROBE_HOST" == "0.0.0.0" ]]; then
  GATEWAY_PROBE_HOST="localhost"
fi
GATEWAY_BASE_URL="http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT:-$(service_default_host_port gateway)}"
RISK_ADAPTER_BASE_URL="http://localhost:${RISK_ADAPTER_PORT:-$(service_default_host_port risk_adapter)}"
API_KEY="$(local_env_first_value "$ENV_FILE" API_KEY API_KEYS || true)"
# smoke는 일반 Gateway 경로를 그대로 호출한다. 별도 30초 상수를 두면 정상적인
# admission queue 대기보다 먼저 실패해 배포 rollback의 원인이 된다. 명시적
# SMOKE_MAX_REQUEST_SECONDS override가 없으면 Gateway의 기존 요청 예산을 쓴다.
SMOKE_MAX_REQUEST_SECONDS="${SMOKE_MAX_REQUEST_SECONDS:-${REQUEST_TIMEOUT_SECONDS:-30}}"
SMOKE_MAX_LATENCY_MS="${SMOKE_MAX_LATENCY_MS:-0}"
SMOKE_RETRY_ATTEMPTS="${SMOKE_RETRY_ATTEMPTS:-3}"
SMOKE_RETRY_DELAY_SECONDS="${SMOKE_RETRY_DELAY_SECONDS:-5}"
SMOKE_SKIP_RUNTIMES="${SMOKE_SKIP_RUNTIMES:-}"

# 모델 식별자는 운영 설정이 소유한다. 이 스크립트는 어떤 모델을 배포 gate로
# 확인할지(대표 chat, 기본/검색 embedding, prompt risk)만 결정한다.
while IFS=$'\t' read -r config_key config_value; do
  case "$config_key" in
    main_model) SMOKE_MAIN_MODEL="$config_value" ;;
    default_embedding_model) SMOKE_DEFAULT_EMBEDDING_MODEL="$config_value" ;;
    default_embedding_runtime) SMOKE_DEFAULT_EMBEDDING_RUNTIME="$config_value" ;;
    default_retrieval_model) SMOKE_RETRIEVAL_MODEL="$config_value" ;;
    retrieval_runtime) SMOKE_RETRIEVAL_RUNTIME="$config_value" ;;
    risk_prompt_runtime) SMOKE_RISK_PROMPT_RUNTIME="$config_value" ;;
    public_model_ids_json) SMOKE_PUBLIC_MODEL_IDS_JSON="$config_value" ;;
    *) echo "[smoke] unknown model configuration key: $config_key" >&2; exit 2 ;;
  esac
done < <("$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

import yaml

catalog = yaml.safe_load(Path("configs/model_catalog.yaml").read_text(encoding="utf-8"))
serving = yaml.safe_load(Path("configs/model_serving.yaml").read_text(encoding="utf-8"))
models = catalog["models"]

def required(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"invalid or missing {label}")
    return value

public_ids = sorted(
    model_id
    for model_id, metadata in models.items()
    if isinstance(metadata, dict)
    and isinstance(metadata.get("gateway_listing"), dict)
    and metadata["gateway_listing"].get("enabled") is True
)
if not public_ids:
    raise SystemExit("model_catalog.yaml has no gateway_listing.enabled models")

print("main_model\t" + required(serving["models"]["main_llm"]["served_model_name"], "models.main_llm.served_model_name"))
default_embedding_model = required(serving["default_embedding_model"], "default_embedding_model")
retrieval_model = required(serving["default_retrieval_model"], "default_retrieval_model")
embedding_profiles = serving["embedding_profiles"]
print("default_embedding_model\t" + default_embedding_model)
print("default_embedding_runtime\t" + required(embedding_profiles[default_embedding_model]["service_key"], "default embedding service_key"))
print("default_retrieval_model\t" + retrieval_model)
print("retrieval_runtime\t" + required(embedding_profiles[retrieval_model]["service_key"], "retrieval embedding service_key"))
print("risk_prompt_runtime\t" + required(serving["risk_adapter"]["detectors"]["prompt"]["service_key"], "risk_adapter.detectors.prompt.service_key"))
print("public_model_ids_json\t" + json.dumps(public_ids, separators=(",", ":")))
PY
)

: "${SMOKE_MAIN_MODEL:?failed to load main model from config}"
: "${SMOKE_DEFAULT_EMBEDDING_MODEL:?failed to load default embedding model from config}"
: "${SMOKE_DEFAULT_EMBEDDING_RUNTIME:?failed to load default embedding runtime from config}"
: "${SMOKE_RETRIEVAL_MODEL:?failed to load retrieval model from config}"
: "${SMOKE_RETRIEVAL_RUNTIME:?failed to load retrieval runtime from config}"
: "${SMOKE_RISK_PROMPT_RUNTIME:?failed to load risk prompt runtime from config}"
: "${SMOKE_PUBLIC_MODEL_IDS_JSON:?failed to load public model IDs from config}"
export SMOKE_PUBLIC_MODEL_IDS_JSON

AUTH_ARGS=()
if [[ -n "$API_KEY" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${API_KEY}")
fi
ADMIN_API_KEY="$(local_env_first_value "$ENV_FILE" ADMIN_API_KEY ADMIN_API_KEYS || true)"
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

post_json_with_retry() {
  local name="$1" attempt
  for attempt in $(seq 1 "${SMOKE_RETRY_ATTEMPTS}"); do
    if post_json "$@"; then
      return 0
    fi
    if [[ "${attempt}" -lt "${SMOKE_RETRY_ATTEMPTS}" ]]; then
      echo "[smoke] $1: transient failure (attempt ${attempt}/${SMOKE_RETRY_ATTEMPTS}), retrying in ${SMOKE_RETRY_DELAY_SECONDS}s..." >&2
      sleep "${SMOKE_RETRY_DELAY_SECONDS}"
    fi
  done
  echo "[smoke] ${name}: failed after ${SMOKE_RETRY_ATTEMPTS} attempt(s); request deadline=${SMOKE_MAX_REQUEST_SECONDS}s" >&2
  return 1
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
    # Smoke test는 배포 gate이다. 명시적으로 완화하지 않는 한 degraded/not_ready에서는 실패한다.
    import os
    if os.getenv("SMOKE_ALLOW_DEGRADED_READY") == "1":
        allowed.add("degraded")
    require(doc.get("status") in allowed, f"readiness status must be one of {sorted(allowed)}")
    require(isinstance(doc.get("dependencies"), list), "dependencies must be a list")
elif check == "models":
    require(doc.get("object") == "list", "object must be list")
    ids = {item.get("id") for item in doc.get("data", [])}
    import os
    expected = set(json.loads(os.environ["SMOKE_PUBLIC_MODEL_IDS_JSON"]))
    require(expected.issubset(ids), f"missing public model id: {sorted(expected - ids)}")
elif check == "risk":
    require(doc.get("assessment_id"), "assessment_id is required")
    require(doc.get("status") in {"completed", "partial", "failed"}, "invalid risk status")
    require(not (FORBIDDEN & set(doc)), "risk response includes forbidden policy fields")
elif check == "chat":
    require(doc.get("object") == "chat.completion", "object must be chat.completion")
    require(isinstance(doc.get("choices"), list) and doc["choices"], "choices must be non-empty")
    choice = doc["choices"][0]
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    require(isinstance(content, str), "structured chat content must be a string")
    try:
        structured = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        require(False, "structured chat content must be valid JSON")
    require(isinstance(structured, dict), "structured chat content must be an object")
    require(isinstance(structured.get("ok"), bool), "structured chat content must contain boolean ok")
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

skip_runtime() {
  local runtime="$1"
  case ",${SMOKE_SKIP_RUNTIMES}," in
    *",${runtime},"*) return 0 ;;
    *) return 1 ;;
  esac
}

get_json gateway-health "$GATEWAY_BASE_URL/health"
assert_json health
get_json gateway-ready "$GATEWAY_BASE_URL/ready" admin
assert_json ready
get_json gateway-models "$GATEWAY_BASE_URL/v1/models"
assert_json models

if skip_runtime "$SMOKE_RISK_PROMPT_RUNTIME"; then
  echo "[smoke] ${SMOKE_RISK_PROMPT_RUNTIME} runtime is deferred; skipping risk inference probes" >&2
else
  post_json_with_retry gateway-risk-aggregate "$GATEWAY_BASE_URL/v1/risk/assessments" \
    '{"prompt":"smoke test prompt"}'
  assert_json risk
fi

# Private-network compose에서는 risk-adapter 포트가 host에 노출되지 않는다.
# 접근 가능할 때만 직접 프로브를 실행하고, 아닐 경우 gateway 경유 테스트로 검증한다.
if skip_runtime "$SMOKE_RISK_PROMPT_RUNTIME"; then
  :
elif curl -sS --max-time 3 -o /dev/null "$RISK_ADAPTER_BASE_URL/health" 2>/dev/null; then
  get_json risk-health "$RISK_ADAPTER_BASE_URL/health"
  assert_json health
  get_json risk-ready "$RISK_ADAPTER_BASE_URL/ready" admin
  assert_json ready

  post_json_with_retry risk-prompt "$RISK_ADAPTER_BASE_URL/v1/risk/detectors/prompt/assessments" \
    '{"prompt":"smoke test prompt"}' internal
  assert_json risk

  post_json_with_retry risk-aggregate "$RISK_ADAPTER_BASE_URL/v1/risk/assessments" \
    '{"prompt":"smoke test prompt"}' internal
  assert_json risk
else
  echo "[smoke] risk-adapter: host port not accessible (private-network compose); gateway /v1/risk/assessments covers risk path" >&2
fi

post_json_with_retry chat "$GATEWAY_BASE_URL/v1/chat/completions" \
  "{\"model\":\"${SMOKE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Return exactly a JSON object with boolean field ok.\"}],\"max_tokens\":16,\"temperature\":0,\"response_format\":{\"type\":\"json_schema\",\"json_schema\":{\"name\":\"smoke_result\",\"strict\":true,\"schema\":{\"type\":\"object\",\"properties\":{\"ok\":{\"type\":\"boolean\"}},\"required\":[\"ok\"],\"additionalProperties\":false}}}}"
assert_json chat

if skip_runtime "$SMOKE_DEFAULT_EMBEDDING_RUNTIME"; then
  echo "[smoke] embedding runtime is deferred; skipping ${SMOKE_DEFAULT_EMBEDDING_MODEL} probe" >&2
else
  post_json_with_retry embedding "$GATEWAY_BASE_URL/v1/embeddings" \
    "{\"model\":\"${SMOKE_DEFAULT_EMBEDDING_MODEL}\",\"input\":[\"smoke test embedding\"]}"
  assert_json embedding
fi

if [[ "$SMOKE_RETRIEVAL_MODEL" == "$SMOKE_DEFAULT_EMBEDDING_MODEL" ]]; then
  echo "[smoke] retrieval model matches default embedding model; skipping duplicate embedding probe" >&2
elif skip_runtime "$SMOKE_RETRIEVAL_RUNTIME"; then
  echo "[smoke] ${SMOKE_RETRIEVAL_RUNTIME} runtime is deferred; skipping ${SMOKE_RETRIEVAL_MODEL} probe" >&2
else
  post_json_with_retry embedding-ko "$GATEWAY_BASE_URL/v1/embeddings" \
    "{\"model\":\"${SMOKE_RETRIEVAL_MODEL}\",\"input\":[\"smoke test Korean retrieval embedding\"]}"
  assert_json embedding
fi

echo "smoke tests completed"
