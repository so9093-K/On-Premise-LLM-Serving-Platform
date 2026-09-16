#!/usr/bin/env bash
# 자동화 도구나 운영 호스트에서 호출하는 provider-neutral 원격 배포 진입점.
#
# 필수 환경 변수:
#   PLATFORM_IMAGE_TO_DEPLOY   배포할 전체 이미지 참조 (예: registry.../platform:sha)
#   DEPLOY_HOST                대상 서버 IP 또는 hostname
#   DEPLOY_USER                대상 서버 SSH 사용자
#   DEPLOY_PATH                대상 서버 배포 루트 (예: /opt/acl-ai-gateway)
#
# Registry 인증은 이 스크립트의 입력이 아니다. 대상 서버는 배포 전에 immutable
# image ref를 pull할 수 있는 credential/helper/workload identity를 준비해야 한다.
# 배포는 실제 docker pull 성공 여부만 검증한다.
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

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
VERSION="$(cat "$ROOT/VERSION")"

: "${PLATFORM_IMAGE_TO_DEPLOY:?Required: full platform image ref}"
: "${DEPLOY_HOST:?Required: deployment server address}"
: "${DEPLOY_USER:?Required: deployment SSH user}"
: "${DEPLOY_PATH:?Required: deployment root}"

COMPOSE_FILE="${DEPLOY_COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
RUN_READY_SMOKE="${RUN_READY_SMOKE:-1}"
RELEASES_TO_KEEP="${RELEASES_TO_KEEP:-5}"
RELEASE_ID="${DEPLOY_RELEASE_ID:-}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
LOCAL_RELEASE=""

cleanup_local_release() {
  if [[ -n "${LOCAL_RELEASE}" && -d "${LOCAL_RELEASE}" ]]; then
    rm -rf "${LOCAL_RELEASE}"
  fi
}
trap cleanup_local_release EXIT

if ! command -v git >/dev/null 2>&1; then
  echo "[deploy] ERROR: git is required to resolve release inputs." >&2
  exit 2
fi
if [[ "$(git rev-parse --is-inside-work-tree 2>/dev/null || true)" != "true" ]]; then
  echo "[deploy] ERROR: deployment must run from a Git working tree." >&2
  exit 2
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[deploy] ERROR: deployment requires a clean tracked working tree." >&2
  echo "[deploy] Commit or restore tracked changes before assigning a release ID." >&2
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

# package_release.sh와 동일한 resolver/materializer로 immutable release tree를 먼저
# 로컬에서 완성한다. 전송 도중에는 Git checkout을 다시 해석하거나 exclude 정책을
# 복제하지 않는다. 따라서 local package와 remote deploy의 payload identity는 같은
# RELEASE_MANIFEST.json으로 증명할 수 있다.
LOCAL_RELEASE="$(mktemp -d "${TMPDIR:-/tmp}/ai-model-serving-deploy-release.XXXXXX")"
if ! RELEASE_PAYLOAD_SHA256="$(
  "$PYTHON_BIN" scripts/release/release_artifact.py materialize \
    --source "$ROOT" \
    --destination "$LOCAL_RELEASE" \
    --version "$VERSION"
)"; then
  echo "[deploy] ERROR: canonical release materialization failed." >&2
  exit 1
fi
echo "[deploy] release payload sha256: ${RELEASE_PAYLOAD_SHA256}"

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

# rsync은 이제 transport일 뿐 release selection policy를 소유하지 않는다. Local
# materialized tree에는 canonical manifest/provenance까지 포함되어 있으며 대상에는
# 그 exact tree만 전송한다.
echo "[deploy] syncing canonical release payload to staged release..."
if ! rsync -az --delete "${LOCAL_RELEASE}/" "${SSH_TARGET}:${RELEASE_PATH}/"; then
  echo "[deploy] ERROR: release file sync failed; removing unapplied candidate." >&2
  if ! cleanup_unapplied_release; then
    echo "[deploy] ERROR: candidate cleanup failed: ${RELEASE_PATH}" >&2
  fi
  exit 1
fi

# 전송 후 대상 host에서 manifest를 다시 검증한다. verify 경로는 stdlib-only라 build
# dependency를 설치하지 않아도 path/mode/size/hash와 compatibility provenance를 확인한다.
if ! ssh "${SSH_TARGET}" \
  RELEASE_PATH="${RELEASE_PATH}" \
  EXPECTED_PAYLOAD_SHA256="${RELEASE_PAYLOAD_SHA256}" \
  bash -s <<'REMOTE_VERIFY'
set -euo pipefail
PYTHON_BIN="$(command -v python3.12 || command -v python3 || command -v python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[deploy] ERROR: Python is required to verify the staged release." >&2
  exit 2
fi
ACTUAL_PAYLOAD_SHA256="$(
  "${PYTHON_BIN}" "${RELEASE_PATH}/scripts/release/release_artifact.py" verify \
    --root "${RELEASE_PATH}"
)"
if [[ "${ACTUAL_PAYLOAD_SHA256}" != "${EXPECTED_PAYLOAD_SHA256}" ]]; then
  echo "[deploy] ERROR: staged release payload identity changed during transfer." >&2
  echo "[deploy]   expected: ${EXPECTED_PAYLOAD_SHA256}" >&2
  echo "[deploy]   actual:   ${ACTUAL_PAYLOAD_SHA256}" >&2
  exit 2
fi
REMOTE_VERIFY
then
  echo "[deploy] ERROR: staged release verification failed; removing candidate." >&2
  if ! cleanup_unapplied_release; then
    echo "[deploy] ERROR: candidate cleanup failed: ${RELEASE_PATH}" >&2
  fi
  exit 1
fi

# ── 2. 원격: 검증된 candidate 배포 → current를 원자적으로 전환 ──────────────
# Staged payload identity 검증까지만 대상 host의 stdlib Python을 사용한다. 이후 release
# helper는 Platform image의 locked runtime dependency를 사용한다. PYTHON_BIN은 Makefile과
# policy helper에 명시적으로 전달하고, PATH 앞에도 같은 runner를 두어 이름으로 Python을
# 찾는 기존 shell helper까지 동일한 실행 환경으로 수렴시킨다.
ssh "${SSH_TARGET}" \
  PLATFORM_IMAGE_TO_DEPLOY="${PLATFORM_IMAGE_TO_DEPLOY}" \
  VLLM_UNIFIED_IMAGE_TO_DEPLOY="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}" \
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-}" \
  AUDIO_VLLM_IMAGE_TO_DEPLOY="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" \
  DEPLOY_PATH="${DEPLOY_PATH}" \
  RELEASE_PATH="${RELEASE_PATH}" \
  RELEASE_ID="${RELEASE_ID}" \
  RELEASES_TO_KEEP="${RELEASES_TO_KEEP}" \
  COMPOSE_FILE="${COMPOSE_FILE}" \
  DEPLOY_MODE="${DEPLOY_MODE}" \
  GATEWAY_HEALTH_URL="${GATEWAY_HEALTH_URL:-}" \
  RUN_READY_SMOKE="${RUN_READY_SMOKE}" \
  DEPLOY_RUNTIME_PROFILE="${DEPLOY_RUNTIME_PROFILE:-}" \
  DEPLOY_DEFERRED_RUNTIMES="${DEPLOY_DEFERRED_RUNTIMES:-}" \
  AUTH_MODE="${AUTH_MODE:-}" \
  TMPDIR="${DEPLOY_PATH}/.runtime" \
  PYTHON_BIN="${RELEASE_PATH}/scripts/deploy/runtime-bin/python3.12" \
  PATH="${RELEASE_PATH}/scripts/deploy/runtime-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  PYTHONDONTWRITEBYTECODE=1 \
  bash "${RELEASE_PATH}/scripts/deploy/apply_remote_release.sh"
