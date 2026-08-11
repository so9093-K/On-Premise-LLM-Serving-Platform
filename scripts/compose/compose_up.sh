#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
source scripts/lib/compose_context.sh
source scripts/lib/bind_mounted_config.sh
compose_context_init "$ROOT"
compose_context_assert_mutation_safe
PROM_SECRET=".runtime/prometheus/admin_api_key"
MAIN_MODEL_BOOT_OVERRIDE="$(mktemp "${TMPDIR:-/tmp}/main-model-boot.XXXXXX.yaml")"
trap 'rm -f "$MAIN_MODEL_BOOT_OVERRIDE"' EXIT

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[compose-up] $ENV_FILE 파일이 없습니다." >&2
  echo "[compose-up] 먼저 'make init-env-compose'를 실행하세요." >&2
  exit 2
fi

"$PYTHON_BIN" scripts/env/env_validate.py --env-file "$ENV_FILE"

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

EXPOSURE_MODE_EFFECTIVE="${EXPOSURE_MODE:-$(_env_value EXPOSURE_MODE)}"
EXPOSURE_MODE_EFFECTIVE="${EXPOSURE_MODE_EFFECTIVE:-master_open}"

# EXPOSURE_MODE를 YAML source-of-truth 기준 canonical mode로 확정합니다.
# 알 수 없는 mode는 code 2로 종료하며 canonical mode 목록을 안내합니다.
CANONICAL_MODE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE_EFFECTIVE")"

# canonical mode로부터 compose override 파일을 결정합니다.
COMPOSE_OVERRIDE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE_EFFECTIVE" --print-override-file)"

if [[ -n "$COMPOSE_OVERRIDE" && ! -f "$COMPOSE_OVERRIDE" ]]; then
  echo "[compose-up] compose override file not found: $COMPOSE_OVERRIDE" >&2
  echo "[compose-up] Run 'python scripts/compose/render_exposure_overrides.py' to generate it." >&2
  exit 2
fi

echo "[compose-up] resolving persisted main-model boot profile"
MAIN_MODEL_BOOT_PROFILE="$(
  "$PYTHON_BIN" scripts/models/render_main_model_boot_override.py \
    --catalog configs/main_model_profiles.yaml \
    --state .runtime/main-model/main-model-state.json \
    --env-file "$ENV_FILE" \
    --output "$MAIN_MODEL_BOOT_OVERRIDE"
)"
echo "[compose-up] main-model boot profile: $MAIN_MODEL_BOOT_PROFILE"

if [[ "${SKIP_PREFLIGHT:-0}" != "1" ]]; then
  ENV_FILE="$ENV_FILE" COMPOSE_FILE="$COMPOSE_FILE" EXPOSURE_MODE="$CANONICAL_MODE" \
    bash scripts/compose/preflight_compose.sh
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

COMPOSE_ARGS=("${COMPOSE_CONTEXT_FILE_ARGS[@]}")
if [[ -n "$COMPOSE_OVERRIDE" ]]; then
  COMPOSE_ARGS+=(-f "$COMPOSE_OVERRIDE")
fi
COMPOSE_ARGS+=(-f "$MAIN_MODEL_BOOT_OVERRIDE")
echo "[compose-up] validating effective Compose config for profile $MAIN_MODEL_BOOT_PROFILE"
docker compose "${COMPOSE_ARGS[@]}" --env-file "$ENV_FILE_ABS" config >/dev/null

HF_CACHE_HOST="$(
  "$PYTHON_BIN" scripts/models/resolve_hf_cache_dir.py \
    --env-file "$ENV_FILE" \
    --compose-file "$COMPOSE_FILE"
)"
mkdir -p "$HF_CACHE_HOST"
if [[ ! -w "$HF_CACHE_HOST" ]]; then
  echo "[compose-up] Hugging Face cache directory is not writable: $HF_CACHE_HOST" >&2
  exit 2
fi
HF_TOKEN_EFFECTIVE="$(_env_value HF_TOKEN)"
HUGGING_FACE_HUB_TOKEN_EFFECTIVE="$(_env_value HUGGING_FACE_HUB_TOKEN)"
echo "[compose-up] preparing main-model cache for $MAIN_MODEL_BOOT_PROFILE"
HF_TOKEN="$HF_TOKEN_EFFECTIVE" \
HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN_EFFECTIVE:-$HF_TOKEN_EFFECTIVE}" \
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -m ai_model_serving.main_model.cache_cli \
    --catalog configs/main_model_profiles.yaml \
    --cache-dir "$HF_CACHE_HOST" \
    --profile "$MAIN_MODEL_BOOT_PROFILE"

echo "[compose-up] starting stack (EXPOSURE_MODE=$CANONICAL_MODE, profile=$MAIN_MODEL_BOOT_PROFILE)"
# Compose는 bind-mounted 파일의 내용 변경만으로는 기존 컨테이너를 바꾸지 않는다.
# 일반 source는 이미지 재빌드로 수렴하지만, 아래 목록은 각 프로세스가 호스트
# 설정을 직접 읽으므로 이전 적용 fingerprint와 다르면 해당 서비스만 재생성한다.
BIND_CONFIG_STATE_DIR=".runtime/compose-state/bind-mounted-config"
CONFIG_SERVICES_TO_REFRESH=()
CONFIG_SERVICE_STATE_FILES=()
CONFIG_SERVICE_FINGERPRINTS=()
mkdir -p "$BIND_CONFIG_STATE_DIR"
for config_service_spec in "${BIND_MOUNTED_CONFIG_SERVICE_SPECS[@]}"; do
  config_service="${config_service_spec%%:*}"
  config_paths="${config_service_spec#*:}"
  read -r -a config_path_array <<< "${config_paths}"
  config_fingerprint="$(bind_mounted_config_fingerprint "$ROOT" "${config_path_array[@]}")"
  config_state_file="${BIND_CONFIG_STATE_DIR}/${config_service}.sha256"
  applied_fingerprint="$(cat "${config_state_file}" 2>/dev/null || true)"
  existing_container_id="$(docker compose "${COMPOSE_ARGS[@]}" --env-file "$ENV_FILE_ABS" ps -q "$config_service" 2>/dev/null || true)"

  # 처음 생성되는 서비스는 아래 compose up이 최신 설정으로 시작한다. 반면 이미
  # 존재하고 적용 fingerprint가 없거나 다르면, up 뒤 명시적으로 재생성한다.
  if [[ -n "$existing_container_id" && "$applied_fingerprint" != "$config_fingerprint" ]]; then
    CONFIG_SERVICES_TO_REFRESH+=("$config_service")
    echo "[compose-up] ${config_service} bind-mounted config requires refresh"
  fi
  CONFIG_SERVICE_STATE_FILES+=("$config_state_file")
  CONFIG_SERVICE_FINGERPRINTS+=("$config_fingerprint")
done
# 현재 Compose 정의에 없는 이전 collector 같은 orphan을 함께 정리한다. 남겨두면
# 구 수집기와 새 수집기가 같은 json-file을 동시에 Loki로 보내 drift를 만든다.
docker compose "${COMPOSE_ARGS[@]}" --env-file "$ENV_FILE_ABS" up -d --remove-orphans

if [[ ${#CONFIG_SERVICES_TO_REFRESH[@]} -gt 0 ]]; then
  echo "[compose-up] force-recreating changed config services: ${CONFIG_SERVICES_TO_REFRESH[*]}"
  docker compose "${COMPOSE_ARGS[@]}" --env-file "$ENV_FILE_ABS" \
    up -d --no-deps --force-recreate "${CONFIG_SERVICES_TO_REFRESH[@]}"
fi

for config_state_index in "${!CONFIG_SERVICE_STATE_FILES[@]}"; do
  printf '%s\n' "${CONFIG_SERVICE_FINGERPRINTS[${config_state_index}]}" \
    > "${CONFIG_SERVICE_STATE_FILES[${config_state_index}]}"
done
