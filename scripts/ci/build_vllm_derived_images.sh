#!/usr/bin/env bash
# CI에서 vLLM 런타임 이미지를 빌드한다.
#
# 2026-07-24부터 모든 served model(26B/12B main-LLM, embedding, embedding-ko,
# risk-prompt)이 하나의 Dockerfile(ops/images/vllm-unified)에서 빌드된 같은
# 이미지를 쓴다 -- Gemma4 멀티모달 패치와 Kanana Llama head_dim 패치가 파일이
# 안 겹쳐 한 이미지에 같이 들어있고, 각 patch는 그걸 필요로 하지 않는 모델에는
# no-op이기 때문이다(둘 다 실제로 검증됨). 예전에는 risk-vllm-kanana(risk-prompt
# 전용)와 vllm-gemma4-audio(12B 전용)를 따로 빌드했었는데, 이제 한 번만 빌드해서
# 두 registry 이름으로 태그/push한다 -- deploy_gitlab_compose.sh가 이 두 이름을
# 그대로 기대하므로 그 스크립트는 안 건드린다.
#
# 필수 환경변수 (GitLab CI job context에서 설정):
#   VLLM_BASE_IMAGE              모든 vLLM 파생 빌드가 공유하는 canonical base image
#   RISK_VLLM_IMAGE_SHA          risk-prompt용 registry ref (SHA 태그) -- 통합 이미지와 동일 content
#   RISK_VLLM_IMAGE_REF          risk-prompt용 registry ref (branch/ref 태그)
#   AUDIO_VLLM_IMAGE_SHA         main-LLM(12B)용 registry ref (SHA 태그) -- 통합 이미지와 동일 content
#   AUDIO_VLLM_IMAGE_REF         main-LLM(12B)용 registry ref (branch/ref 태그)
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
# 두 이름(risk-vllm-kanana 계열 태그, vllm-gemma4-audio 계열 태그) 모두 이 한
# 번의 빌드 결과를 가리킨다 -- 실제로는 같은 이미지다.
echo "[build] building vllm-unified: ${AUDIO_VLLM_IMAGE_SHA}"
docker build \
  --cache-from "${RESOLVED_VLLM_BASE_IMAGE}" \
  -f ops/images/vllm-unified/Dockerfile \
  --build-arg BASE_IMAGE="${RESOLVED_VLLM_BASE_IMAGE}" \
  --build-arg TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION}" \
  -t "${AUDIO_VLLM_IMAGE_SHA}" \
  -t "${AUDIO_VLLM_IMAGE_REF}" \
  -t "${RISK_VLLM_IMAGE_SHA}" \
  -t "${RISK_VLLM_IMAGE_REF}" \
  .

echo "[build] disk status (post-build):"
docker system df -v || true

echo "[build] pushing vllm-unified under both registry names..."
docker push "${AUDIO_VLLM_IMAGE_SHA}"
docker push "${AUDIO_VLLM_IMAGE_REF}"
docker push "${RISK_VLLM_IMAGE_SHA}"
docker push "${RISK_VLLM_IMAGE_REF}"

if [ -n "${CI_COMMIT_TAG:-}" ]; then
  AUDIO_VLLM_TAG="${CI_REGISTRY_IMAGE}/vllm-gemma4-audio:${CI_COMMIT_TAG}"
  RISK_VLLM_TAG="${CI_REGISTRY_IMAGE}/risk-vllm-kanana:${CI_COMMIT_TAG}"
  echo "[build] tagging as ${AUDIO_VLLM_TAG} and ${RISK_VLLM_TAG}..."
  docker tag "${AUDIO_VLLM_IMAGE_SHA}" "${AUDIO_VLLM_TAG}"
  docker tag "${AUDIO_VLLM_IMAGE_SHA}" "${RISK_VLLM_TAG}"
  docker push "${AUDIO_VLLM_TAG}"
  docker push "${RISK_VLLM_TAG}"
fi

# ── 12B 프로필의 image override에 고정할 immutable digest 출력 ──────────
docker pull "${AUDIO_VLLM_IMAGE_SHA}"
mkdir -p build
AUDIO_VLLM_IMAGE_DIGEST="$(docker image inspect "${AUDIO_VLLM_IMAGE_SHA}" --format '{{index .RepoDigests 0}}')"
test -n "${AUDIO_VLLM_IMAGE_DIGEST}"
printf 'AUDIO_VLLM_IMAGE_DIGEST=%s\n' "${AUDIO_VLLM_IMAGE_DIGEST}" > build/audio-image.env
echo "[build] vllm-unified digest: ${AUDIO_VLLM_IMAGE_DIGEST}"

echo "[build] done — vllm-unified pushed successfully (as both risk-vllm-kanana and vllm-gemma4-audio tags)"
