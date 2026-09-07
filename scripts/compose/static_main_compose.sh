#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET:-linux-nvidia-static}"
ENV_FILE_ABS="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$ENV_FILE")"
STATIC_COMPOSE_PROJECT_NAME="${STATIC_COMPOSE_PROJECT_NAME:-ai-model-serving-static}"
GATEWAY_RUNTIME_ENV_FILE="${GATEWAY_RUNTIME_ENV_FILE:-$ROOT/.runtime/env/${DEPLOYMENT_TARGET}-gateway.env}"
GATEWAY_RUNTIME_ENV_FILE="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$GATEWAY_RUNTIME_ENV_FILE")"

if [[ "${1:-}" == "down" && -f "$GATEWAY_RUNTIME_ENV_FILE" ]]; then
  # down은 이미 생성된 project를 정리하는 명령이다. 현재 source env에 static
  # profile이 없어도 중지를 막지 않도록 기존 projection을 그대로 사용한다.
  :
else
  "$PYTHON_BIN" scripts/env/env_validate.py --env-file "$ENV_FILE_ABS"
  "$PYTHON_BIN" scripts/config/render_service_env.py \
    --target "$DEPLOYMENT_TARGET" \
    --source-env "$ENV_FILE_ABS" \
    --output "$GATEWAY_RUNTIME_ENV_FILE"
fi

# The source env is only Compose interpolation input.  The generated projection is
# the sole env_file received by the Gateway container.
export COMPOSE_PROJECT_NAME="$STATIC_COMPOSE_PROJECT_NAME"
export DEPLOYMENT_TARGET
export GATEWAY_RUNTIME_ENV_FILE
COMPOSE_FILES=(-f ops/compose/static-main.external-runtime.yaml)
if [[ "$DEPLOYMENT_TARGET" == "macos-metal-static" ]]; then
  COMPOSE_FILES+=(-f ops/compose/overrides/static.macos-metal.yaml)

  # 이 override의 Prometheus는 Gateway /metrics를 admin bearer token으로 긁는다
  # (ADMIN_API_KEY_REQUIRED=true일 때 필요). Compose secret은 파일이 없으면
  # 어떤 하위 명령이든 실패하므로 기동 전에 존재를 보장한다.
  PROM_SECRET="$ROOT/.runtime/prometheus/admin_api_key"
  if [[ ! -s "$PROM_SECRET" ]]; then
    echo "[static-compose] $PROM_SECRET 이 없거나 비어 있습니다. .env는 유지하고 runtime secret만 복구합니다."
    "$PYTHON_BIN" scripts/config/setup_env.py --sync-runtime-secrets --output "$ENV_FILE_ABS" || true
  fi
  if [[ ! -s "$PROM_SECRET" ]]; then
    if [[ "${1:-}" == "down" ]]; then
      # 정지 경로는 secret 내용을 쓰지 않는다. 복구 실패가 teardown을 막지 않게 한다.
      mkdir -p "$(dirname "$PROM_SECRET")"
      printf 'unset\n' > "$PROM_SECRET"
      chmod 0644 "$PROM_SECRET"
    else
      echo "[static-compose] $PROM_SECRET 복구에 실패했습니다. $ENV_FILE_ABS 의 ADMIN_API_KEY 또는 ADMIN_API_KEYS를 확인하세요." >&2
      exit 2
    fi
  fi
fi
exec docker compose \
  --project-name "$STATIC_COMPOSE_PROJECT_NAME" \
  "${COMPOSE_FILES[@]}" \
  --env-file "$ENV_FILE_ABS" \
  "$@"
