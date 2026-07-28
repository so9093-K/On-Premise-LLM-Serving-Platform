#!/usr/bin/env bash
# vLLM unified 이미지 태그를 확정합니다.
#
# 2026-07-24부터 VLLM_IMAGE/EMBEDDING_KO_VLLM_IMAGE/RISK_VLLM_IMAGE는 전부 같은
# 이미지(ops/images/vllm-unified)를 가리키는 게 정상이다 -- Gemma4 멀티모달
# 패치와 Kanana Llama head_dim 패치가 파일이 안 겹쳐 한 이미지에 같이 들어있고,
# 각 patch는 그걸 필요로 하지 않는 모델에는 no-op이기 때문이다. (예전에는 risk
# 이미지가 실수로 공용/base 이미지와 같아지는 걸 막는 마이그레이션 가드가 여기
# 있었는데, 지금은 정확히 그 상태가 의도된 정상 상태라 가드를 제거했다.)

vllm_unified_default_image() {
  local version
  version="$(cat VERSION 2>/dev/null || echo 0.0.0)"
  printf 'ai-model-serving-vllm-unified:%s\n' "$version"
}

vllm_unified_env_file_value() {
  local env_file="${1:-.env}"
  local key="${2:?env key required}"
  [[ -f "$env_file" ]] || return 1
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*($|#)/ {
      k=$1
      gsub(/[[:space:]]/, "", k)
      if (k == key) {
        sub(/^[^=]*=/, "")
        print
        found=1
        exit
      }
    }
    END { if (!found) exit 1 }
  ' "$env_file"
}

vllm_unified_resolve_images() {
  local env_file="${1:-.env}"
  local default_image
  default_image="$(vllm_unified_default_image)"

  local file_risk file_base file_main
  file_risk="$(vllm_unified_env_file_value "$env_file" RISK_VLLM_IMAGE 2>/dev/null || true)"
  file_base="$(vllm_unified_env_file_value "$env_file" RISK_VLLM_BASE_IMAGE 2>/dev/null || true)"
  file_main="$(vllm_unified_env_file_value "$env_file" VLLM_IMAGE 2>/dev/null || true)"

  VLLM_IMAGE_RESOLVED="${VLLM_IMAGE:-${file_main:-$default_image}}"
  RISK_VLLM_BASE_IMAGE_RESOLVED="${RISK_VLLM_BASE_IMAGE:-${file_base:-vllm/vllm-openai@sha256:6a090ed9d4a3739813ce355cbd63d4c34c987a25c8409796f24912ba71c2d4a4}}"
  RISK_VLLM_IMAGE_RESOLVED="${RISK_VLLM_IMAGE:-${file_risk:-$default_image}}"

  export VLLM_IMAGE_RESOLVED
  export RISK_VLLM_BASE_IMAGE_RESOLVED
  export RISK_VLLM_IMAGE_RESOLVED
}
