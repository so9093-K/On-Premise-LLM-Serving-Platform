#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

IMAGE="${COLBERT_KO_VLLM_IMAGE:-ai-model-serving-colbert-ko-vllm:$(< VERSION)}"
BASE_IMAGE="${COLBERT_KO_VLLM_BASE_IMAGE:-${VLLM_IMAGE:-vllm/vllm-openai:gemma4-0505-cu129}}"

docker build \
  --build-arg "COLBERT_KO_VLLM_BASE_IMAGE=${BASE_IMAGE}" \
  -f ops/docker/Dockerfile.colbert-ko-vllm \
  -t "$IMAGE" \
  .

echo "[build-colbert-ko-vllm-image] built $IMAGE from $BASE_IMAGE"
