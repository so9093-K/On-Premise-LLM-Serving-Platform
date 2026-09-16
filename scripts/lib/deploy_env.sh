#!/usr/bin/env bash
# Remote deployment .env helpers. COMPOSE_ENV_FILE is owned by the caller.

deploy_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$COMPOSE_ENV_FILE"
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
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$COMPOSE_ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$COMPOSE_ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$COMPOSE_ENV_FILE"
  fi
}

# Full deploy가 실제로 사용할 runtime image와 persistent pin 변경 범위를 계산한다.
# target .env의 각 runtime pin은 서로 독립된 현재 상태다. 명시적인 shared promotion
# input이 있을 때만 main/embedding-ko/risk를 함께 승격한다. 오래된
# RISK_VLLM_IMAGE_TO_DEPLOY는 기존 외부 호출자를 위한 shared-promotion compatibility
# alias이고, canonical 입력은 VLLM_UNIFIED_IMAGE_TO_DEPLOY다.
deploy_resolve_runtime_image_plan() {
  local current_vllm current_embedding_ko current_risk current_audio shared_promotion
  current_vllm="$(deploy_env_value VLLM_IMAGE)"
  current_embedding_ko="$(deploy_env_value EMBEDDING_KO_VLLM_IMAGE)"
  current_risk="$(deploy_env_value RISK_VLLM_IMAGE)"
  current_audio="$(deploy_env_value AUDIO_VLLM_IMAGE)"
  shared_promotion="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_TO_DEPLOY:-}}"

  VLLM_IMAGE_PROMOTION="${shared_promotion}"
  EMBEDDING_KO_VLLM_IMAGE_PROMOTION="${shared_promotion}"
  RISK_VLLM_IMAGE_PROMOTION="${shared_promotion}"
  AUDIO_VLLM_IMAGE_PROMOTION="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-${shared_promotion}}"

  VLLM_IMAGE_EFFECTIVE="${VLLM_IMAGE_PROMOTION:-${current_vllm}}"
  EMBEDDING_KO_VLLM_IMAGE_EFFECTIVE="${EMBEDDING_KO_VLLM_IMAGE_PROMOTION:-${current_embedding_ko}}"
  RISK_VLLM_IMAGE_EFFECTIVE="${RISK_VLLM_IMAGE_PROMOTION:-${current_risk}}"
  AUDIO_VLLM_IMAGE_EFFECTIVE="${AUDIO_VLLM_IMAGE_PROMOTION:-${current_audio}}"
}

deploy_apply_runtime_image_promotions() {
  if [[ -n "${VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value VLLM_IMAGE "${VLLM_IMAGE_PROMOTION}"
  fi
  if [[ -n "${EMBEDDING_KO_VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value EMBEDDING_KO_VLLM_IMAGE "${EMBEDDING_KO_VLLM_IMAGE_PROMOTION}"
  fi
  if [[ -n "${RISK_VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value RISK_VLLM_IMAGE "${RISK_VLLM_IMAGE_PROMOTION}"
  fi
  if [[ -n "${AUDIO_VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value AUDIO_VLLM_IMAGE "${AUDIO_VLLM_IMAGE_PROMOTION}"
  fi
}
