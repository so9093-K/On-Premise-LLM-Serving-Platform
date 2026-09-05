#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
ENV_FILE_ABS="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$ENV_FILE")"
STATIC_COMPOSE_PROJECT_NAME="${STATIC_COMPOSE_PROJECT_NAME:-ai-model-serving-static}"
GATEWAY_RUNTIME_ENV_FILE="${GATEWAY_RUNTIME_ENV_FILE:-$ROOT/.runtime/env/linux-nvidia-static-gateway.env}"
GATEWAY_RUNTIME_ENV_FILE="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$GATEWAY_RUNTIME_ENV_FILE")"

if [[ "${1:-}" == "down" && -f "$GATEWAY_RUNTIME_ENV_FILE" ]]; then
  # down은 이미 생성된 project를 정리하는 명령이다. 현재 source env에 static
  # profile이 없어도 중지를 막지 않도록 기존 projection을 그대로 사용한다.
  :
else
  "$PYTHON_BIN" scripts/env/env_validate.py --env-file "$ENV_FILE_ABS"
  "$PYTHON_BIN" scripts/config/render_service_env.py \
    --target linux-nvidia-static \
    --source-env "$ENV_FILE_ABS" \
    --output "$GATEWAY_RUNTIME_ENV_FILE"
fi

# The source env is only Compose interpolation input.  The generated projection is
# the sole env_file received by the Gateway container.
export COMPOSE_PROJECT_NAME="$STATIC_COMPOSE_PROJECT_NAME"
export GATEWAY_RUNTIME_ENV_FILE
exec docker compose \
  --project-name "$STATIC_COMPOSE_PROJECT_NAME" \
  -f ops/compose/static-main.external-runtime.yaml \
  --env-file "$ENV_FILE_ABS" \
  "$@"
