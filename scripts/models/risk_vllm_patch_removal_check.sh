#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/risk_vllm_image.sh
risk_vllm_resolve_images .env

IMAGE="$RISK_VLLM_IMAGE_RESOLVED"
PYTHON_IN_IMAGE="${RISK_VLLM_IMAGE_PYTHON:-python3}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[risk-vllm-patch-removal] docker CLI가 필요합니다" >&2
  exit 2
fi

if [[ "$IMAGE" == ai-model-serving-risk-vllm-kanana:* ]] && ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[risk-vllm-patch-removal] local risk vLLM image가 없습니다: $IMAGE" >&2
  echo "[risk-vllm-patch-removal] 먼저 실행: make rebuild-risk-vllm" >&2
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
