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
"$PYTHON_BIN" scripts/build/check_python.py --context ready-full >/dev/null
MAIN_MODEL_NAME="$(model_serving_runtime_model_name main_llm)"
# Health probe는 host에 노출된 Gateway bind address를 대상으로 한다. Gateway가
# 0.0.0.0으로 bind하면 localhost도 유효하지만, private interface IP로 bind한 경우
# localhost는 listen하지 않으므로 probe는 그 명시적인 bind address를 사용해야 한다.
GATEWAY_PROBE_HOST="${GATEWAY_PROBE_HOST:-${GATEWAY_BIND_ADDR:-localhost}}"
if [[ -z "$GATEWAY_PROBE_HOST" || "$GATEWAY_PROBE_HOST" == "0.0.0.0" ]]; then
  GATEWAY_PROBE_HOST="localhost"
fi
GATEWAY_BASE_URL="http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT:-$(service_default_host_port gateway)}"
READY_FULL_TIMEOUT_SECONDS="${READY_FULL_TIMEOUT_SECONDS:-1800}"
READY_FULL_INTERVAL_SECONDS="${READY_FULL_INTERVAL_SECONDS:-10}"
# admin-sidecar를 재생성하면(배포마다 PLATFORM_IMAGE가 bump됨) main-model inference
# gate가 닫힌다. boot reconcile이 persisted profile을 재검증할 때까지 gateway는
# main-model chat에 503을 반환한다. /ready에는 이 gate 상태가 반영되지 않으므로,
# ready-full은 엄격한 smoke gate 전에 chat이 실제로 서빙될 때까지 기다린다.
# 이 budget은 model-load budget(READY_FULL_TIMEOUT_SECONDS)과 동일하게 맞춘다:
# boot reconcile이 main model을 교체해야 하는 경우(observed != persisted target)
# gate는 단 몇 초가 아니라 모델 전체 reload 시간만큼 닫혀 있기 때문이다. fast path
# (gate가 이미 열려 있는 경우)는 이 상한과 무관하게 즉시 반환된다.
READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS="${READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS:-${READY_FULL_TIMEOUT_SECONDS}}"
# main-model gate probe도 일반 Gateway 경로를 통과한다. 이 probe만 30초로 고정하면
# 정상적인 queue 대기보다 먼저 끊겨 readiness가 거짓 실패할 수 있으므로 Gateway
# 요청 예산을 기본값으로 공유한다.
READY_FULL_MAIN_MODEL_REQUEST_SECONDS="${READY_FULL_MAIN_MODEL_REQUEST_SECONDS:-${REQUEST_TIMEOUT_SECONDS:-30}}"
# 모델별 chat template가 assistant text를 emit하기 전에 소비하는 token 수가 다를 수
# 있으므로 운영 환경에서 override할 수 있게 한다. 기본값 16은 현재 지원 profile의
# non-reasoning canary를 충족하면서 readiness 요청 비용을 작게 유지한다.
READY_FULL_MAIN_MODEL_MAX_TOKENS="${READY_FULL_MAIN_MODEL_MAX_TOKENS:-16}"
if [[ ! "$READY_FULL_MAIN_MODEL_MAX_TOKENS" =~ ^[1-9][0-9]*$ ]]; then
  echo "[ready-full] READY_FULL_MAIN_MODEL_MAX_TOKENS must be a positive integer" >&2
  exit 2
fi
SMOKE_SKIP_RUNTIMES="${SMOKE_SKIP_RUNTIMES:-}"

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

# control-plane 재배포 후 main-model inference gate가 다시 열리기를 기다린다(실패 시
# fatal). gate가 닫혀 있는 동안 gateway는 main-model chat에 503(MAIN_MODEL_SWITCH_IN_PROGRESS)을
# 반환하므로, 실제 chat 경로를 200이 나올 때까지 폴링한다. gate-closed 503은 그저
# "계속 기다려라"는 의미일 뿐이며, 진짜 timeout이 발생했을 때만 배포를 실패 처리한다.
wait_for_main_model_ready() {
  local url="$GATEWAY_BASE_URL/v1/chat/completions"
  # 1 token은 Gemma가 assistant text를 emit하기 전에 finish_reason=length로 끝날 수
  # 있어, 열린 gate를 UPSTREAM_SCHEMA_ERROR로 오진한다. 현재 운영 runtime에서
  # non-reasoning 최소 응답이 정상 종료하는 16 token을 readiness probe 계약으로 쓴다.
  # reasoning 필드는 이를 지원하지 않는 다른 profile도 있으므로 보내지 않는다.
  local body="{\"model\":\"${MAIN_MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with OK.\"}],\"max_tokens\":${READY_FULL_MAIN_MODEL_MAX_TOKENS},\"temperature\":0}"
  local start=$SECONDS deadline=$((SECONDS + READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS))
  local attempt=0 code elapsed tmp detail error_code
  tmp="$(mktemp)"
  local curl_args=(-sS --max-time "$READY_FULL_MAIN_MODEL_REQUEST_SECONDS" -o "$tmp" -w '%{http_code}'
    -H 'Content-Type: application/json' -d "$body")
  [[ -n "$API_KEY" ]] && curl_args+=(-H "Authorization: Bearer ${API_KEY}")
  echo "[ready-full] waiting for main-model gate to open (${MAIN_MODEL_NAME} chat, up to ${READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS}s; request deadline ${READY_FULL_MAIN_MODEL_REQUEST_SECONDS}s)..."
  while true; do
    attempt=$((attempt + 1))
    code="$(curl "${curl_args[@]}" "$url" 2>/dev/null || true)"
    elapsed=$((SECONDS - start))
    if [[ "$code" == "200" ]]; then
      rm -f "$tmp"
      echo "[ready-full] main-model chat serving (${elapsed}s, attempt ${attempt})"
      return 0
    fi
    # 실패를 그 자리에서 진단할 수 있도록 gateway의 에러를 그대로 노출한다.
    # 여기서 MAIN_MODEL_CONTROL_UNAVAILABLE은 gateway가 admin-sidecar로부터 gate
    # 상태를 읽지 못한다는 의미이므로(예: sidecar의 /main-model이 에러를 내는 경우)
    # 아래 diagnostics에서 admin-sidecar 로그를 확인해야 한다. MAIN_MODEL_SWITCH_IN_PROGRESS는
    # gate가 아직 정상적으로 재개방되는 중이라는 의미이므로 계속 기다리면 된다.
    detail="$(tr '\n' ' ' < "$tmp" 2>/dev/null | cut -c1-300)"
    error_code="$("$PYTHON_BIN" - "$tmp" <<'PY'
import json
import sys

try:
    body = json.load(open(sys.argv[1], encoding="utf-8"))
    print(str((body.get("error") or {}).get("code") or ""))
except Exception:
    print("")
PY
    )"
    # 요청 자체가 잘못됐거나 응답 contract가 결정론적으로 실패한 경우는 gate가
    # 열리기를 기다린다고 회복되지 않는다. 즉시 실패해 30분 polling과 늦은 rollback을 막는다.
    if [[ "$code" =~ ^4[0-9][0-9]$ || "$error_code" == "UPSTREAM_SCHEMA_ERROR" ]]; then
      echo "[ready-full] main-model gate probe failed permanently (HTTP ${code:-000}, error=${error_code:-unknown}): ${detail}" >&2
      rm -f "$tmp"
      return 1
    fi
    if (( SECONDS >= deadline )); then
      echo "[ready-full] main-model chat not serving after ${READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS}s (last HTTP ${code:-000}): ${detail}" >&2
      echo "[ready-full] gate did not open — inspect admin-sidecar logs in the diagnostics that follow" >&2
      rm -f "$tmp"
      return 1
    fi
    echo "[ready-full] main-model gate not open yet (HTTP ${code:-000}, ${elapsed}s): ${detail}" >&2
    sleep "$READY_FULL_INTERVAL_SECONDS"
  done
}

wait_for_probe "gateway /health" "$GATEWAY_BASE_URL/health" 200

# /health 통과 후 vLLM upstream이 모두 로드될 때까지 /ready를 기다린다.
# compose 서비스에는 로컬 run/*.pid 파일이 없으므로, readiness는 HTTP로 폴링한다.
wait_for_gateway_ready "$GATEWAY_BASE_URL/ready" "$ADMIN_API_KEY"

# 엄격한 smoke gate 전에 readiness dependency 목록을 출력한다.
bash scripts/ops/status_services.sh --full || true

# control-plane 재배포 후 admin-sidecar가 main-model gate를 닫았다가 boot reconcile로
# 다시 연다. gate가 닫혀 있는 동안 main-model chat은 503이고 /ready엔 반영되지 않으므로,
# smoke(엄격 gate) 전에 chat이 실제로 200을 줄 때까지 기다린다.
wait_for_main_model_ready

bash scripts/ops/smoke_test.sh
