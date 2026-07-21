#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/risk_vllm_image.sh
source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"
risk_vllm_resolve_images "$ENV_FILE"

IMAGE="$RISK_VLLM_IMAGE_RESOLVED"
PYTHON_IN_IMAGE="${RISK_VLLM_IMAGE_PYTHON:-python3}"
MODELS=("${@:-}")
if [[ ${#MODELS[@]} -eq 0 || -z "${MODELS[0]:-}" ]]; then
  default_risk_model="$(python3 -c "
import yaml
from pathlib import Path
cfg = yaml.safe_load((Path('$ROOT') / 'configs/model_catalog.yaml').read_text())
print(cfg['models']['risk-prompt']['upstream_model_id'])
" 2>/dev/null || true)"
  if [[ -z "${default_risk_model}" ]]; then
    echo "[risk-vllm-config] ERROR: could not read risk-prompt upstream_model_id from configs/model_catalog.yaml" >&2
    exit 2
  fi
  MODELS=("${default_risk_model}")
fi

LOCAL_FILES_ONLY_ARGS=()
if [[ "${HF_MODEL_CONFIG_LOCAL_FILES_ONLY:-0}" == "1" ]]; then
  LOCAL_FILES_ONLY_ARGS+=("--local-files-only")
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[risk-vllm-config] docker CLI is required" >&2
  exit 2
fi

if [[ "$IMAGE" == ai-model-serving-risk-vllm-kanana:* ]] && ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[risk-vllm-config] missing local risk vLLM image: $IMAGE" >&2
  echo "[risk-vllm-config] build it with: make rebuild-risk-vllm" >&2
  exit 2
fi

if [[ "${SKIP_RISK_VLLM_PATCH_VERIFY:-0}" != "1" ]]; then
  patch_label="$(docker image inspect --format '{{ index .Config.Labels "ai_model_serving.patch.transformers_llama_head_dim_guard" }}' "$IMAGE" 2>/dev/null || true)"
  if [[ "$patch_label" != "true" ]]; then
    echo "[risk-vllm-config] missing Kanana head_dim patch label on $IMAGE" >&2
    echo "[risk-vllm-config] rebuild it with: make rebuild-risk-vllm" >&2
    exit 2
  fi
  echo "[risk-vllm-config] verifying Kanana head_dim patch metadata inside ${IMAGE}"
  docker run --rm \
    --entrypoint "${PYTHON_IN_IMAGE}" \
    "$IMAGE" \
    /usr/local/bin/transformers_llama_head_dim_guard.py \
      --verify \
      --json >/dev/null
fi

for model in "${MODELS[@]}"; do
  echo "[risk-vllm-config] checking ${model} inside ${IMAGE}"
  docker run --rm \
    --entrypoint "${PYTHON_IN_IMAGE}" \
    -e "HF_TOKEN=${HF_TOKEN:-}" \
    -e "HUGGING_FACE_HUB_TOKEN=${HUGGING_FACE_HUB_TOKEN:-${HF_TOKEN:-}}" \
    -v "$ROOT:/workspace:ro" \
    -w /workspace \
    "$IMAGE" \
    scripts/models/check_hf_model_config.py \
      --model "$model" \
      --traceback \
      "${LOCAL_FILES_ONLY_ARGS[@]}"
done
