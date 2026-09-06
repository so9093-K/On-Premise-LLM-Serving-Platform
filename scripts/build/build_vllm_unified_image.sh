#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

HOST_PLATFORM="$(uname -s)/$(uname -m)"
if [[ "$HOST_PLATFORM" != "Linux/x86_64" ]]; then
  echo "[vllm-unified-image] This CUDA image build is supported only on Linux x86_64; current host is ${HOST_PLATFORM}." >&2
  echo "[vllm-unified-image] macOS/M5 Metal is a separate runtime qualification track." >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "[vllm-unified-image] Docker CLI is required." >&2
  exit 2
fi
if ! DAEMON_PLATFORM="$(docker info --format '{{.OSType}}/{{.Architecture}}' 2>/dev/null)"; then
  echo "[vllm-unified-image] Cannot access the Docker daemon." >&2
  exit 2
fi
case "$DAEMON_PLATFORM" in
  linux/x86_64|linux/amd64) ;;
  *)
    echo "[vllm-unified-image] Docker daemon must target Linux x86_64; got ${DAEMON_PLATFORM}." >&2
    exit 2
    ;;
esac

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
SOURCE_REVISION="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
SOURCE_STATE="unknown"
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    SOURCE_STATE="dirty"
  else
    SOURCE_STATE="clean"
  fi
fi

echo "[vllm-unified-image] building ${IMAGE}"
echo "[vllm-unified-image] source revision=${SOURCE_REVISION} state=${SOURCE_STATE} target=linux/amd64"
echo "[vllm-unified-image] base=${BASE_IMAGE} transformers=${TRANSFORMERS_VERSION} huggingface_hub=${HUGGINGFACE_HUB_VERSION}"
docker build \
  --platform linux/amd64 \
  --file ops/images/vllm-unified/Dockerfile \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "TRANSFORMERS_VERSION=${TRANSFORMERS_VERSION}" \
  --build-arg "HUGGINGFACE_HUB_VERSION=${HUGGINGFACE_HUB_VERSION}" \
  --label "org.opencontainers.image.revision=${SOURCE_REVISION}" \
  --label "org.opencontainers.image.version=${VERSION}" \
  --label "ai_model_serving.source_state=${SOURCE_STATE}" \
  --label "ai_model_serving.build_platform=linux/amd64" \
  --tag "$IMAGE" \
  .
echo "[vllm-unified-image] built ${IMAGE}"
