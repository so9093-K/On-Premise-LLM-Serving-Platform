#!/usr/bin/env bash
# CI에서 vLLM 런타임 이미지를 빌드한다.
#
# 2026-07-24부터 모든 served model(26B/12B main-LLM, embedding, embedding-ko,
# risk-prompt)이 하나의 Dockerfile(ops/images/vllm-unified)에서 빌드된 같은
# 이미지를 쓴다 -- Gemma4 멀티모달 패치와 Kanana Llama head_dim 패치가 파일이
# 안 겹쳐 한 이미지에 같이 들어있고, 각 patch는 그걸 필요로 하지 않는 모델에는
# no-op이기 때문이다(둘 다 실제로 검증됨).
#
# 2026-07-25부터 registry 이름도 하나(vllm-unified)로 합쳤다 -- RISK_VLLM_IMAGE_SHA/REF와
# AUDIO_VLLM_IMAGE_SHA/REF는 (deploy_gitlab_compose.sh가 risk-prompt-vllm/12B 프로필을
# 서로 다른 방식으로 pin하므로) 변수 자체는 남겨뒀지만, .gitlab-ci.yml에서 이미 같은
# 문자열로 정의돼 있다. 예전엔 같은 빌드 결과를 risk-vllm-kanana/vllm-gemma4-audio
# 두 registry 이름으로 각각 push해서 ~25GB짜리 push/저장공간을 두 배로 썼다.
#
# 필수 환경변수 (GitLab CI job context에서 설정):
#   VLLM_BASE_IMAGE              모든 vLLM 파생 빌드가 공유하는 canonical base image
#   RISK_VLLM_IMAGE_SHA          vllm-unified의 registry ref (SHA 태그)
#   RISK_VLLM_IMAGE_REF          vllm-unified의 registry ref (branch/ref 태그)
#   AUDIO_VLLM_IMAGE_SHA         RISK_VLLM_IMAGE_SHA와 동일한 값이어야 한다(.gitlab-ci.yml에서 보장)
#   AUDIO_VLLM_IMAGE_REF         RISK_VLLM_IMAGE_REF와 동일한 값이어야 한다(.gitlab-ci.yml에서 보장)
#   CI_REGISTRY_IMAGE            GitLab 프로젝트 container registry prefix
#
# 선택:
#   RISK_VLLM_TRANSFORMERS_MIN_VERSION   (기본값: configs/recommended_images.yaml)
#   CI_COMMIT_TAG                    비어있지 않으면 <image>:$CI_COMMIT_TAG도 push
#
# 출력:
#   build/audio-image.env            AUDIO_VLLM_IMAGE_DIGEST=<immutable digest>,
#                                    12B 프로필의 image override에 고정(pin)하는 용도.

set -euo pipefail

# ── Preflight: 필수 환경변수 확인 ───────────────────────────────────────────────
: "${VLLM_BASE_IMAGE:?VLLM_BASE_IMAGE is required — define in .gitlab-ci.yml variables or CI/CD override}"
: "${RISK_VLLM_IMAGE_SHA:?RISK_VLLM_IMAGE_SHA is required}"
: "${RISK_VLLM_IMAGE_REF:?RISK_VLLM_IMAGE_REF is required}"
: "${AUDIO_VLLM_IMAGE_SHA:?AUDIO_VLLM_IMAGE_SHA is required}"
: "${AUDIO_VLLM_IMAGE_REF:?AUDIO_VLLM_IMAGE_REF is required}"
: "${CI_REGISTRY_IMAGE:?CI_REGISTRY_IMAGE is required (predefined GitLab CI variable)}"

if [[ "${AUDIO_VLLM_IMAGE_SHA}" != "${RISK_VLLM_IMAGE_SHA}" || "${AUDIO_VLLM_IMAGE_REF}" != "${RISK_VLLM_IMAGE_REF}" ]]; then
  echo "[build] ERROR: AUDIO_VLLM_IMAGE_*/RISK_VLLM_IMAGE_* must resolve to the same vllm-unified tag." >&2
  echo "[build]   AUDIO_VLLM_IMAGE_SHA=${AUDIO_VLLM_IMAGE_SHA}" >&2
  echo "[build]   RISK_VLLM_IMAGE_SHA=${RISK_VLLM_IMAGE_SHA}" >&2
  exit 2
fi
IMAGE_SHA="${RISK_VLLM_IMAGE_SHA}"
IMAGE_REF="${RISK_VLLM_IMAGE_REF}"

# ── 공통 base image 결정 ──────────────────────────────────────────────────
RESOLVED_VLLM_BASE_IMAGE="${VLLM_BASE_IMAGE}"
echo "[build] vLLM base image : ${RESOLVED_VLLM_BASE_IMAGE}"
RISK_VLLM_TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION:-$(
  python3 scripts/models/print_risk_vllm_compatibility.py
)}"

# ── 빌드 전 디스크 상태 ──────────────────────────────────────────────────────
echo "[build] disk status (pre-build):"
docker system df -v || true

# ── base image를 한 번만 pull ────────────────────────────────────────────
echo "[build] pulling base image..."
docker pull "${RESOLVED_VLLM_BASE_IMAGE}"

# ── vllm-unified 빌드 (Gemma4 멀티모달 + Kanana Llama head_dim 패치 둘 다) ───
echo "[build] building vllm-unified: ${IMAGE_SHA}"
docker build \
  --cache-from "${RESOLVED_VLLM_BASE_IMAGE}" \
  -f ops/images/vllm-unified/Dockerfile \
  --build-arg BASE_IMAGE="${RESOLVED_VLLM_BASE_IMAGE}" \
  --build-arg TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION}" \
  -t "${IMAGE_SHA}" \
  -t "${IMAGE_REF}" \
  .

echo "[build] disk status (post-build):"
docker system df -v || true

echo "[build] pushing vllm-unified..."
docker push "${IMAGE_SHA}"
docker push "${IMAGE_REF}"

if [ -n "${CI_COMMIT_TAG:-}" ]; then
  IMAGE_TAG="${CI_REGISTRY_IMAGE}/vllm-unified:${CI_COMMIT_TAG}"
  echo "[build] tagging as ${IMAGE_TAG}..."
  docker tag "${IMAGE_SHA}" "${IMAGE_TAG}"
  docker push "${IMAGE_TAG}"
fi

# ── 12B 프로필의 image override에 고정할 immutable digest 출력 ──────────
docker pull "${IMAGE_SHA}"
mkdir -p build
AUDIO_VLLM_IMAGE_DIGEST="$(docker image inspect "${IMAGE_SHA}" --format '{{index .RepoDigests 0}}')"
test -n "${AUDIO_VLLM_IMAGE_DIGEST}"
printf 'AUDIO_VLLM_IMAGE_DIGEST=%s\n' "${AUDIO_VLLM_IMAGE_DIGEST}" > build/audio-image.env
echo "[build] vllm-unified digest: ${AUDIO_VLLM_IMAGE_DIGEST}"

echo "[build] done — vllm-unified pushed successfully"
