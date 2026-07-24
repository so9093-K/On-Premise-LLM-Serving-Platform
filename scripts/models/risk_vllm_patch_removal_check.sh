#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/vllm_unified_image.sh
vllm_unified_resolve_images .env

IMAGE="$RISK_VLLM_IMAGE_RESOLVED"
PYTHON_IN_IMAGE="${RISK_VLLM_IMAGE_PYTHON:-python3}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[risk-vllm-patch-removal] docker CLI가 필요합니다" >&2
  exit 2
fi

if [[ "$IMAGE" == ai-model-serving-vllm-unified:* ]] && ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[risk-vllm-patch-removal] local vLLM unified image가 없습니다: $IMAGE" >&2
  echo "[risk-vllm-patch-removal] 먼저 실행: make build-vllm-unified-image" >&2
  exit 2
fi

echo "[risk-vllm-patch-removal] image=$IMAGE"
echo "[risk-vllm-patch-removal] 현재 image 내부 patch 제거 후보 상태를 점검합니다"
docker run --rm \
  --entrypoint "$PYTHON_IN_IMAGE" \
  "$IMAGE" \
  /usr/local/bin/transformers_llama_head_dim_guard.py \
    --removal-check \
    --json

echo "[risk-vllm-patch-removal] 주의: patch가 이미 적용된 image만으로는 제거 가능성을 증명할 수 없습니다."
echo "[risk-vllm-patch-removal] patch 없는 candidate image에서 make risk-vllm-config-check와 실제 vLLM smoke를 통과해야 제거할 수 있습니다."
