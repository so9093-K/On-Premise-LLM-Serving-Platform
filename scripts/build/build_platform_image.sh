#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"
VERSION="$(cat VERSION)"
IMAGE="${PLATFORM_IMAGE:-ai-model-serving-platform:${VERSION}}"
EXTRA_TAGS="${PLATFORM_IMAGE_EXTRA_TAGS:-}"
CACHE_FROM="${PLATFORM_IMAGE_CACHE_FROM:-}"
PULL_BASE_IMAGE="${PLATFORM_IMAGE_PULL:-0}"
INLINE_CACHE="${PLATFORM_IMAGE_INLINE_CACHE:-0}"

# 로컬과 CI는 이 스크립트로 같은 Dockerfile build와 image 내부 app 초기화를
# 확인한다. CI만 cache/tag/push/digest 수집을 환경 변수와 후속 단계로 덧붙인다.
build_args=()
if [[ "$PULL_BASE_IMAGE" == "1" ]]; then
  build_args+=(--pull)
fi
if [[ -n "$CACHE_FROM" ]]; then
  echo "[image] loading cache image ${CACHE_FROM}"
  docker pull "$CACHE_FROM" || true
  build_args+=(--cache-from "$CACHE_FROM")
fi
if [[ "$INLINE_CACHE" == "1" ]]; then
  build_args+=(--build-arg BUILDKIT_INLINE_CACHE=1)
fi

build_args+=(-t "$IMAGE")
for tag in $EXTRA_TAGS; do
  build_args+=(-t "$tag")
done

echo "[image] building platform image ${IMAGE}"
docker build "${build_args[@]}" .

echo "[image] verifying platform image imports"
# 이 smoke는 image 안에서 앱 factory를 import/초기화하는지만 확인한다. 배포 .env는
# 컨테이너 실행 시 주입되므로, catalog의 digest 형식 해석에는 고정 fixture를 사용한다.
IMAGE_SMOKE_VLLM="registry.example.com/vllm-unified@sha256:0000000000000000000000000000000000000000000000000000000000000000"
docker run --rm \
  --env "VLLM_IMAGE=${IMAGE_SMOKE_VLLM}" \
  --env "AUDIO_VLLM_IMAGE=${IMAGE_SMOKE_VLLM}" \
  --entrypoint python "$IMAGE" -c \
  "from ai_model_serving.apps.gateway import create_gateway_app; from ai_model_serving.apps.risk_adapter import create_risk_adapter_app; create_gateway_app(); create_risk_adapter_app()"
echo "[image] built and verified ${IMAGE}"
