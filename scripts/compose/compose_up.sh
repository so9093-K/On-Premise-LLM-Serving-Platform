#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
PROM_SECRET=".runtime/prometheus/admin_api_key"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[compose-up] $ENV_FILE 파일이 없습니다." >&2
  echo "[compose-up] 먼저 'make init-env-compose'를 실행하세요." >&2
  exit 2
fi

if [[ ! -s "$PROM_SECRET" ]]; then
  echo "[compose-up] $PROM_SECRET 파일이 없거나 비어 있습니다. .env는 유지하고 runtime secret만 복구합니다."
  "$PYTHON_BIN" scripts/config/setup_env.py --sync-runtime-secrets --output "$ENV_FILE"
fi

if [[ ! -s "$PROM_SECRET" ]]; then
  echo "[compose-up] $PROM_SECRET 복구에 실패했습니다. ADMIN_API_KEY 또는 ADMIN_API_KEYS를 확인하세요." >&2
  exit 2
fi

if [[ "${SKIP_PREFLIGHT:-0}" != "1" ]]; then
  bash scripts/compose/preflight_compose.sh
fi

echo "[compose-up] using $COMPOSE_FILE with $ENV_FILE"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
