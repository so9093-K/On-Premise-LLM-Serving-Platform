#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "[vllm-unified-image] Docker CLI is required." >&2
  exit 2
fi
if ! DAEMON_PLATFORM="$(docker info --format '{{.OSType}}/{{.Architecture}}' 2>/dev/null)"; then
  echo "[vllm-unified-image] Cannot access the Docker daemon." >&2
  exit 2
fi

source scripts/lib/vllm_unified_image.sh
VERSION="$(cat VERSION)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3.13 || command -v python3.14 || command -v python3 || command -v python)}"
TARGET_PLATFORM="$("$PYTHON_BIN" scripts/models/print_vllm_unified_compatibility.py --key target_platform)"
case "${TARGET_PLATFORM}:${DAEMON_PLATFORM}" in
  linux/amd64:linux/x86_64|linux/amd64:linux/amd64) ;;
  *)
    echo "[vllm-unified-image] Docker daemon ${DAEMON_PLATFORM} cannot provide the required native target ${TARGET_PLATFORM}." >&2
    echo "[vllm-unified-image] Build this CUDA image on the Ubuntu/Linux amd64 build path; macOS Metal is a separate runtime target." >&2
    exit 2
    ;;
esac

IMAGE="${VLLM_UNIFIED_BUILD_IMAGE:-$(vllm_unified_default_image)}"
EXTRA_TAGS="${VLLM_UNIFIED_BUILD_EXTRA_TAGS:-}"
BASE_IMAGE="${VLLM_BASE_IMAGE:-${RISK_VLLM_BASE_IMAGE:-$(vllm_unified_canonical_base_image)}}"
if [[ "$BASE_IMAGE" != *"@sha256:"* ]]; then
  echo "[vllm-unified-image] Base image must be immutable (name@sha256:...): ${BASE_IMAGE}" >&2
  exit 2
fi
CACHE_FROM="${VLLM_UNIFIED_BUILD_CACHE_FROM:-}"
PULL_BASE_IMAGE="${VLLM_UNIFIED_BUILD_PULL_BASE:-0}"
TRANSFORMERS_VERSION="$("$PYTHON_BIN" scripts/models/print_vllm_unified_compatibility.py --key transformers)"
HUGGINGFACE_HUB_VERSION="$("$PYTHON_BIN" scripts/models/print_vllm_unified_compatibility.py --key huggingface_hub)"
SOURCE_REVISION="${CI_COMMIT_SHA:-unknown}"
SOURCE_STATE="unknown"
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SOURCE_REVISION="$(git rev-parse HEAD)"
  if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    SOURCE_STATE="dirty"
  else
    SOURCE_STATE="clean"
  fi
fi

echo "[vllm-unified-image] building ${IMAGE}"
echo "[vllm-unified-image] source revision=${SOURCE_REVISION} state=${SOURCE_STATE} target=${TARGET_PLATFORM}"
echo "[vllm-unified-image] base=${BASE_IMAGE} transformers=${TRANSFORMERS_VERSION} huggingface_hub=${HUGGINGFACE_HUB_VERSION}"

if [[ "$PULL_BASE_IMAGE" == "1" ]]; then
  echo "[vllm-unified-image] pulling base image"
  docker pull --platform "$TARGET_PLATFORM" "$BASE_IMAGE"
fi

build_args=(
  --platform "$TARGET_PLATFORM"
  --file ops/images/vllm-unified/Dockerfile
  --build-arg "BASE_IMAGE=${BASE_IMAGE}"
  --build-arg "TRANSFORMERS_VERSION=${TRANSFORMERS_VERSION}"
  --build-arg "HUGGINGFACE_HUB_VERSION=${HUGGINGFACE_HUB_VERSION}"
  --label "org.opencontainers.image.revision=${SOURCE_REVISION}"
  --label "org.opencontainers.image.version=${VERSION}"
  --label "ai_model_serving.source_state=${SOURCE_STATE}"
  --label "ai_model_serving.build_platform=${TARGET_PLATFORM}"
)
if [[ -n "$CACHE_FROM" ]]; then
  build_args+=(--cache-from "$CACHE_FROM")
fi
build_args+=(--tag "$IMAGE")
for tag in $EXTRA_TAGS; do
  build_args+=(--tag "$tag")
done

docker build "${build_args[@]}" .
echo "[vllm-unified-image] built ${IMAGE}"
