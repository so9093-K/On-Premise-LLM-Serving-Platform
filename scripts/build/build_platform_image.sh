#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "[image] Docker CLI is required." >&2
  echo "[image] Use 'make package' when only the release ZIP is needed." >&2
  exit 2
fi
if ! DAEMON_PLATFORM="$(docker info --format '{{.OSType}}/{{.Architecture}}' 2>/dev/null)"; then
  echo "[image] Cannot access the Docker daemon. Start Docker and retry." >&2
  exit 2
fi

source scripts/lib/load_env.sh
ENV_FILE="${ENV_FILE:-.env}"
load_local_env "$ENV_FILE"
VERSION="$(cat VERSION)"
IMAGE="${PLATFORM_IMAGE:-ai-model-serving-platform:${VERSION}}"
EXTRA_TAGS="${PLATFORM_IMAGE_EXTRA_TAGS:-}"
CACHE_FROM="${PLATFORM_IMAGE_CACHE_FROM:-}"
PULL_BASE_IMAGE="${PLATFORM_IMAGE_PULL:-0}"
INLINE_CACHE="${PLATFORM_IMAGE_INLINE_CACHE:-0}"
TARGET_PLATFORM="${PLATFORM_BUILD_PLATFORM:-}"

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
EFFECTIVE_PLATFORM="${TARGET_PLATFORM:-$DAEMON_PLATFORM}"

# 로컬과 CI는 이 스크립트로 같은 Dockerfile build와 image 내부 app 초기화를
# 확인한다. 로컬 기본값은 Docker daemon의 architecture이고, GitLab 운영 build는
# PLATFORM_BUILD_PLATFORM=linux/amd64를 명시한 뒤 registry digest를 수집한다.
build_args=()
if [[ -n "$TARGET_PLATFORM" ]]; then
  build_args+=(--platform "$TARGET_PLATFORM")
fi
if [[ "$PULL_BASE_IMAGE" == "1" ]]; then
  build_args+=(--pull)
fi
if [[ -n "$CACHE_FROM" ]]; then
  echo "[image] loading cache image ${CACHE_FROM}"
  pull_args=()
  if [[ -n "$TARGET_PLATFORM" ]]; then
    pull_args+=(--platform "$TARGET_PLATFORM")
  fi
  if docker pull "${pull_args[@]}" "$CACHE_FROM"; then
    build_args+=(--cache-from "$CACHE_FROM")
  else
    # 캐시는 선택적 최적화다. 존재하지 않거나 일시적으로 읽을 수 없는 ref를
    # BuildKit에 다시 넘겨 빌드 자체가 실패하지 않게 한다.
    echo "[image] cache image unavailable; building without remote cache" >&2
  fi
fi
if [[ "$INLINE_CACHE" == "1" ]]; then
  build_args+=(--build-arg BUILDKIT_INLINE_CACHE=1)
fi

build_args+=(
  --label "org.opencontainers.image.revision=${SOURCE_REVISION}"
  --label "org.opencontainers.image.version=${VERSION}"
  --label "ai_model_serving.source_state=${SOURCE_STATE}"
  --label "ai_model_serving.build_platform=${EFFECTIVE_PLATFORM}"
)
build_args+=(-t "$IMAGE")
for tag in $EXTRA_TAGS; do
  build_args+=(-t "$tag")
done

echo "[image] source revision=${SOURCE_REVISION} state=${SOURCE_STATE}"
echo "[image] target platform=${EFFECTIVE_PLATFORM}"
if [[ "$SOURCE_STATE" == "dirty" ]]; then
  echo "[image] WARNING: building from a modified working tree; this is not the clean-commit CI artifact." >&2
fi
echo "[image] building platform image ${IMAGE}"
docker build "${build_args[@]}" .

echo "[image] verifying platform image imports"
# 이 smoke는 image 안에서 앱 factory를 import/초기화하는지만 확인한다. 배포 .env는
# 컨테이너 실행 시 주입되므로, catalog의 digest 형식 해석에는 고정 fixture를 사용한다.
IMAGE_SMOKE_VLLM="registry.example.com/vllm-unified@sha256:0000000000000000000000000000000000000000000000000000000000000000"
run_args=(--rm)
if [[ -n "$TARGET_PLATFORM" ]]; then
  run_args+=(--platform "$TARGET_PLATFORM")
fi
docker run "${run_args[@]}" \
  --env "VLLM_IMAGE=${IMAGE_SMOKE_VLLM}" \
  --env "AUDIO_VLLM_IMAGE=${IMAGE_SMOKE_VLLM}" \
  --entrypoint python "$IMAGE" -c \
  "from ai_model_serving.apps.gateway import create_gateway_app; from ai_model_serving.apps.risk_adapter import create_risk_adapter_app; create_gateway_app(); create_risk_adapter_app()"
IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
echo "[image] built and verified ${IMAGE} (${IMAGE_ID})"
