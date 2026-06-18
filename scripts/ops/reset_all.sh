#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
ENV_FILE="${ENV_FILE:-.env}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

env_value() {
  "$PYTHON_BIN" scripts/env/env_get.py --env-file "$ENV_FILE" "$1" --default ""
}

remove_image_and_containers() {
  local image="$1"
  local label="$2"
  if [[ -z "$image" ]]; then
    return 0
  fi
  local lingering
  lingering="$(docker ps -aq --filter "ancestor=${image}" 2>/dev/null || true)"
  if [[ -n "$lingering" ]]; then
    echo "[reset] removing containers referencing ${label}: ${image}"
    echo "$lingering" | xargs docker rm -f
  fi
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "[reset] removing ${label}: ${image}"
    docker rmi "$image"
  else
    echo "[reset] ${label} not found locally: ${image}"
  fi
}

echo "[reset] stopping services"
if ! command -v docker >/dev/null 2>&1; then
  echo "[reset] docker CLI is required because reset stops compose services and removes the platform/risk images." >&2
  echo "[reset] Use 'make clean-all' for local artifacts only." >&2
  exit 2
fi

if ! docker info >/dev/null 2>&1; then
  echo "[reset] cannot access the Docker daemon." >&2
  echo "[reset] Fix Docker permissions first, or run a Docker-only cleanup with sudo." >&2
  echo "[reset] Local artifacts were not removed, so reset did not leave a partial state." >&2
  exit 2
fi

ENV_FILE="$ENV_FILE" COMPOSE_FILE="$COMPOSE_FILE" bash scripts/ops/down_services.sh

# compose file의 image: 항목은 build: 섹션이 없어 --rmi local로 삭제되지 않는다.
# .env의 PLATFORM_IMAGE 태그를 직접 삭제한다.
PLATFORM_IMAGE=""
if [[ -f "$ENV_FILE" ]]; then
  PLATFORM_IMAGE="$(env_value PLATFORM_IMAGE || true)"
fi
if [[ -z "$PLATFORM_IMAGE" && -f VERSION ]]; then
  PLATFORM_IMAGE="ai-model-serving-platform:$(cat VERSION)"
fi

remove_image_and_containers "$PLATFORM_IMAGE" "platform image"

RISK_VLLM_IMAGE=""
RISK_VLLM_BASE_IMAGE=""
VLLM_IMAGE=""
if [[ -f "$ENV_FILE" ]]; then
  RISK_VLLM_IMAGE="$(env_value RISK_VLLM_IMAGE || true)"
  RISK_VLLM_BASE_IMAGE="$(env_value RISK_VLLM_BASE_IMAGE || true)"
  VLLM_IMAGE="$(env_value VLLM_IMAGE || true)"
fi
if [[ -z "$RISK_VLLM_IMAGE" && -f VERSION ]]; then
  RISK_VLLM_IMAGE="ai-model-serving-risk-vllm-kanana:$(cat VERSION)"
fi
if [[ -n "$RISK_VLLM_IMAGE" ]]; then
  if [[ "$RISK_VLLM_IMAGE" == "$RISK_VLLM_BASE_IMAGE" || "$RISK_VLLM_IMAGE" == "$VLLM_IMAGE" ]]; then
    echo "[reset] preserving shared/base risk vLLM image: ${RISK_VLLM_IMAGE}"
  else
    remove_image_and_containers "$RISK_VLLM_IMAGE" "risk vLLM image"
  fi
fi

if [[ "${PURGE_BASE_IMAGES:-0}" == "1" ]]; then
  remove_image_and_containers "$RISK_VLLM_BASE_IMAGE" "risk vLLM base image"
  remove_image_and_containers "$VLLM_IMAGE" "main vLLM base image"
else
  echo "[reset] preserving upstream/base vLLM images (set PURGE_BASE_IMAGES=1 to delete them)"
fi

echo "[reset] pruning dangling images"
docker image prune -f >/dev/null

echo "[reset] cleaning artifacts (PURGE_MODEL_CACHE=${PURGE_MODEL_CACHE:-0}, PURGE_RUNTIME_SECRETS=${PURGE_RUNTIME_SECRETS:-0})"
FORCE_CLEAN_RUNNING=1 \
  PURGE_MODEL_CACHE="${PURGE_MODEL_CACHE:-0}" \
  PURGE_RUNTIME_SECRETS="${PURGE_RUNTIME_SECRETS:-0}" \
  bash scripts/ops/clean_all.sh --all

if [[ "${PURGE_VENV:-0}" == "1" ]]; then
  echo "[reset] removing .venv"
  rm -rf "$ROOT/.venv"
fi

echo ""
echo "[reset] done."
echo "  run 'make rebuild-full' to rebuild from scratch."
echo ""
echo "  flags used this run:"
echo "    PURGE_MODEL_CACHE=${PURGE_MODEL_CACHE:-0}     (set to 1 to delete model_cache/ and legacy ops/compose/model_cache/)"
echo "    PURGE_RUNTIME_SECRETS=${PURGE_RUNTIME_SECRETS:-0} (set to 1 to delete .runtime/)"
echo "    PURGE_VENV=${PURGE_VENV:-0}          (set to 1 to delete .venv/)"
echo "    PURGE_BASE_IMAGES=${PURGE_BASE_IMAGES:-0}   (set to 1 to delete shared upstream vLLM base images)"
