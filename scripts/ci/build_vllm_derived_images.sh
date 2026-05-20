#!/usr/bin/env bash
# Builds the risk-vllm-kanana derived runtime image for CI.
#
# Required environment (set by GitLab CI job context):
#   VLLM_BASE_IMAGE              canonical base image for all vLLM-derived builds
#   RISK_VLLM_IMAGE_SHA          full registry ref (SHA tag) for risk-vllm-kanana
#   RISK_VLLM_IMAGE_REF          full registry ref (branch/ref tag) for risk-vllm-kanana
#   CI_REGISTRY_IMAGE            GitLab project container registry prefix
#
# Optional:
#   RISK_VLLM_BASE_IMAGE             legacy fallback for VLLM_BASE_IMAGE
#   RISK_VLLM_TRANSFORMERS_MIN_VERSION   (default: 4.52.4)
#   CI_COMMIT_TAG                    if non-empty, also pushes <image>:$CI_COMMIT_TAG

set -euo pipefail

# ── Preflight: required env vars ───────────────────────────────────────────────
: "${VLLM_BASE_IMAGE:?VLLM_BASE_IMAGE is required — define in .gitlab-ci.yml variables or CI/CD override}"
: "${RISK_VLLM_IMAGE_SHA:?RISK_VLLM_IMAGE_SHA is required}"
: "${RISK_VLLM_IMAGE_REF:?RISK_VLLM_IMAGE_REF is required}"
: "${CI_REGISTRY_IMAGE:?CI_REGISTRY_IMAGE is required (predefined GitLab CI variable)}"

# ── Resolve common base image ──────────────────────────────────────────────────
RESOLVED_VLLM_BASE_IMAGE="${VLLM_BASE_IMAGE}"
echo "[build] vLLM base image : ${RESOLVED_VLLM_BASE_IMAGE}"
echo "[build] risk-vllm-kanana: ${RISK_VLLM_IMAGE_SHA}"

# ── Pre-build disk status ──────────────────────────────────────────────────────
echo "[build] disk status (pre-build):"
docker system df -v || true

# ── Pull base image once; both builds share the daemon layer cache ─────────────
echo "[build] pulling base image..."
docker pull "${RESOLVED_VLLM_BASE_IMAGE}"

# ── Build risk-vllm-kanana ─────────────────────────────────────────────────────
echo "[build] building risk-vllm-kanana..."
docker build \
  --cache-from "${RESOLVED_VLLM_BASE_IMAGE}" \
  -f ops/docker/Dockerfile.risk-vllm-kanana \
  --build-arg BASE_IMAGE="${RESOLVED_VLLM_BASE_IMAGE}" \
  --build-arg TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION:-4.52.4}" \
  -t "${RISK_VLLM_IMAGE_SHA}" \
  -t "${RISK_VLLM_IMAGE_REF}" \
  .

# ── Post-build disk status ─────────────────────────────────────────────────────
echo "[build] disk status (post-build):"
docker system df -v || true

# ── Push image after build succeeds ────────────────────────────────────────────
echo "[build] pushing risk-vllm-kanana..."
docker push "${RISK_VLLM_IMAGE_SHA}"
docker push "${RISK_VLLM_IMAGE_REF}"

# ── Tag push on CI_COMMIT_TAG ──────────────────────────────────────────────────
if [ -n "${CI_COMMIT_TAG:-}" ]; then
  RISK_VLLM_TAG="${CI_REGISTRY_IMAGE}/risk-vllm-kanana:${CI_COMMIT_TAG}"
  echo "[build] tagging risk-vllm-kanana as ${RISK_VLLM_TAG}..."
  docker tag "${RISK_VLLM_IMAGE_SHA}" "${RISK_VLLM_TAG}"
  docker push "${RISK_VLLM_TAG}"
fi

echo "[build] done — risk-vllm-kanana pushed successfully"
