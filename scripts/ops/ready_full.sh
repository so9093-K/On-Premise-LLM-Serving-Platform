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
# Health probe는 host에 노출된 Gateway bind address를 대상으로 한다. Gateway가
# 0.0.0.0으로 bind하면 localhost도 유효하지만, private interface IP로 bind한 경우
# localhost는 listen하지 않으므로 probe는 그 명시적인 bind address를 사용해야 한다.
GATEWAY_PROBE_HOST="${GATEWAY_PROBE_HOST:-${GATEWAY_BIND_ADDR:-localhost}}"
if [[ -z "$GATEWAY_PROBE_HOST" || "$GATEWAY_PROBE_HOST" == "0.0.0.0" ]]; then
  GATEWAY_PROBE_HOST="localhost"
fi
GATEWAY_BASE_URL="http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT:-9400}"
# RISK_ADAPTER_BASE_URL은 서비스 간 내부 통신용 URL이므로(예: compose에서
# http://risk-adapter:9405) host 측 점검에는 사용하면 안 된다.
RISK_ADAPTER_BASE_URL="http://localhost:${RISK_ADAPTER_PORT:-9405}"
READY_FULL_TIMEOUT_SECONDS="${READY_FULL_TIMEOUT_SECONDS:-1800}"
READY_FULL_INTERVAL_SECONDS="${READY_FULL_INTERVAL_SECONDS:-10}"
# /ready가 200이 된 이후에도 vLLM 엔진이 완전히 요청을 받아들이기 전까지는
# 첫 inference 요청이 일시적으로 503을 반환할 수 있다. 이 warmup 단계는 가벼운
# inference endpoint를 성공할 때까지 폴링해 "스택이 떴다"와 "inference가 실제로
# 서빙 중이다"를 구분한다.
READY_FULL_INFERENCE_WARMUP_SECONDS="${READY_FULL_INFERENCE_WARMUP_SECONDS:-120}"
# admin-sidecar를 재생성하면(배포마다 PLATFORM_IMAGE가 bump됨) main-model inference
# gate가 닫힌다. boot reconcile이 persisted profile을 재검증할 때까지 gateway는
# local-main chat에 503을 반환한다. /ready에는 이 gate 상태가 반영되지 않으므로,
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
SMOKE_SKIP_RUNTIMES="${SMOKE_SKIP_RUNTIMES:-}"

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

# control-plane 재배포 후 main-model inference gate가 다시 열리기를 기다린다(실패 시
# fatal). gate가 닫혀 있는 동안 gateway는 local-main chat에 503(MAIN_MODEL_SWITCH_IN_PROGRESS)을
# 반환하므로, 실제 chat 경로를 200이 나올 때까지 폴링한다. gate-closed 503은 그저
# "계속 기다려라"는 의미일 뿐이며, 진짜 timeout이 발생했을 때만 배포를 실패 처리한다.
wait_for_main_model_ready() {
  local url="$GATEWAY_BASE_URL/v1/chat/completions"
  local body='{"model":"local-main","messages":[{"role":"user","content":"ok"}],"max_tokens":1,"temperature":0}'
  local start=$SECONDS deadline=$((SECONDS + READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS))
  local attempt=0 code elapsed tmp detail
  tmp="$(mktemp)"
  local curl_args=(-sS --max-time "$READY_FULL_MAIN_MODEL_REQUEST_SECONDS" -o "$tmp" -w '%{http_code}'
    -H 'Content-Type: application/json' -d "$body")
  [[ -n "$API_KEY" ]] && curl_args+=(-H "Authorization: Bearer ${API_KEY}")
  echo "[ready-full] waiting for main-model gate to open (local-main chat, up to ${READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS}s; request deadline ${READY_FULL_MAIN_MODEL_REQUEST_SECONDS}s)..."
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

# 단일 inference endpoint를 서빙되거나 budget이 소진될 때까지 best-effort로 데운다.
# 갓 (재)기동된 vLLM은 /ready가 200이어도 첫 inference 요청을 일시적으로 503으로
# 반환할 수 있는데, warming으로 smoke gate 이전에 그 race를 흡수한다.
# 이것은 gate가 아니다 — 호출자는 실패를 무시하므로 warmup이 배포를 abort시키는 일은 없다.
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
      echo "[ready-full] ${name}: warm (attempt ${attempt})"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "[ready-full] ${name}: not warm after ${READY_FULL_INFERENCE_WARMUP_SECONDS}s (best-effort; smoke is the gate)" >&2
      return 1
    fi
    echo "[ready-full] ${name}: not yet warm (attempt ${attempt}), retrying in 10s..." >&2
    sleep 10
  done
}

skip_runtime() {
  local runtime="$1"
  case ",${SMOKE_SKIP_RUNTIMES}," in
    *",${runtime},"*) return 0 ;;
    *) return 1 ;;
  esac
}

# 나머지 inference 경로들을 best-effort로 데워서, 엄격한 smoke gate가 갓 (재)기동된
# vLLM의 첫 요청 503과 race하지 않도록 한다. 절대 fatal하지 않다: chat gate는 위의
# wait_for_main_model_ready가 처리하며, 실제 gate는 smoke이다.
#
# structured-output 웜업은 DockerMainModelBackend.validate()가 이미 admin-sidecar
# 제어 API 경로(전환/시작)에서 수행하는 것과 동일한 목적이다 — xgrammar 제약 디코딩
# Triton 커널(apply_token_bitmask_inplace_kernel)이 response_format:json_schema
# 요청에서 최초 1회 JIT 컴파일되므로, compose가 main-llm-vllm만 recreate하고
# admin-sidecar는 안 건드리는 배포 경로(예: main_model_profiles.yaml 변경으로 인한
# rolling->full 자동 승격)에서는 admin-sidecar의 validate()가 재실행되지 않아 이
# 웜업을 놓친다. 여기서 한 번 더 쏴서 그 경로를 커버한다(ADR-0018 Update 참고).
warm_inference_paths_best_effort() {
  echo "[ready-full] warming inference paths (best-effort, up to ${READY_FULL_INFERENCE_WARMUP_SECONDS}s each)..."
  warm_one_endpoint "main-llm structured output (json_schema)" "$GATEWAY_BASE_URL/v1/chat/completions" \
    '{"model":"local-main","messages":[{"role":"user","content":"Reply with {\"ok\": true}."}],"max_tokens":16,"temperature":0,"response_format":{"type":"json_schema","json_schema":{"name":"warmup","schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false},"strict":true}}}' \
    "$API_KEY" || true
  if ! skip_runtime risk_prompt; then
    warm_one_endpoint "risk" "$GATEWAY_BASE_URL/v1/risk/assessments" \
      '{"prompt":"warmup"}' "$API_KEY" || true
  fi
  if ! skip_runtime embedding; then
    warm_one_endpoint "embedding (local-embed)" "$GATEWAY_BASE_URL/v1/embeddings" \
      '{"model":"local-embed","input":["warmup"]}' "$API_KEY" || true
  fi
  if ! skip_runtime embedding_ko; then
    warm_one_endpoint "embedding (local-embed-ko)" "$GATEWAY_BASE_URL/v1/embeddings" \
      '{"model":"local-embed-ko","input":["warmup"]}' "$API_KEY" || true
  fi
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
# compose 서비스에는 로컬 run/*.pid 파일이 없으므로, readiness는 HTTP로 폴링한다.
wait_for_gateway_ready "$GATEWAY_BASE_URL/ready" "$ADMIN_API_KEY"

# 엄격한 smoke gate 전에 readiness dependency 목록을 출력한다.
bash scripts/ops/status_services.sh --full || true

# control-plane 재배포 후 admin-sidecar가 main-model gate를 닫았다가 boot reconcile로
# 다시 연다. gate가 닫혀 있는 동안 local-main chat은 503이고 /ready엔 반영되지 않으므로,
# smoke(엄격 gate) 전에 chat이 실제로 200을 줄 때까지 기다린다.
wait_for_main_model_ready

# /ready 200 이후에도 갓 (재)기동된 vLLM은 첫 inference 요청을 503으로 반환할 수 있다.
# risk·embedding 경로를 best-effort로 데운다(실패해도 abort하지 않음 — smoke가 진짜 gate).
warm_inference_paths_best_effort

bash scripts/ops/smoke_test.sh
