#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/vllm_unified_image.sh
source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"
vllm_unified_resolve_images "$ENV_FILE"

VERSION="$(cat VERSION)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
IMAGE="$RISK_VLLM_IMAGE_RESOLVED"
BASE_IMAGE="$RISK_VLLM_BASE_IMAGE_RESOLVED"
TRANSFORMERS_VERSION="$("$PYTHON_BIN" scripts/models/print_vllm_unified_compatibility.py --key transformers)"
HUGGINGFACE_HUB_VERSION="$("$PYTHON_BIN" scripts/models/print_vllm_unified_compatibility.py --key huggingface_hub)"

echo "[vllm-unified-image] building ${IMAGE}"
echo "[vllm-unified-image] base=${BASE_IMAGE} transformers=${TRANSFORMERS_VERSION} huggingface_hub=${HUGGINGFACE_HUB_VERSION}"
docker build \
  --file ops/images/vllm-unified/Dockerfile \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "TRANSFORMERS_VERSION=${TRANSFORMERS_VERSION}" \
  --build-arg "HUGGINGFACE_HUB_VERSION=${HUGGINGFACE_HUB_VERSION}" \
  --tag "$IMAGE" \
  .
echo "[vllm-unified-image] built ${IMAGE}"
