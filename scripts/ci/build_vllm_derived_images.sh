#!/usr/bin/env bash
# Builds both vLLM-derived runtime images (risk-vllm-kanana, colbert-ko-vllm) for CI.
#
# Both images share the same vLLM base layer cache within this job's Docker daemon —
# the large (~25 GB) base image is pulled exactly once.  Sequential builds run first;
# push happens only after both succeed.
#
# Required environment (set by GitLab CI job context):
#   VLLM_BASE_IMAGE              canonical base image for all vLLM-derived builds
#   RISK_VLLM_IMAGE_SHA          full registry ref (SHA tag) for risk-vllm-kanana
#   RISK_VLLM_IMAGE_REF          full registry ref (branch/ref tag) for risk-vllm-kanana
#   COLBERT_KO_VLLM_IMAGE_SHA    full registry ref (SHA tag) for colbert-ko-vllm
#   COLBERT_KO_VLLM_IMAGE_REF    full registry ref (branch/ref tag) for colbert-ko-vllm
#   CI_REGISTRY_IMAGE            GitLab project container registry prefix
#
# Optional:
#   RISK_VLLM_BASE_IMAGE             legacy — conflict-checked against VLLM_BASE_IMAGE
#   COLBERT_KO_VLLM_BASE_IMAGE       legacy — conflict-checked against VLLM_BASE_IMAGE
#   RISK_VLLM_TRANSFORMERS_MIN_VERSION   (default: 4.52.4)
#   CI_COMMIT_TAG                    if non-empty, also pushes <image>:$CI_COMMIT_TAG

set -euo pipefail

# ── Preflight: required env vars ───────────────────────────────────────────────
: "${VLLM_BASE_IMAGE:?VLLM_BASE_IMAGE is required — define in .gitlab-ci.yml variables or CI/CD override}"
: "${RISK_VLLM_IMAGE_SHA:?RISK_VLLM_IMAGE_SHA is required}"
: "${RISK_VLLM_IMAGE_REF:?RISK_VLLM_IMAGE_REF is required}"
: "${COLBERT_KO_VLLM_IMAGE_SHA:?COLBERT_KO_VLLM_IMAGE_SHA is required}"
: "${COLBERT_KO_VLLM_IMAGE_REF:?COLBERT_KO_VLLM_IMAGE_REF is required}"
: "${CI_REGISTRY_IMAGE:?CI_REGISTRY_IMAGE is required (predefined GitLab CI variable)}"

# ── Resolve common base image ──────────────────────────────────────────────────
# VLLM_BASE_IMAGE is canonical.  Legacy RISK_VLLM_BASE_IMAGE / COLBERT_KO_VLLM_BASE_IMAGE
# are checked: if both are set and differ, the build fails fast rather than silently
# using mismatched bases.
RISK_LEGACY="${RISK_VLLM_BASE_IMAGE:-}"
COLBERT_LEGACY="${COLBERT_KO_VLLM_BASE_IMAGE:-}"
if [ -n "${RISK_LEGACY}" ] && [ -n "${COLBERT_LEGACY}" ] && \
   [ "${RISK_LEGACY}" != "${COLBERT_LEGACY}" ]; then
  echo "[build] ERROR: RISK_VLLM_BASE_IMAGE and COLBERT_KO_VLLM_BASE_IMAGE" >&2
  echo "  are both set but point to different images:" >&2
  echo "  RISK_VLLM_BASE_IMAGE=${RISK_LEGACY}" >&2
  echo "  COLBERT_KO_VLLM_BASE_IMAGE=${COLBERT_LEGACY}" >&2
  echo "  These legacy variables are no longer used for base image resolution." >&2
  echo "  Set VLLM_BASE_IMAGE to the desired common base and unset the legacy vars." >&2
  exit 1
fi

RESOLVED_BASE="${VLLM_BASE_IMAGE}"
echo "[build] vLLM base image : ${RESOLVED_BASE}"
echo "[build] risk-vllm-kanana: ${RISK_VLLM_IMAGE_SHA}"
echo "[build] colbert-ko-vllm : ${COLBERT_KO_VLLM_IMAGE_SHA} (required dedicated retrieval runtime)"

# ── Pre-build disk status ──────────────────────────────────────────────────────
echo "[build] disk status (pre-build):"
docker system df -v || true

# ── Pull base image once; both builds share the daemon layer cache ─────────────
echo "[build] pulling base image..."
docker pull "${RESOLVED_BASE}"

# ── Build risk-vllm-kanana ─────────────────────────────────────────────────────
echo "[build] building risk-vllm-kanana..."
docker build \
  --cache-from "${RESOLVED_BASE}" \
  -f ops/docker/Dockerfile.risk-vllm-kanana \
  --build-arg BASE_IMAGE="${RESOLVED_BASE}" \
  --build-arg TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION:-4.52.4}" \
  -t "${RISK_VLLM_IMAGE_SHA}" \
  -t "${RISK_VLLM_IMAGE_REF}" \
  .

# ── Build colbert-ko-vllm (required dedicated retrieval runtime) ───────────────
echo "[build] building colbert-ko-vllm (required dedicated retrieval runtime)..."
docker build \
  --cache-from "${RESOLVED_BASE}" \
  -f ops/docker/Dockerfile.colbert-ko-vllm \
  --build-arg COLBERT_KO_VLLM_BASE_IMAGE="${RESOLVED_BASE}" \
  -t "${COLBERT_KO_VLLM_IMAGE_SHA}" \
  -t "${COLBERT_KO_VLLM_IMAGE_REF}" \
  .

# ── Post-build disk status ─────────────────────────────────────────────────────
echo "[build] disk status (post-build):"
docker system df -v || true

# ── Push both images after both builds succeed ─────────────────────────────────
echo "[build] pushing risk-vllm-kanana..."
docker push "${RISK_VLLM_IMAGE_SHA}"
docker push "${RISK_VLLM_IMAGE_REF}"

echo "[build] pushing colbert-ko-vllm..."
docker push "${COLBERT_KO_VLLM_IMAGE_SHA}"
docker push "${COLBERT_KO_VLLM_IMAGE_REF}"

# ── Tag push on CI_COMMIT_TAG ──────────────────────────────────────────────────
if [ -n "${CI_COMMIT_TAG:-}" ]; then
  RISK_VLLM_TAG="${CI_REGISTRY_IMAGE}/risk-vllm-kanana:${CI_COMMIT_TAG}"
  echo "[build] tagging risk-vllm-kanana as ${RISK_VLLM_TAG}..."
  docker tag "${RISK_VLLM_IMAGE_SHA}" "${RISK_VLLM_TAG}"
  docker push "${RISK_VLLM_TAG}"

  COLBERT_KO_TAG="${CI_REGISTRY_IMAGE}/colbert-ko-vllm:${CI_COMMIT_TAG}"
  echo "[build] tagging colbert-ko-vllm as ${COLBERT_KO_TAG}..."
  docker tag "${COLBERT_KO_VLLM_IMAGE_SHA}" "${COLBERT_KO_TAG}"
  docker push "${COLBERT_KO_TAG}"
fi

echo "[build] done — risk-vllm-kanana and colbert-ko-vllm pushed successfully"
