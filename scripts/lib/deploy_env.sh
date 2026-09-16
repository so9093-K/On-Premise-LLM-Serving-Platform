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

  # Legacy per-runtime image keys are compatibility overrides. A new env may
  # omit them, so Compose receives the shared authority through process-only
  # fallbacks without re-materializing those keys in the persistent env file.
  if [[ -n "${VLLM_IMAGE:-}" && -z "${EMBEDDING_KO_VLLM_IMAGE:-}" ]]; then
    export EMBEDDING_KO_VLLM_IMAGE="$VLLM_IMAGE"
    if ! deploy_env_has_key EMBEDDING_KO_VLLM_IMAGE; then
      COMPOSE_EXPORTED_KEYS+=("EMBEDDING_KO_VLLM_IMAGE")
    fi
  fi
  if [[ -n "${VLLM_IMAGE:-}" && -z "${RISK_VLLM_IMAGE:-}" ]]; then
    export RISK_VLLM_IMAGE="$VLLM_IMAGE"
    if ! deploy_env_has_key RISK_VLLM_IMAGE; then
      COMPOSE_EXPORTED_KEYS+=("RISK_VLLM_IMAGE")
    fi
  fi
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
# VLLM_IMAGE가 shared persistent authority이고, 기존 embedding-ko/risk key는 파일에
# 존재할 때만 compatibility override로 해석한다. 명시적인 shared promotion input은
# shared runtime 전체를 같은 artifact로 승격하되, legacy key가 없던 env에는 그 key를
# 새로 만들지 않는다. RISK_VLLM_IMAGE_TO_DEPLOY는 기존 외부 호출자를 위한
# shared-promotion compatibility alias이고 canonical 입력은 VLLM_UNIFIED_IMAGE_TO_DEPLOY다.
deploy_resolve_runtime_image_plan() {
  local current_vllm current_embedding_ko current_risk current_audio shared_promotion
  current_vllm="$(deploy_env_value VLLM_IMAGE)"
  current_embedding_ko="$(deploy_env_value EMBEDDING_KO_VLLM_IMAGE)"
  current_risk="$(deploy_env_value RISK_VLLM_IMAGE)"
  current_audio="$(deploy_env_value AUDIO_VLLM_IMAGE)"
  shared_promotion="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_TO_DEPLOY:-}}"

  EMBEDDING_KO_VLLM_IMAGE_PERSISTED=0
  RISK_VLLM_IMAGE_PERSISTED=0
  deploy_env_has_key EMBEDDING_KO_VLLM_IMAGE && EMBEDDING_KO_VLLM_IMAGE_PERSISTED=1
  deploy_env_has_key RISK_VLLM_IMAGE && RISK_VLLM_IMAGE_PERSISTED=1

  current_embedding_ko="${current_embedding_ko:-${current_vllm}}"
  current_risk="${current_risk:-${current_vllm}}"

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
  if [[ -n "${EMBEDDING_KO_VLLM_IMAGE_PROMOTION:-}" && "${EMBEDDING_KO_VLLM_IMAGE_PERSISTED:-0}" == "1" ]]; then
    deploy_set_env_value EMBEDDING_KO_VLLM_IMAGE "${EMBEDDING_KO_VLLM_IMAGE_PROMOTION}"
  fi
  if [[ -n "${RISK_VLLM_IMAGE_PROMOTION:-}" && "${RISK_VLLM_IMAGE_PERSISTED:-0}" == "1" ]]; then
    deploy_set_env_value RISK_VLLM_IMAGE "${RISK_VLLM_IMAGE_PROMOTION}"
  fi
  if [[ -n "${AUDIO_VLLM_IMAGE_PROMOTION:-}" ]]; then
    deploy_set_env_value AUDIO_VLLM_IMAGE "${AUDIO_VLLM_IMAGE_PROMOTION}"
  fi
}
