#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
export ENV_FILE COMPOSE_FILE
load_local_env "$ENV_FILE"

ADMIN_API_KEY="$(local_env_first_value "$ENV_FILE" ADMIN_API_KEY ADMIN_API_KEYS || true)"
API_KEY="$(local_env_first_value "$ENV_FILE" API_KEY API_KEYS || true)"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
# Health probes target the host-published Gateway bind address. When Gateway binds
# to 0.0.0.0, localhost is valid; when it binds to a private interface IP, localhost
# is not listening and the probe must use that explicit bind address.
GATEWAY_PROBE_HOST="${GATEWAY_PROBE_HOST:-${GATEWAY_BIND_ADDR:-localhost}}"
if [[ -z "$GATEWAY_PROBE_HOST" || "$GATEWAY_PROBE_HOST" == "0.0.0.0" ]]; then
  GATEWAY_PROBE_HOST="localhost"
fi
GATEWAY_BASE_URL="http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT:-9400}"
# RISK_ADAPTER_BASE_URL is the internal service-to-service URL (e.g.
# http://risk-adapter:9405 in compose) and must not be used for host-side checks.
RISK_ADAPTER_BASE_URL="http://localhost:${RISK_ADAPTER_PORT:-9405}"
READY_FULL_TIMEOUT_SECONDS="${READY_FULL_TIMEOUT_SECONDS:-1800}"
READY_FULL_INTERVAL_SECONDS="${READY_FULL_INTERVAL_SECONDS:-10}"
# After /ready 200, vLLM may transiently 503 on first inference requests before
# its engine is fully accepting work. This warmup step polls a lightweight inference
# endpoint until it succeeds — separating "stack is up" from "inference is serving".
READY_FULL_INFERENCE_WARMUP_SECONDS="${READY_FULL_INFERENCE_WARMUP_SECONDS:-120}"

"$PYTHON_BIN" scripts/build/check_python.py --context ready-full >/dev/null
"$PYTHON_BIN" scripts/compose/validate_vllm_compose.py >/dev/null

run_diagnostics() {
  echo "[ready-full] collecting compose diagnostics" >&2
  bash scripts/compose/compose_diagnostics.sh >&2 || true
}

on_error() {
  local rc=$?
  echo "[ready-full] failed with exit code ${rc}" >&2
  run_diagnostics
  exit "$rc"
}
trap on_error ERR

http_probe() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local bearer="${4:-}"
  local tmp code curl_args
  tmp="$(mktemp)"
  curl_args=(-sS --max-time 5 -o "$tmp" -w '%{http_code}')
  [[ -n "$bearer" ]] && curl_args+=(-H "Authorization: Bearer ${bearer}")
  code="$(curl "${curl_args[@]}" "$url" 2>/dev/null || true)"
  if [[ "$code" == "$expected" ]]; then
    rm -f "$tmp"
    return 0
  fi
  if [[ "$code" == "200" || "$code" == "503" ]]; then
    echo "[ready-full] ${name}: HTTP ${code} $(tr '\n' ' ' < "$tmp" | cut -c1-500)" >&2
  else
    echo "[ready-full] ${name}: unavailable HTTP ${code:-000}" >&2
  fi
  rm -f "$tmp"
  return 1
}

wait_for_probe() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local bearer="${4:-}"
  local deadline now
  deadline=$((SECONDS + READY_FULL_TIMEOUT_SECONDS))
  while true; do
    if http_probe "$name" "$url" "$expected" "$bearer"; then
      echo "[ready-full] ${name}: ok"
      return 0
    fi
    now=$SECONDS
    if (( now >= deadline )); then
      echo "[ready-full] ${name}: timed out after ${READY_FULL_TIMEOUT_SECONDS}s waiting for HTTP ${expected} at ${url}" >&2
      return 1
    fi
    sleep "$READY_FULL_INTERVAL_SECONDS"
  done
}

# /ready 전용 폴링 함수.
# 503 응답의 dependency 목록을 파싱해 어떤 백엔드가 아직 로딩 중인지 표시하고,
# 경과/잔여 시간을 함께 출력해 크래시와 로딩 중을 구별할 수 있게 한다.
wait_for_gateway_ready() {
  local url="$1"
  local bearer="${2:-}"
  local start=$SECONDS deadline attempt=0 now elapsed remaining tmp code not_ready
  deadline=$((SECONDS + READY_FULL_TIMEOUT_SECONDS))

  echo "[ready-full] gateway /ready: vLLM 모델 로딩 대기 중 (최대 ${READY_FULL_TIMEOUT_SECONDS}s) ..."
  echo "[ready-full] tip: vLLM 컨테이너는 main-llm → embedding → risk-prompt 순으로 기동합니다 (enabled runtime healthcheck 기준)." >&2
  echo "[ready-full] tip: 최초 실행은 HuggingFace 다운로드·캐시·quantization 초기화가 겹쳐 30분을 넘을 수 있습니다. 필요하면 READY_FULL_TIMEOUT_SECONDS를 늘리세요." >&2

  while true; do
    attempt=$((attempt + 1))
    tmp="$(mktemp)"
    local curl_args=(-sS --max-time 5 -o "$tmp" -w '%{http_code}')
    [[ -n "$bearer" ]] && curl_args+=(-H "Authorization: Bearer ${bearer}")
    code="$(curl "${curl_args[@]}" "$url" 2>/dev/null || true)"
    now=$SECONDS
    elapsed=$((now - start))
    remaining=$((deadline - now))

    if [[ "$code" == "200" ]]; then
      rm -f "$tmp"
      echo "[ready-full] gateway /ready: ok (${elapsed}s 소요)"
      return 0
    fi

    if (( now >= deadline )); then
      rm -f "$tmp"
      echo "[ready-full] gateway /ready: ${READY_FULL_TIMEOUT_SECONDS}s timeout — 로그를 확인하세요: COMPOSE_FILE=${COMPOSE_FILE} ENV_FILE=${ENV_FILE} make compose-logs" >&2
      return 1
    fi

    if [[ "$code" == "503" ]]; then
      not_ready="$("$PYTHON_BIN" - "$tmp" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    entries = []
    for dep in data.get("dependencies", []):
        if not isinstance(dep, dict) or dep.get("status") == "ready":
            continue
        name = dep.get("name", "?")
        message = dep.get("message")
        if message:
            message = " ".join(str(message).split())
            entries.append(f"{name} ({message[:180]})")
        else:
            entries.append(str(name))
    print(", ".join(entries) if entries else "?")
except Exception:
    print("?")
PY
      2>/dev/null || echo "?")"
      # 첫 번째 시도와 이후 60초마다 상세 출력, 그 사이는 한 줄 요약
      if (( attempt == 1 )) || (( elapsed > 0 && elapsed % 60 < READY_FULL_INTERVAL_SECONDS )); then
        echo "[ready-full] gateway /ready: 로딩 중 — [${not_ready}] (${elapsed}s 경과, 최대 ${remaining}s 남음)" >&2
      else
        echo "[ready-full] gateway /ready: 로딩 중 — [${not_ready}] (${elapsed}s)" >&2
      fi
    elif [[ "$code" == "401" ]]; then
      echo "[ready-full] gateway /ready: 인증 실패 (401) — 실행 중인 gateway가 현재 .env의 ADMIN_API_KEY와 다른 secret으로 떠 있을 수 있습니다." >&2
      echo "[ready-full] hint: compose 서비스를 재생성하거나, stale shell env를 지우고 다시 실행하세요: unset ADMIN_API_KEY ADMIN_API_KEYS" >&2
      rm -f "$tmp"
      return 1
    else
      echo "[ready-full] gateway /ready: HTTP ${code:-000} (${elapsed}s 경과) — 서비스 상태를 확인하세요" >&2
    fi
    rm -f "$tmp"
    sleep "$READY_FULL_INTERVAL_SECONDS"
  done
}

# Warm a single inference endpoint until it serves (2xx) or the per-endpoint
# budget elapses. A vLLM may report /ready 200 yet transiently 503 its first
# inference requests before the engine accepts work; this absorbs that race so
# the strict smoke gate never sees it.
warm_one_endpoint() {
  local name="$1" url="$2" body="$3" bearer="${4:-}"
  local deadline=$((SECONDS + READY_FULL_INFERENCE_WARMUP_SECONDS))
  local attempt=0
  local curl_args=(-sf -X POST --max-time 20
    -H 'Content-Type: application/json'
    -d "$body")
  [[ -n "$bearer" ]] && curl_args+=(-H "Authorization: Bearer ${bearer}")
  while true; do
    attempt=$((attempt + 1))
    if curl "${curl_args[@]}" "$url" >/dev/null 2>&1; then
      echo "[ready-full] ${name}: serving (attempt ${attempt})"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "[ready-full] ${name}: not serving after ${READY_FULL_INFERENCE_WARMUP_SECONDS}s — stack may be degraded" >&2
      return 1
    fi
    echo "[ready-full] ${name}: not yet serving (attempt ${attempt}), retrying in 10s..." >&2
    sleep 10
  done
}

# Warm every inference path the smoke gate will hard-test. Models that were not
# recreated are already warm and return immediately; only a genuinely cold model
# (freshly (re)started) consumes its warmup budget. This makes the gate reliable
# regardless of how many models the deploy actually replaced.
wait_for_inference_ready() {
  echo "[ready-full] warming inference paths (up to ${READY_FULL_INFERENCE_WARMUP_SECONDS}s each)..."
  warm_one_endpoint "risk" "$GATEWAY_BASE_URL/v1/risk/assessments" \
    '{"prompt":"warmup"}' "$API_KEY"
  warm_one_endpoint "chat (local-main)" "$GATEWAY_BASE_URL/v1/chat/completions" \
    '{"model":"local-main","messages":[{"role":"user","content":"ok"}],"max_tokens":1,"temperature":0}' "$API_KEY"
  warm_one_endpoint "embedding (local-embed)" "$GATEWAY_BASE_URL/v1/embeddings" \
    '{"model":"local-embed","input":["warmup"]}' "$API_KEY"
  warm_one_endpoint "embedding (local-embed-ko)" "$GATEWAY_BASE_URL/v1/embeddings" \
    '{"model":"local-embed-ko","input":["warmup"]}' "$API_KEY"
}

wait_for_probe "gateway /health" "$GATEWAY_BASE_URL/health" 200

# Risk Adapter health은 private-network compose에서는 host port가 없어 직접 접근이 안 된다.
# 접근 가능할 때만 확인하고, 실패하면 gateway /ready가 risk adapter 상태를 포함해 검증한다.
if http_probe "risk-adapter /health" "$RISK_ADAPTER_BASE_URL/health" 200 2>/dev/null; then
  echo "[ready-full] risk-adapter /health: ok"
else
  echo "[ready-full] risk-adapter /health: host port not accessible (private-network compose); gateway /ready will verify risk adapter readiness" >&2
fi

# /health 통과 후 vLLM upstream이 모두 로드될 때까지 /ready를 기다린다.
# compose services do not have local run/*.pid files; readiness is polled via HTTP.
wait_for_gateway_ready "$GATEWAY_BASE_URL/ready" "$ADMIN_API_KEY"

# Print readiness dependencies before the strict smoke gate.
bash scripts/ops/status_services.sh --full || true

# /ready 200 이후에도 갓 (재)기동된 vLLM은 첫 inference 요청을 503으로 반환할 수 있다.
# smoke가 hard-test하는 모든 모델 경로(risk·chat·embedding·embedding-ko)를
# 실제로 응답할 때까지 warmup한 뒤 smoke_test.sh로 진입한다.
wait_for_inference_ready

bash scripts/ops/smoke_test.sh
