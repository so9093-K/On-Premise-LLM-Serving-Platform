#!/usr/bin/env bash
# 자동화 도구나 운영 호스트에서 호출하는 provider-neutral 원격 배포 진입점.
#
# 필수 환경 변수:
#   PLATFORM_IMAGE_TO_DEPLOY   배포할 전체 이미지 참조 (예: registry.../platform:sha)
#   DEPLOY_HOST                대상 서버 IP 또는 hostname
#   DEPLOY_USER                대상 서버 SSH 사용자
#   DEPLOY_PATH                대상 서버 배포 루트 (예: /opt/acl-ai-gateway)
#   REGISTRY_HOST              Container Registry 호스트
#   REGISTRY_USER / REGISTRY_PASSWORD
#                              대상 서버가 image를 pull할 registry 자격 증명
#
# 선택:
#   RISK_VLLM_IMAGE_TO_DEPLOY         RISK_VLLM_IMAGE를 덮어쓰는 전체 런타임 배포 override;
#                                     DEPLOY_MODE=full일 때만 허용
#   VLLM_UNIFIED_IMAGE_TO_DEPLOY      새로 빌드·publish한 immutable digest;
#                                     unified source 변경 full 배포에서는 필수
#   DEPLOY_COMPOSE_FILE               DEPLOY_PATH 기준 상대 compose 파일 경로
#                              기본값: ops/compose/full-stack.private-network.yaml
#   DEPLOY_MODE                기본값 full. 빠른 platform-only 배포에만 rolling을 명시한다.
#                              full 배포는 서비스 단위로 수렴한다: 이미지 ID가 바뀐
#                              서비스(또는 마운트된 런타임 설정이 바뀐 서비스)만
#                              재생성하고, 나머지 vLLM 모델은 전체 재기동 없이 계속
#                              서빙 상태를 유지한다.
#   GATEWAY_HEALTH_URL         배포 후 헬스체크에 쓸 명시적 URL.
#                              기본값은 대상 서버의 .env에서 파생됨:
#                              GATEWAY_BIND_ADDR/GATEWAY_PORT, 0.0.0.0은 localhost로 치환.
#   RUN_READY_SMOKE            1(기본) 또는 0 — 배포 후 gateway /health 체크 실행 여부
#   RUN_READY_FULL_SMOKE       호환성 유지용 변수. full 배포는 반드시 1이어야 하며
#                              /health 이후 항상 make ready-full을 실행한다.
#   DEPLOY_RELEASE_ID          불변 release 디렉터리 이름; 기본값은 현재 Git commit
#   RELEASES_TO_KEEP           보관할 성공한 release 디렉터리 개수 (기본값: 5)
#   DEPLOY_RUNTIME_PROFILE     configs/deploy_profiles.yaml의 런타임 시작 프로필
#                              (예: main_only, retrieval_ready). 생략 시 파일의
#                              default_profile을 사용한다.
#   DEPLOY_DEFERRED_RUNTIMES   배포 후 정지 상태로 유지할, 콤마로 구분된 controllable
#                              런타임 키 또는 compose 서비스 (예:
#                              embedding,embedding_ko,risk_prompt). full 배포는 이
#                              컨테이너들을 시작하지 않고 생성만 한다. 이 값이 설정되면
#                              DEPLOY_RUNTIME_PROFILE보다 우선한다.
set -euo pipefail

: "${PLATFORM_IMAGE_TO_DEPLOY:?Required: full platform image ref}"
: "${DEPLOY_HOST:?Required: deployment server address}"
: "${DEPLOY_USER:?Required: deployment SSH user}"
: "${DEPLOY_PATH:?Required: deployment root}"
: "${REGISTRY_HOST:?Required: container registry host}"
: "${REGISTRY_USER:?Required: registry pull user}"
: "${REGISTRY_PASSWORD:?Required: registry pull password or token}"

COMPOSE_FILE="${DEPLOY_COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
RUN_READY_SMOKE="${RUN_READY_SMOKE:-1}"
RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE:-1}"
RELEASES_TO_KEEP="${RELEASES_TO_KEEP:-5}"
RELEASE_ID="${DEPLOY_RELEASE_ID:-}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

if ! command -v git >/dev/null 2>&1; then
  echo "[deploy] ERROR: git is required to select tracked release inputs." >&2
  exit 2
fi
if [[ "$(git rev-parse --is-inside-work-tree 2>/dev/null || true)" != "true" ]]; then
  echo "[deploy] ERROR: deployment must run from a Git working tree." >&2
  exit 2
fi

if [[ -z "${RELEASE_ID}" ]]; then
  RELEASE_ID="$(git rev-parse HEAD)"
fi

source scripts/lib/deploy_request_policy.sh
deploy_resolve_mode
if [[ -n "${DEPLOY_MODE_REASON:-}" ]]; then
  echo "[deploy] auto mode: ${DEPLOY_MODE} (${DEPLOY_MODE_REASON})"
fi

deploy_validate_request "${RELEASE_ID}" "${RELEASES_TO_KEEP}"
RELEASE_PATH="${DEPLOY_PATH}/releases/${RELEASE_ID}"

echo "[deploy] target: ${SSH_TARGET}:${DEPLOY_PATH}"
echo "[deploy] platform image: ${PLATFORM_IMAGE_TO_DEPLOY}"
echo "[deploy] compose file: ${COMPOSE_FILE}"
echo "[deploy] mode: ${DEPLOY_MODE}"
echo "[deploy] release: ${RELEASE_ID}"

deploy_resolve_full_runtime_images

# ── 1. 불변 release 파일 스테이징 ────────────────────────────────────────
echo "[deploy] preparing release directory ${SSH_TARGET}:${RELEASE_PATH}/"
ssh "${SSH_TARGET}" \
  DEPLOY_PATH="${DEPLOY_PATH}" \
  RELEASE_PATH="${RELEASE_PATH}" \
  bash -s <<'REMOTE_PREPARE'
set -euo pipefail
mkdir -p "${DEPLOY_PATH}/releases"
if [[ -e "${RELEASE_PATH}" ]]; then
  echo "[deploy] ERROR: release directory already exists: ${RELEASE_PATH}" >&2
  exit 1
fi
mkdir "${RELEASE_PATH}"
REMOTE_PREPARE

cleanup_unapplied_release() {
  ssh "${SSH_TARGET}" \
    DEPLOY_PATH="${DEPLOY_PATH}" \
    RELEASE_PATH="${RELEASE_PATH}" \
    bash -s <<'REMOTE_CLEANUP'
set -euo pipefail
case "${RELEASE_PATH}" in
  "${DEPLOY_PATH}/releases/"?*) rm -rf -- "${RELEASE_PATH}" ;;
  *)
    echo "[deploy] ERROR: refusing to clean unexpected release path: ${RELEASE_PATH}" >&2
    exit 2
    ;;
esac
REMOTE_CLEANUP
}

# package와 원격 배포 모두 Git tracked 파일만 입력으로 사용한다. 작업 디렉터리에 남은
# cache/report/임시 파일이 release마다 달라지는 것을 막는다. 자동화 provider 정의는
# 저장소 검증 입력이지 runtime 입력이 아니므로 대상 서버 Release에서는 제외한다.
#
# tests/는 두 배포 경로 모두에서 포함한다. 자동화와 배포 전 make check가 같은 source의
# 테스트를 실행할 수 있어야 하므로 테스트가 빠진 배포본은 검증 입력이 불완전하다.
#
# 예전에는 여기서 제외하고 package_release.sh와 정책을 맞췄는데, 그 배제 근거(크기·
# 공격 표면)를 재보니 셋 다 성립하지 않았다: 압축 후 111KB(전체 +4%), 앱이 import하지
# 않고 .dockerignore가 컨테이너 유입을 막는다. 두 경로의 정책은 여전히 같아야 하고,
# 지금은 "포함"으로 같다.
echo "[deploy] syncing tracked deployable project files to staged release..."
if ! git ls-files -z -- . ':(exclude).github/**' | \
  rsync -az --delete --from0 --files-from=- \
    --exclude ".git/" \
    --exclude "/.other/" \
    --exclude "/.agents/" \
    --exclude "/.codex/" \
    --exclude "/.claude/" \
    --exclude "/.cursor/" \
    --exclude ".env" \
    --exclude ".runtime/" \
    --exclude ".venv/" \
    --exclude ".cache/" \
    --exclude ".pytest_cache/" \
    --exclude "__pycache__/" \
    --exclude "*.pyc" \
    --exclude "model_cache/" \
    --exclude "ops/compose/models/" \
    --exclude "logs/" \
    --exclude "/dist/" \
    --exclude "/build/" \
    --exclude "run/" \
    --exclude "outputs/" \
    ./ \
    "${SSH_TARGET}:${RELEASE_PATH}/"; then
  echo "[deploy] ERROR: release file sync failed; removing unapplied candidate." >&2
  if ! cleanup_unapplied_release; then
    echo "[deploy] ERROR: candidate cleanup failed: ${RELEASE_PATH}" >&2
  fi
  exit 1
fi

# ── 2. 원격: candidate 검증 → 배포 → current를 원자적으로 전환 ──
ssh "${SSH_TARGET}" \
  PLATFORM_IMAGE_TO_DEPLOY="${PLATFORM_IMAGE_TO_DEPLOY}" \
  VLLM_UNIFIED_IMAGE_TO_DEPLOY="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}" \
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-}" \
  AUDIO_VLLM_IMAGE_TO_DEPLOY="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" \
  REGISTRY_HOST="${REGISTRY_HOST}" \
  REGISTRY_USER="${REGISTRY_USER}" \
  REGISTRY_PASSWORD="${REGISTRY_PASSWORD}" \
  DEPLOY_PATH="${DEPLOY_PATH}" \
  RELEASE_PATH="${RELEASE_PATH}" \
  RELEASE_ID="${RELEASE_ID}" \
  RELEASES_TO_KEEP="${RELEASES_TO_KEEP}" \
  COMPOSE_FILE="${COMPOSE_FILE}" \
  DEPLOY_MODE="${DEPLOY_MODE}" \
  GATEWAY_HEALTH_URL="${GATEWAY_HEALTH_URL:-}" \
  RUN_READY_SMOKE="${RUN_READY_SMOKE}" \
  RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE}" \
  DEPLOY_RUNTIME_PROFILE="${DEPLOY_RUNTIME_PROFILE:-}" \
  DEPLOY_DEFERRED_RUNTIMES="${DEPLOY_DEFERRED_RUNTIMES:-}" \
  AUTH_MODE="${AUTH_MODE:-}" \
  PYTHONDONTWRITEBYTECODE=1 \
  bash "${RELEASE_PATH}/scripts/deploy/apply_remote_release.sh"
