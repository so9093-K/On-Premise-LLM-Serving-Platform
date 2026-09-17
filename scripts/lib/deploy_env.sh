#!/usr/bin/env bash
# Remote deployment .env helpers. COMPOSE_ENV_FILE is owned by the caller.

deploy_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$COMPOSE_ENV_FILE"
}

deploy_env_has_key() {
  local key="$1"
  grep -qE "^${key}=" "$COMPOSE_ENV_FILE"
}

deploy_export_compose_env() {
  local key value
  if ((${#COMPOSE_EXPORTED_KEYS[@]})); then unset "${COMPOSE_EXPORTED_KEYS[@]}"; fi
  COMPOSE_EXPORTED_KEYS=()
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
    COMPOSE_EXPORTED_KEYS+=("$key")
  done < "$COMPOSE_ENV_FILE"
}

deploy_set_env_value() {
  local key="$1" value="$2" temporary
  if deploy_env_has_key "$key"; then
    temporary="$(mktemp "${COMPOSE_ENV_FILE}.tmp.XXXXXX")"
    if ! awk -v key="$key" -v value="$value" '
      index($0, key "=") == 1 { print key "=" value; next }
      { print }
    ' "$COMPOSE_ENV_FILE" > "$temporary"; then
      rm -f "$temporary"
      return 1
    fi
    if ! cat "$temporary" > "$COMPOSE_ENV_FILE"; then
      rm -f "$temporary"
      return 1
    fi
    rm -f "$temporary"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$COMPOSE_ENV_FILE"
  fi
}

# Full deploy가 실제로 사용할 runtime image와 persistent pin 변경 범위를 계산한다.
# VLLM_IMAGE가 main/embedding/embedding-ko/risk-prompt가 공유하는 persistent image
# authority다. deployment-time shared promotion은 VLLM_UNIFIED_IMAGE_TO_DEPLOY 하나가
# 소유하고, AUDIO_VLLM_IMAGE_TO_DEPLOY만 profile-specific override로 분리한다.
deploy_resolve_runtime_image_plan() {
  local current_vllm current_audio
  current_vllm="$(deploy_env_value VLLM_IMAGE)"
  current_audio="$(deploy_env_value AUDIO_VLLM_IMAGE)"

  VLLM_IMAGE_PROMOTION="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}"
  AUDIO_VLLM_IMAGE_PROMOTION="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}}"

  VLLM_IMAGE_EFFECTIVE="${VLLM_IMAGE_PROMOTION:-${current_vllm}}"
  EMBEDDING_KO_VLLM_IMAGE_EFFECTIVE="${VLLM_IMAGE_EFFECTIVE}"
  RISK_VLLM_IMAGE_EFFECTIVE="${VLLM_IMAGE_EFFECTIVE}"
  AUDIO_VLLM_IMAGE_EFFECTIVE="${AUDIO_VLLM_IMAGE_PROMOTION:-${current_audio}}"
}

deploy_apply_runtime_image_promotions() {
  if [[ -n "${VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value VLLM_IMAGE "${VLLM_IMAGE_PROMOTION}"
  fi
  if [[ -n "${AUDIO_VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value AUDIO_VLLM_IMAGE "${AUDIO_VLLM_IMAGE_PROMOTION}"
  fi
}
