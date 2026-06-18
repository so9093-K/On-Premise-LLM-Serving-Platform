#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"

mkdir -p run logs

if [[ -f compose.yaml || -f compose.yml || -f docker-compose.yml ]]; then
  echo "[up] compose file found; starting compose stack"
  docker compose up -d
  exit 0
fi

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
"$PYTHON_BIN" scripts/build/check_python.py --context start >/dev/null
GATEWAY_HOST="${GATEWAY_HOST:-127.0.0.1}"
RISK_ADAPTER_HOST="${RISK_ADAPTER_HOST:-127.0.0.1}"
GATEWAY_PORT="${GATEWAY_PORT:-9400}"
RISK_ADAPTER_PORT="${RISK_ADAPTER_PORT:-9405}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-90}"

start_service() {
  local name="$1"
  local app="$2"
  local host="$3"
  local port="$4"
  local pid_file="run/${name}.pid"
  local log_file="logs/${name}.log"

  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    echo "[up] ${name} already running with pid $(cat "$pid_file")"
    return
  fi

  echo "[up] starting ${name} on ${host}:${port}"
  PYTHONPATH=src nohup "$PYTHON_BIN" -m uvicorn "$app" --host "$host" --port "$port" >"$log_file" 2>&1 &
  echo $! > "$pid_file"
}

wait_for_health() {
  local name="$1"
  local url="$2"
  local pid_file="run/${name}.pid"
  local log_file="logs/${name}.log"
  local deadline=$((SECONDS + STARTUP_WAIT_SECONDS))

  while (( SECONDS <= deadline )); do
    if [[ -f "$pid_file" ]] && ! kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
      echo "[up] ${name} exited before becoming healthy. Last log lines:" >&2
      tail -n 40 "$log_file" >&2 || true
      return 1
    fi
    if curl -fsS --max-time 2 "${url}/health" >/dev/null 2>&1; then
      echo "[up] ${name} /health ok"
      return 0
    fi
    sleep 1
  done

  echo "[up] ${name} did not report /health within ${STARTUP_WAIT_SECONDS}s. Last log lines:" >&2
  tail -n 40 "$log_file" >&2 || true
  echo "[up] 다음을 확인하세요: 포트 충돌, .env의 GATEWAY_PORT/RISK_ADAPTER_PORT, venv 의존성, PYTHON_BIN, 그리고 logs/${name}.log" >&2
  echo "[up] 기존 프로세스가 남아 있으면 'make stop' 후 다시 'make start'를 실행하세요." >&2
  return 1
}

start_service gateway ai_model_serving.apps.gateway_asgi:app "$GATEWAY_HOST" "$GATEWAY_PORT"
wait_for_health gateway "http://${GATEWAY_HOST}:${GATEWAY_PORT}"

start_service risk_adapter ai_model_serving.apps.risk_adapter_asgi:app "$RISK_ADAPTER_HOST" "$RISK_ADAPTER_PORT"
wait_for_health risk_adapter "http://${RISK_ADAPTER_HOST}:${RISK_ADAPTER_PORT}"

cat <<MSG
[up] Gateway and Risk Adapter processes started and passed /health.
[up] This starts the application layer only. It does not start vLLM model servers.
[up] Use 'make ready-local' for app-only health or 'make ready-full' to verify real upstream vLLM services.
MSG
