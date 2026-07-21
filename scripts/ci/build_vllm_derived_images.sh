#!/usr/bin/env bash
# CI에서 vLLM 기반 런타임 이미지(risk-vllm-kanana, vllm-gemma4-audio)를 한 job에서
# 빌드해, 큰 용량(~25 GB)의 vLLM base를 한 번만 pull하도록 한다.
#
# 필수 환경변수 (GitLab CI job context에서 설정):
#   VLLM_BASE_IMAGE              risk-vllm-kanana가 쓰는 canonical base image
#   RISK_VLLM_IMAGE_SHA          risk-vllm-kanana의 전체 registry ref (SHA 태그)
#   RISK_VLLM_IMAGE_REF          risk-vllm-kanana의 전체 registry ref (branch/ref 태그)
#   AUDIO_VLLM_IMAGE_SHA         vllm-gemma4-audio의 전체 registry ref (SHA 태그)
#   AUDIO_VLLM_IMAGE_REF         vllm-gemma4-audio의 전체 registry ref (branch/ref 태그)
#   CI_REGISTRY_IMAGE            GitLab 프로젝트 container registry prefix
#
# 선택:
#   AUDIO_VLLM_BASE_IMAGE             vllm-gemma4-audio 전용 base image; 미설정 시
#                                      VLLM_BASE_IMAGE를 그대로 쓴다. risk-vllm-kanana(다른
#                                      모델, 별도 검증 필요)를 건드리지 않고 gemma4-audio만
#                                      독립적으로 새 vLLM 버전으로 올릴 수 있도록 분리했다.
#   RISK_VLLM_BASE_IMAGE             VLLM_BASE_IMAGE의 legacy fallback
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

# ── base image 결정 (risk-vllm-kanana와 vllm-gemma4-audio는 독립적으로 버전 관리) ──
RESOLVED_VLLM_BASE_IMAGE="${VLLM_BASE_IMAGE}"
RESOLVED_AUDIO_VLLM_BASE_IMAGE="${AUDIO_VLLM_BASE_IMAGE:-${VLLM_BASE_IMAGE}}"
echo "[build] risk-vllm-kanana base : ${RESOLVED_VLLM_BASE_IMAGE}"
echo "[build] vllm-gemma4-audio base: ${RESOLVED_AUDIO_VLLM_BASE_IMAGE}"
echo "[build] risk-vllm-kanana: ${RISK_VLLM_IMAGE_SHA}"
RISK_VLLM_TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION:-$(
  python3 scripts/models/print_risk_vllm_compatibility.py
)}"

# ── 빌드 전 디스크 상태 ──────────────────────────────────────────────────────
echo "[build] disk status (pre-build):"
docker system df -v || true

# ── base image pull. 두 base가 같으면 daemon layer cache를 공유하고,
#    다르면(예: gemma4-audio만 새 버전으로 올릴 때) 각각 pull한다 ─────────────
echo "[build] pulling base image(s)..."
docker pull "${RESOLVED_VLLM_BASE_IMAGE}"
if [ "${RESOLVED_AUDIO_VLLM_BASE_IMAGE}" != "${RESOLVED_VLLM_BASE_IMAGE}" ]; then
  docker pull "${RESOLVED_AUDIO_VLLM_BASE_IMAGE}"
fi

# ── risk-vllm-kanana 빌드 ─────────────────────────────────────────────────────
echo "[build] building risk-vllm-kanana..."
docker build \
  --cache-from "${RESOLVED_VLLM_BASE_IMAGE}" \
  -f ops/docker/Dockerfile.risk-vllm-kanana \
  --build-arg BASE_IMAGE="${RESOLVED_VLLM_BASE_IMAGE}" \
  --build-arg TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION}" \
  -t "${RISK_VLLM_IMAGE_SHA}" \
  -t "${RISK_VLLM_IMAGE_REF}" \
  .

# ── 빌드 후 디스크 상태 ─────────────────────────────────────────────────────
echo "[build] disk status (post-build):"
docker system df -v || true

# ── 빌드 성공 후 이미지 push ────────────────────────────────────────────
echo "[build] pushing risk-vllm-kanana..."
docker push "${RISK_VLLM_IMAGE_SHA}"
docker push "${RISK_VLLM_IMAGE_REF}"

# ── CI_COMMIT_TAG 기준 태그 push ──────────────────────────────────────────────────
if [ -n "${CI_COMMIT_TAG:-}" ]; then
  RISK_VLLM_TAG="${CI_REGISTRY_IMAGE}/risk-vllm-kanana:${CI_COMMIT_TAG}"
  echo "[build] tagging risk-vllm-kanana as ${RISK_VLLM_TAG}..."
  docker tag "${RISK_VLLM_IMAGE_SHA}" "${RISK_VLLM_TAG}"
  docker push "${RISK_VLLM_TAG}"
fi

# ── vllm-gemma4-audio 빌드 (동일 base + media decode 의존성) ─────────────────────
# 이미 pull된 base layer를 재사용하며, 검증된 audio/container decode 스택과
# Gemma4 멀티모달 패치를 추가한다. 12B 프로필 이미지에 고정(pin)된다.
echo "[build] building vllm-gemma4-audio: ${AUDIO_VLLM_IMAGE_SHA}"
docker build \
  --cache-from "${RESOLVED_AUDIO_VLLM_BASE_IMAGE}" \
  -f ops/images/vllm-gemma4-audio/Dockerfile \
  --build-arg BASE_IMAGE="${RESOLVED_AUDIO_VLLM_BASE_IMAGE}" \
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

# ── 12B 프로필의 image override에 고정할 immutable digest 출력 ──────────
docker pull "${AUDIO_VLLM_IMAGE_SHA}"
mkdir -p build
AUDIO_VLLM_IMAGE_DIGEST="$(docker image inspect "${AUDIO_VLLM_IMAGE_SHA}" --format '{{index .RepoDigests 0}}')"
test -n "${AUDIO_VLLM_IMAGE_DIGEST}"
printf 'AUDIO_VLLM_IMAGE_DIGEST=%s\n' "${AUDIO_VLLM_IMAGE_DIGEST}" > build/audio-image.env
echo "[build] vllm-gemma4-audio digest: ${AUDIO_VLLM_IMAGE_DIGEST}"

echo "[build] done — risk-vllm-kanana and vllm-gemma4-audio pushed successfully"
