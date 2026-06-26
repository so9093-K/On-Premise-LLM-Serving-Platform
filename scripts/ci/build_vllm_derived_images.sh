#!/usr/bin/env bash
# Builds the vLLM-derived runtime images for CI (risk-vllm-kanana and
# vllm-gemma4-audio) in one job so the large (~25 GB) vLLM base is pulled once.
#
# Required environment (set by GitLab CI job context):
#   VLLM_BASE_IMAGE              canonical base image for all vLLM-derived builds
#   RISK_VLLM_IMAGE_SHA          full registry ref (SHA tag) for risk-vllm-kanana
#   RISK_VLLM_IMAGE_REF          full registry ref (branch/ref tag) for risk-vllm-kanana
#   AUDIO_VLLM_IMAGE_SHA         full registry ref (SHA tag) for vllm-gemma4-audio
#   AUDIO_VLLM_IMAGE_REF         full registry ref (branch/ref tag) for vllm-gemma4-audio
#   CI_REGISTRY_IMAGE            GitLab project container registry prefix
#
# Optional:
#   RISK_VLLM_BASE_IMAGE             legacy fallback for VLLM_BASE_IMAGE
#   RISK_VLLM_TRANSFORMERS_MIN_VERSION   (default: configs/recommended_images.yaml)
#   CI_COMMIT_TAG                    if non-empty, also pushes <image>:$CI_COMMIT_TAG
#
# Output:
#   build/audio-image.env            AUDIO_VLLM_IMAGE_DIGEST=<immutable digest>,
#                                    to pin into the 12B profile's image override.

set -euo pipefail

# ── Preflight: required env vars ───────────────────────────────────────────────
: "${VLLM_BASE_IMAGE:?VLLM_BASE_IMAGE is required — define in .gitlab-ci.yml variables or CI/CD override}"
: "${RISK_VLLM_IMAGE_SHA:?RISK_VLLM_IMAGE_SHA is required}"
: "${RISK_VLLM_IMAGE_REF:?RISK_VLLM_IMAGE_REF is required}"
: "${AUDIO_VLLM_IMAGE_SHA:?AUDIO_VLLM_IMAGE_SHA is required}"
: "${AUDIO_VLLM_IMAGE_REF:?AUDIO_VLLM_IMAGE_REF is required}"
: "${CI_REGISTRY_IMAGE:?CI_REGISTRY_IMAGE is required (predefined GitLab CI variable)}"

# ── Resolve common base image ──────────────────────────────────────────────────
RESOLVED_VLLM_BASE_IMAGE="${VLLM_BASE_IMAGE}"
echo "[build] vLLM base image : ${RESOLVED_VLLM_BASE_IMAGE}"
echo "[build] risk-vllm-kanana: ${RISK_VLLM_IMAGE_SHA}"
RISK_VLLM_TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION:-$(
  python3 scripts/models/print_risk_vllm_compatibility.py
)}"

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
  --build-arg TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION}" \
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

# ── Build vllm-gemma4-audio (same base + media decode deps) ─────────────────────
# Reuses the already-pulled base layers; adds the audited audio/container decode
# stack plus Gemma4 multimodal patches. Pinned into the 12B profile image.
echo "[build] building vllm-gemma4-audio: ${AUDIO_VLLM_IMAGE_SHA}"
docker build \
  --cache-from "${RESOLVED_VLLM_BASE_IMAGE}" \
  -f ops/images/vllm-gemma4-audio/Dockerfile \
  --build-arg BASE_IMAGE="${RESOLVED_VLLM_BASE_IMAGE}" \
  -t "${AUDIO_VLLM_IMAGE_SHA}" \
  -t "${AUDIO_VLLM_IMAGE_REF}" \
  .

echo "[build] pushing vllm-gemma4-audio..."
docker push "${AUDIO_VLLM_IMAGE_SHA}"
docker push "${AUDIO_VLLM_IMAGE_REF}"

if [ -n "${CI_COMMIT_TAG:-}" ]; then
  AUDIO_VLLM_TAG="${CI_REGISTRY_IMAGE}/vllm-gemma4-audio:${CI_COMMIT_TAG}"
  echo "[build] tagging vllm-gemma4-audio as ${AUDIO_VLLM_TAG}..."
  docker tag "${AUDIO_VLLM_IMAGE_SHA}" "${AUDIO_VLLM_TAG}"
  docker push "${AUDIO_VLLM_TAG}"
fi

# ── Emit immutable digest to pin into the 12B profile's image override ──────────
docker pull "${AUDIO_VLLM_IMAGE_SHA}"
mkdir -p build
AUDIO_VLLM_IMAGE_DIGEST="$(docker image inspect "${AUDIO_VLLM_IMAGE_SHA}" --format '{{index .RepoDigests 0}}')"
test -n "${AUDIO_VLLM_IMAGE_DIGEST}"
printf 'AUDIO_VLLM_IMAGE_DIGEST=%s\n' "${AUDIO_VLLM_IMAGE_DIGEST}" > build/audio-image.env
echo "[build] vllm-gemma4-audio digest: ${AUDIO_VLLM_IMAGE_DIGEST}"

echo "[build] done — risk-vllm-kanana and vllm-gemma4-audio pushed successfully"
