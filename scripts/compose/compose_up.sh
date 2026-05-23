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

_env_value() {
  local key="$1"
  "$PYTHON_BIN" scripts/env/env_get.py --env-file "$ENV_FILE" "$key"
}

if [[ ! -f "$PROM_SECRET" || ! -s "$PROM_SECRET" ]]; then
  echo "[compose-up] $PROM_SECRET 파일이 없거나 비어 있습니다. .env는 유지하고 runtime secret만 복구합니다."
  "$PYTHON_BIN" scripts/config/setup_env.py --sync-runtime-secrets --output "$ENV_FILE"
fi

if [[ ! -f "$PROM_SECRET" || ! -s "$PROM_SECRET" ]]; then
  echo "[compose-up] $PROM_SECRET 복구에 실패했습니다. ADMIN_API_KEY 또는 ADMIN_API_KEYS를 확인하세요." >&2
  exit 2
fi

EXPOSURE_MODE_EFFECTIVE="$(_env_value EXPOSURE_MODE)"
EXPOSURE_MODE_EFFECTIVE="${EXPOSURE_MODE_EFFECTIVE:-private_network}"

# Resolve EXPOSURE_MODE to canonical mode via YAML source-of-truth.
# Unknown modes exit with code 2 and a clear message listing canonical modes.
CANONICAL_MODE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE_EFFECTIVE")"

# Determine compose override file from the canonical mode.
COMPOSE_OVERRIDE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE_EFFECTIVE" --print-override-file)"

if [[ -n "$COMPOSE_OVERRIDE" && ! -f "$COMPOSE_OVERRIDE" ]]; then
  echo "[compose-up] compose override file not found: $COMPOSE_OVERRIDE" >&2
  echo "[compose-up] Run 'python scripts/compose/render_exposure_overrides.py' to generate it." >&2
  exit 2
fi

if [[ "${SKIP_PREFLIGHT:-0}" != "1" ]]; then
  ENV_FILE="$ENV_FILE" EXPOSURE_MODE="$CANONICAL_MODE" bash scripts/compose/preflight_compose.sh
else
  APP_ENV_EFFECTIVE="$(_env_value APP_ENV)"
  APP_ENV_EFFECTIVE="${APP_ENV_EFFECTIVE:-local}"
  case "${APP_ENV_EFFECTIVE,,}" in
    local|test|development)
      ;;
    *)
      if [[ "${ALLOW_SKIP_PREFLIGHT:-0}" != "1" && "${ALLOW_SKIP_PREFLIGHT:-}" != "true" ]]; then
        echo "[compose-up] SKIP_PREFLIGHT=1 is forbidden for APP_ENV=$APP_ENV_EFFECTIVE without ALLOW_SKIP_PREFLIGHT=1 and CHANGE_TICKET." >&2
        exit 2
      fi
      if [[ -z "${CHANGE_TICKET:-}" ]]; then
        echo "[compose-up] SKIP_PREFLIGHT=1 for APP_ENV=$APP_ENV_EFFECTIVE requires CHANGE_TICKET." >&2
        exit 2
      fi
      echo "[compose-up] warning: SKIP_PREFLIGHT=1 accepted for APP_ENV=$APP_ENV_EFFECTIVE CHANGE_TICKET=$CHANGE_TICKET" >&2
      ;;
  esac
fi

if [[ -n "$COMPOSE_OVERRIDE" ]]; then
  echo "[compose-up] using $COMPOSE_FILE + $COMPOSE_OVERRIDE (EXPOSURE_MODE=$CANONICAL_MODE) with $ENV_FILE"
  docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_OVERRIDE" --env-file "$ENV_FILE" up -d
else
  echo "[compose-up] using $COMPOSE_FILE with $ENV_FILE"
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
fi
