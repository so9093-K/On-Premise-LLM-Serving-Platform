#!/usr/bin/env bash
# CI 배포 스크립트: 111(runner) → 175(GPU runtime)
#
# 필수 환경 변수 (GitLab CI/CD 변수로 설정):
#   PLATFORM_IMAGE_TO_DEPLOY   배포할 전체 이미지 참조 (예: registry.../platform:sha)
#   DEPLOY_HOST                175 서버 IP 또는 hostname
#   DEPLOY_USER                175의 SSH 사용자
#   DEPLOY_PATH                175의 배포 루트 (예: /opt/acl-ai-gateway)
#   CI_REGISTRY                GitLab Container Registry 호스트
#   REGISTRY_DEPLOY_USER / REGISTRY_DEPLOY_PASSWORD
#                              우선 사용되는 read_registry 배포 토큰 자격 증명
#                              (CI_REGISTRY_USER / CI_REGISTRY_PASSWORD로 폴백)
#
# 선택:
#   RISK_VLLM_IMAGE_TO_DEPLOY         RISK_VLLM_IMAGE를 덮어쓰는 전체 런타임 배포 override;
#                                     DEPLOY_MODE=full일 때만 허용
#   VLLM_UNIFIED_IMAGE_SHA            build-vllm-derived 전용 예상 tag; 배포 image 선택에는 사용하지 않음
#   VLLM_UNIFIED_IMAGE_TO_DEPLOY      이번 pipeline에서 새로 만든 immutable digest;
#                                     unified source 변경 full 배포에서는 필수
#   DEPLOY_COMPOSE_FILE               DEPLOY_PATH 기준 상대 compose 파일 경로
#                              기본값: ops/compose/full-stack.private-network.yaml
#   DEPLOY_MODE                기본값 full. 빠른 platform-only 배포에만 Run pipeline에서
#                              rolling으로 명시한다.
#                              full 배포는 서비스 단위로 수렴한다: 이미지 ID가 바뀐
#                              서비스(또는 마운트된 런타임 설정이 바뀐 서비스)만
#                              재생성하고, 나머지 vLLM 모델은 전체 재기동 없이 계속
#                              서빙 상태를 유지한다.
#   GATEWAY_HEALTH_URL         배포 후 헬스체크에 쓸 명시적 URL.
#                              기본값은 175의 .env에서 파생됨:
#                              GATEWAY_BIND_ADDR/GATEWAY_PORT, 0.0.0.0은 localhost로 치환.
#   RUN_READY_SMOKE            1(기본) 또는 0 — 배포 후 gateway /health 체크 실행 여부
#   RUN_READY_FULL_SMOKE       호환성 유지용 변수. full 배포는 반드시 1이어야 하며
#                              /health 이후 항상 make ready-full을 실행한다.
#   DEPLOY_RELEASE_ID          불변 release 디렉터리 이름; 기본값은 CI_COMMIT_SHA
#   RELEASES_TO_KEEP           보관할 성공한 release 디렉터리 개수 (기본값: 5)
#   DEPLOY_RUNTIME_PROFILE     configs/deploy_profiles.yaml의 런타임 시작 프로필
#                              (예: full_hot, main_only, retrieval_ready).
#   DEPLOY_DEFERRED_RUNTIMES   배포 후 정지 상태로 유지할, 콤마로 구분된 controllable
#                              런타임 키 또는 compose 서비스 (예:
#                              embedding,embedding_ko,risk_prompt). full 배포는 이
#                              컨테이너들을 시작하지 않고 생성만 한다. 이 값이 설정되면
#                              DEPLOY_RUNTIME_PROFILE보다 우선한다.
set -euo pipefail

: "${PLATFORM_IMAGE_TO_DEPLOY:?Required: full platform image ref}"
: "${DEPLOY_HOST:?Required: 175 server address}"
: "${DEPLOY_USER:?Required: SSH user on 175}"
: "${DEPLOY_PATH:?Required: deployment root on 175}"
: "${CI_REGISTRY:?Required: GitLab registry host}"

REGISTRY_USER="${REGISTRY_DEPLOY_USER:-${CI_REGISTRY_USER:-}}"
REGISTRY_PASSWORD="${REGISTRY_DEPLOY_PASSWORD:-${CI_REGISTRY_PASSWORD:-}}"
: "${REGISTRY_USER:?Required: REGISTRY_DEPLOY_USER or CI_REGISTRY_USER}"
: "${REGISTRY_PASSWORD:?Required: REGISTRY_DEPLOY_PASSWORD or CI_REGISTRY_PASSWORD}"

COMPOSE_FILE="${DEPLOY_COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
RUN_READY_SMOKE="${RUN_READY_SMOKE:-1}"
RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE:-1}"
RELEASES_TO_KEEP="${RELEASES_TO_KEEP:-5}"
RELEASE_ID="${DEPLOY_RELEASE_ID:-${CI_COMMIT_SHA:-}}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

source scripts/lib/deploy_request_policy.sh
deploy_resolve_mode
if [[ -n "${DEPLOY_MODE_REASON:-}" ]]; then
  echo "[deploy] auto mode: ${DEPLOY_MODE} (${DEPLOY_MODE_REASON})"
fi

if [[ -z "${RELEASE_ID}" ]]; then
  RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
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

echo "[deploy] syncing deployable project files to staged release..."
rsync -az --delete \
  --exclude ".git/" \
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
  "${SSH_TARGET}:${RELEASE_PATH}/"

# ── 2. 원격: candidate 검증 → 배포 → current를 원자적으로 전환 ──
ssh "${SSH_TARGET}" \
  PLATFORM_IMAGE_TO_DEPLOY="${PLATFORM_IMAGE_TO_DEPLOY}" \
  VLLM_UNIFIED_IMAGE_TO_DEPLOY="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}" \
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-}" \
  AUDIO_VLLM_IMAGE_TO_DEPLOY="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" \
  CI_REGISTRY="${CI_REGISTRY}" \
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
  bash -s <<'REMOTE'
set -euo pipefail

# gateway 컨테이너는 이 디렉터리를 bind-mount해서 이미지의 non-root appuser 권한으로
# runtime-state.json을 쓴다. 이걸 (대개 root인) 배포 사용자 권한으로 mkdir하면
# appuser가 쓸 수 없어서 gateway 컨테이너가 PermissionError로 기동에 실패한다.
# 대신 platform 이미지 내부에서 생성하면 그 이미지가 실제로 실행되는 UID로
# 항상 소유권이 잡힌다.
ensure_gateway_runtime_dir() {
  local dir="$1"
  local parent
  parent="$(dirname "${dir}")"
  mkdir -p "${parent}"
  if [[ -d "${dir}" ]]; then
    return 0
  fi
  docker run --rm -v "${parent}:/mnt" --entrypoint sh "${PLATFORM_IMAGE_TO_DEPLOY}" \
    -c "mkdir -p /mnt/$(basename "${dir}")"
}

if [[ ! -d "${RELEASE_PATH}" ]]; then
  echo "[deploy] ERROR: staged release not found: ${RELEASE_PATH}" >&2
  exit 1
fi
source "${RELEASE_PATH}/scripts/lib/bind_mounted_config.sh"
if [[ ! -f "${DEPLOY_PATH}/.env" ]]; then
  echo "[deploy] ERROR: shared .env not found at ${DEPLOY_PATH}/.env" >&2
  echo "[deploy] Run bootstrap on the deployment root before the first CI deployment." >&2
  rm -rf "${RELEASE_PATH}"
  exit 1
fi
mkdir -p "${DEPLOY_PATH}/.runtime" "${DEPLOY_PATH}/ops/compose/model_cache"
ensure_gateway_runtime_dir "${DEPLOY_PATH}/.runtime/gateway"

PREVIOUS_RELEASE=""
if [[ -L "${DEPLOY_PATH}/current" ]]; then
  if ! PREVIOUS_RELEASE="$(readlink -f "${DEPLOY_PATH}/current")" ||
    [[ ! -d "${PREVIOUS_RELEASE}" ]]; then
    echo "[deploy] ERROR: current release link is broken" >&2
    rm -rf "${RELEASE_PATH}"
    exit 1
  fi
fi
RUNTIME_RELEASE=""
if [[ -L "${DEPLOY_PATH}/runtime-current" ]]; then
  if ! RUNTIME_RELEASE="$(readlink -f "${DEPLOY_PATH}/runtime-current")" ||
    [[ ! -d "${RUNTIME_RELEASE}" ]]; then
    echo "[deploy] ERROR: runtime-current release link is broken" >&2
    rm -rf "${RELEASE_PATH}"
    exit 1
  fi
fi
PREVIOUS_CURRENT_LINK=""
PREVIOUS_RUNTIME_LINK=""
if [[ -L "${DEPLOY_PATH}/current" ]]; then
  PREVIOUS_CURRENT_LINK="$(readlink "${DEPLOY_PATH}/current")"
fi
if [[ -L "${DEPLOY_PATH}/runtime-current" ]]; then
  PREVIOUS_RUNTIME_LINK="$(readlink "${DEPLOY_PATH}/runtime-current")"
fi

ln -s "${DEPLOY_PATH}/.env" "${RELEASE_PATH}/.env"
ln -s "${DEPLOY_PATH}/.runtime" "${RELEASE_PATH}/.runtime"
mkdir -p "${RELEASE_PATH}/ops/compose"
ln -s "${DEPLOY_PATH}/ops/compose/model_cache" \
  "${RELEASE_PATH}/ops/compose/model_cache"

cd "${RELEASE_PATH}"
COMPOSE_ENV_FILE="${DEPLOY_PATH}/.env"
source scripts/lib/deploy_recreate_policy.sh
source scripts/lib/deploy_env.sh

if [[ "${DEPLOY_MODE}" == "rolling" ]]; then
  if [[ -z "${PREVIOUS_RELEASE}" || ! -d "${PREVIOUS_RELEASE}" ]]; then
    echo "[deploy] ERROR: first release-directory deployment requires DEPLOY_MODE=full." >&2
    rm -rf "${RELEASE_PATH}"
    exit 2
  fi
  mapfile -t _changed_sensitive < <(deploy_changed_files "${PREVIOUS_RELEASE}" "${RELEASE_PATH}" \
    "ops/compose/full-stack.private-network.yaml" \
    "configs/main_model_profiles.yaml" \
    "configs/gemma4_chat_template.jinja")
  if [[ ${#_changed_sensitive[@]} -gt 0 ]]; then
    echo "[deploy] runtime-sensitive files changed — auto-upgrading to full deploy:"
    for _f in "${_changed_sensitive[@]}"; do echo "[deploy]   ${_f}"; done
    DEPLOY_MODE="full"
    if [[ -z "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
      RISK_VLLM_IMAGE_TO_DEPLOY="$(deploy_env_value RISK_VLLM_IMAGE)"
      echo "[deploy] keeping current RISK_VLLM_IMAGE: ${RISK_VLLM_IMAGE_TO_DEPLOY}"
    fi
  fi
fi
# rolling에서 full로 승격됐을 때도, 명시적 12B override가 없다면 동일한 unified
# image를 사용한다. 설정-only 변경은 current image를 재사용할 수 있다.
if [[ "${DEPLOY_MODE}" == "full" && -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  AUDIO_VLLM_IMAGE_TO_DEPLOY="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_TO_DEPLOY}}"
fi
if [[ "${DEPLOY_MODE}" != "full" &&
  ( -n "${DEPLOY_RUNTIME_PROFILE:-}" || -n "${DEPLOY_DEFERRED_RUNTIMES:-}" ) ]]; then
  echo "[deploy] ERROR: DEPLOY_RUNTIME_PROFILE/DEPLOY_DEFERRED_RUNTIMES require DEPLOY_MODE=full." >&2
  rm -rf "${RELEASE_PATH}"
  exit 2
fi

# source-drift 최종 가드. release commit의 일반 경로는 CI only:changes가 새
# unified image를 자동 빌드하지만, 이전 배포 이후 여러 commit을 건너뛴 수동
# pipeline까지 CI의 직전-commit diff만으로 완전히 판별할 수는 없다. 이 비교는
# 그런 경우 기존 digest의 조용한 재사용을 배포 직전에 막는다.
if [[ -n "${PREVIOUS_RELEASE}" && -d "${PREVIOUS_RELEASE}" ]]; then
  _unified_image_source=(
    ".dockerignore"
    "ops/images/vllm-unified/Dockerfile"
    "ops/images/vllm-unified/requirements.media.lock"
    "ops/patches/apply_gemma4_multimodal_patches.py"
    "ops/patches/transformers_llama_head_dim_guard.py"
    "scripts/ci/build_vllm_derived_images.sh"
    "scripts/models/print_vllm_unified_compatibility.py"
  )
  mapfile -t _changed_unified_image_source < <(deploy_changed_files "${PREVIOUS_RELEASE}" "${RELEASE_PATH}" "${_unified_image_source[@]}")
  if deploy_unified_image_config_changed "${PREVIOUS_RELEASE}" "${RELEASE_PATH}"; then
    _changed_unified_image_source+=("configs/vllm_unified_build.yaml")
  else
    _config_compare_status=$?
    if [[ ${_config_compare_status} -ne 1 ]]; then
      rm -rf "${RELEASE_PATH}"
      exit "${_config_compare_status}"
    fi
  fi
  if [[ ${#_changed_unified_image_source[@]} -gt 0 ]] && ! deploy_has_fresh_unified_image_artifact "${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}"; then
    echo "[deploy] ERROR: vllm-unified image source changed but build-vllm-derived did not run." >&2
    echo "[deploy]   No fresh immutable unified image artifact — deploying now would ship the previous image." >&2
    printf '[deploy]   Changed source: %s\n' "${_changed_unified_image_source[@]}" >&2
    echo "[deploy]   Push the vLLM input change through release so CI builds it automatically," >&2
    echo "[deploy]   or re-trigger with BUILD_VLLM_DERIVED=1 for this historical source drift." >&2
    rm -rf "${RELEASE_PATH}"
    exit 2
  fi
fi

# read-only 배포 토큰으로 registry 로그인
echo "${REGISTRY_PASSWORD}" | \
  docker login "${CI_REGISTRY}" -u "${REGISTRY_USER}" --password-stdin

COMPOSE_EXPORTED_KEYS=()

_PYTHON_BIN="$(command -v python3.12 || command -v python3 || command -v python)"
_exposure_mode=""
COMPOSE_OVERRIDE=""
MAIN_MODEL_BOOT_OVERRIDE=""
DEPLOY_RUNTIME_PROFILE="${DEPLOY_RUNTIME_PROFILE:-}"
DEPLOY_DEFERRED_RUNTIMES="${DEPLOY_DEFERRED_RUNTIMES:-}"
DEFERRED_RUNTIME_KEYS=()
DEFERRED_RUNTIME_SERVICES=()
RESTORING_RELEASE=0
ENV_BACKUP_CREATED=0
cleanup_generated_files() {
  local path
  for path in "${MAIN_MODEL_BOOT_OVERRIDE:-}"; do
    if [[ -n "${path}" ]]; then
      rm -f "${path}"
    fi
  done
  if [[ "${ENV_BACKUP_CREATED:-0}" != "1" &&
    -n "${RELEASE_PATH:-}" && -d "${RELEASE_PATH}" ]]; then
    rm -rf "${RELEASE_PATH}"
  fi
}
trap cleanup_generated_files EXIT

configure_release_context() {
  local release_path="$1"
  cd "${release_path}"
  _exposure_mode="${EXPOSURE_MODE:-private_network}"
  if [[ ! -f "scripts/compose/resolve_exposure_mode.py" ]]; then
    if [[ "${RESTORING_RELEASE:-0}" != "1" ]]; then
      echo "[deploy] ERROR: exposure mode resolver is missing in ${release_path}" >&2
      return 1
    fi
    case "${_exposure_mode}" in
      private_network) COMPOSE_OVERRIDE="" ;;
      master_open) COMPOSE_OVERRIDE="ops/compose/overrides/exposure.master-open.yaml" ;;
      *)
        echo "[deploy] ERROR: previous release cannot resolve EXPOSURE_MODE=${_exposure_mode}" >&2
        return 1
        ;;
    esac
  elif ! COMPOSE_OVERRIDE="$(
    "$_PYTHON_BIN" scripts/compose/resolve_exposure_mode.py \
      "$_exposure_mode" --print-override-file
  )"; then
    echo "[deploy] ERROR: failed to resolve EXPOSURE_MODE=${_exposure_mode}" >&2
    return 1
  fi
  if [[ -n "$COMPOSE_OVERRIDE" && ! -f "$COMPOSE_OVERRIDE" ]]; then
    echo "[deploy] ERROR: exposure overlay not found: ${COMPOSE_OVERRIDE}" >&2
    return 1
  fi

  if [[ "${DEPLOY_MODE}" == "full" && "${RESTORING_RELEASE:-0}" != "1" ]]; then
    if [[ -z "${MAIN_MODEL_BOOT_OVERRIDE}" ]]; then
      MAIN_MODEL_BOOT_OVERRIDE="$(
        mktemp "${TMPDIR:-/tmp}/main-model-boot.XXXXXX.yaml"
      )"
    fi
    _state_file="${DEPLOY_PATH}/.runtime/main-model/main-model-state.json"
    if [[ -f "${_state_file}" && ! -r "${_state_file}" ]]; then
      # ensure_gateway_runtime_dir와 같은 종류의 문제: admin-sidecar 컨테이너는
      # (docker.sock을 다루므로) user: 0:0으로 돌기 때문에 이 파일은 root 소유로
      # 쓰여지고, 배포 사용자가 다시 읽을 수 없다. 실패시키고 수동 chmod를 요구하는
      # 대신, 같은 방식으로 — platform 이미지 내부에서 root 권한으로 — 복구한다.
      echo "[deploy] main-model state file is not readable by the deploy user; repairing ownership..." >&2
      if ! docker run --rm -v "$(dirname "${_state_file}"):/mnt" --entrypoint sh "${PLATFORM_IMAGE_TO_DEPLOY}" \
        -c "chmod o+r /mnt/$(basename "${_state_file}")"; then
        echo "[deploy] ERROR: main-model state file exists but is not readable by the deploy user." >&2
        echo "[deploy]   Automatic repair failed. Fix: sudo chmod o+r ${_state_file}" >&2
        return 1
      fi
    fi
    if ! MAIN_MODEL_BOOT_PROFILE="$(
      "${_PYTHON_BIN}" scripts/models/render_main_model_boot_override.py \
        --catalog configs/main_model_profiles.yaml \
        --state "${_state_file}" \
        --env-file "${COMPOSE_ENV_FILE}" \
        --output "${MAIN_MODEL_BOOT_OVERRIDE}"
    )"; then
      echo "[deploy] ERROR: persisted main-model boot profile is invalid" >&2
      return 1
    fi
  fi

  if [[ -n "$COMPOSE_OVERRIDE" ]]; then
    echo "[deploy] exposure overlay: $COMPOSE_OVERRIDE (EXPOSURE_MODE=$_exposure_mode)"
  else
    echo "[deploy] no exposure overlay (EXPOSURE_MODE=$_exposure_mode)"
  fi
}

compose_run() {
  local compose_args=(-f "${COMPOSE_FILE}")
  if [[ -n "${COMPOSE_OVERRIDE:-}" ]]; then
    compose_args+=(-f "${COMPOSE_OVERRIDE}")
  fi
  if [[ "${RESTORING_RELEASE:-0}" != "1" && -n "${MAIN_MODEL_BOOT_OVERRIDE:-}" ]]; then
    compose_args+=(-f "${MAIN_MODEL_BOOT_OVERRIDE}")
  fi
  COMPOSE_SERVICE_ENV_FILE="${COMPOSE_ENV_FILE}" \
    docker compose \
      --project-name "${COMPOSE_PROJECT_NAME:-compose}" \
      "${compose_args[@]}" \
      --env-file "${COMPOSE_ENV_FILE}" \
      "$@"
}

resolve_deferred_runtimes() {
  DEFERRED_RUNTIME_KEYS=()
  DEFERRED_RUNTIME_SERVICES=()
  [[ -n "${DEPLOY_DEFERRED_RUNTIMES:-}" || -n "${DEPLOY_RUNTIME_PROFILE:-}" ]] || return 0
  local resolved
  if ! resolved="$(
    "${_PYTHON_BIN}" scripts/runtime/deferred_runtimes.py \
      --config-root "${PWD}" \
      --compose-file "${COMPOSE_FILE}" \
      --profile "${DEPLOY_RUNTIME_PROFILE}" \
      --runtimes "${DEPLOY_DEFERRED_RUNTIMES}" \
      --output lines
  )"; then
    return 1
  fi
  mapfile -t _resolved_lines <<<"${resolved}"
  read -r -a DEFERRED_RUNTIME_KEYS <<<"${_resolved_lines[0]:-}"
  read -r -a DEFERRED_RUNTIME_SERVICES <<<"${_resolved_lines[1]:-}"
  if [[ ${#DEFERRED_RUNTIME_KEYS[@]} -gt 0 ]]; then
    echo "[deploy] deferred runtimes: ${DEFERRED_RUNTIME_KEYS[*]} (${DEFERRED_RUNTIME_SERVICES[*]})"
  elif [[ -n "${DEPLOY_RUNTIME_PROFILE:-}" ]]; then
    echo "[deploy] runtime profile ${DEPLOY_RUNTIME_PROFILE}: no deferred runtimes"
  fi
}

is_deferred_service() {
  local service="$1"
  local deferred
  for deferred in "${DEFERRED_RUNTIME_SERVICES[@]}"; do
    [[ "${service}" == "${deferred}" ]] && return 0
  done
  return 1
}

apply_deferred_runtime_state() {
  [[ ${#DEFERRED_RUNTIME_KEYS[@]} -gt 0 ]] || return 0
  local state_path="${DEPLOY_PATH}/.runtime/gateway/runtime-state.json"
  ensure_gateway_runtime_dir "$(dirname "${state_path}")"
  if [[ "${RUNTIME_STATE_MUTATED}" != "1" ]]; then
    RUNTIME_STATE_BACKUP="${DEPLOY_PATH}/.runtime/gateway/runtime-state.json.bak.$(date +%Y%m%d%H%M%S)"
    if [[ -f "${state_path}" ]]; then
      cp "${state_path}" "${RUNTIME_STATE_BACKUP}"
      RUNTIME_STATE_ORIGINAL_EXISTS=1
    else
      RUNTIME_STATE_ORIGINAL_EXISTS=0
    fi
    RUNTIME_STATE_MUTATED=1
  fi
  "${_PYTHON_BIN}" scripts/runtime/deferred_runtimes.py \
    --config-root "${PWD}" \
    --compose-file "${COMPOSE_FILE}" \
    --profile "${DEPLOY_RUNTIME_PROFILE}" \
    --runtimes "${DEPLOY_DEFERRED_RUNTIMES}" \
    --state-path "${state_path}" \
    --apply-state \
    --reason deferred_at_deploy \
    --source deploy \
    --output lines >/dev/null
  echo "[deploy] gateway desired runtime state updated: ${state_path}"
}

# 이미지가 바뀌었거나(또는 실행 중이 아닌) Compose 서비스를 출력한다.
#
# Compose의 config-hash 대신 이미지 identity를 쓰는 이유: 모든 서비스가
# `env_file: ../../.env`로 공유 .env를 로드하는데, 이 파일은 매 배포마다 바뀐다
# (PLATFORM_IMAGE digest, DEPLOY_RELEASE_ID, ...). Compose는 이 resolve된 환경
# 전체를 각 서비스의 config-hash에 포함시키므로, config-hash는 매 release마다
# 전체 fleet을 다시 해싱하게 되어 재생성 범위를 좁힐 수 없다. resolve된 이미지 ID야말로
# "이 서비스의 payload가 실제로 바뀌었는지"를 정확히 반영하는 신호다: .env 변동을
# 무시하고, ref가 아니라 ID를 비교하기 때문에 `compose pull` 이후 태그가 옮겨진
# 경우(예: risk-vllm-kanana:release가 새 digest로 재빌드된 경우)도 잡아낸다.
# 이미지 ID가 안 바뀐 서비스는 전체 fleet을 cold-restart하고 여러 분 걸리는
# 순차 healthcheck 체인과 경합하는 대신 계속 서빙 상태를 유지한다.
#
# 설정 내용 변경(chat template, model profile)은 이미지를 바꾸지 않는다;
# compute_recreate_set()이 실제로 이 파일들을 마운트하는 서비스를 추가로 잡아낸다.
list_services_needing_recreate() {
  local svc cref cid runid candid
  while read -r svc cref; do
    [[ -z "${svc}" ]] && continue
    cid="$(compose_run ps -q "${svc}" 2>/dev/null || true)"
    if [[ -z "${cid}" ]]; then
      echo "${svc}"
      continue
    fi
    runid="$(docker inspect -f '{{ .Image }}' "${cid}" 2>/dev/null || true)"
    candid="$(docker image inspect -f '{{ .Id }}' "${cref}" 2>/dev/null || true)"
    if [[ -z "${candid}" || "${runid}" != "${candid}" ]]; then
      echo "${svc}"
    fi
  done < <(
    compose_run config --format json |
      "${_PYTHON_BIN}" -c \
        'import json,sys; d=json.load(sys.stdin); [print(n, s.get("image","")) for n,s in d["services"].items()]'
  )
}

# Release directory는 매번 바뀌지만 Compose context는 current로 고정한다. 과거
# release 경로를 label로 가진 컨테이너는 새 current symlink를 따라가지 않으므로,
# 다음 full deploy에서 한 번 재생성해 안정 경로로 수렴시킨다.
list_services_with_stale_compose_context() {
  local expected_context="${DEPLOY_PATH}/current/ops/compose"
  local service container_id actual_context
  while read -r service; do
    [[ -n "${service}" ]] || continue
    container_id="$(compose_run ps -q "${service}" 2>/dev/null || true)"
    [[ -n "${container_id}" ]] || continue
    actual_context="$(
      docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
        "${container_id}" 2>/dev/null || true
    )"
    if [[ "${actual_context}" != "${expected_context}" ]]; then
      echo "[deploy] ${service} uses stale Compose context: ${actual_context:-unknown}" >&2
      echo "${service}"
    fi
  done < <(compose_run config --services)
}

# 실행 중인 스택이 현재 작업 디렉터리에 설정된 compose 컨텍스트로 수렴하도록,
# (재)생성해야 할 서비스 집합을 중복 없이 출력한다. 다음을 합친다:
#   - 이미지 ID 변경 / 실행 중이 아닌 서비스 (list_services_needing_recreate)
#   - ${1} baseline release 트리 대비 설정 내용 변경분을, 실제로 그 파일을
#     쓰는 서비스로 매핑:
#       * configs/main_model_profiles.yaml, configs/gemma4_chat_template.jinja
#         -> main-llm-vllm (configs/와 template을 마운트하는 유일한 모델)
#       * compose 파일 자체 -> 모든 서비스 (구조적 변경은 어떤 서비스 정의든
#         바꿀 수 있고, 이미지 ID만으로는 감지할 수 없다)
# baseline을 빈 값으로 넘기면 설정 diff를 건너뛴다(예: 최초 배포).
# 비교가 대칭적이라서 forward 배포(baseline = 이전 release)와
# rollback(baseline = 실패한 candidate) 양쪽에 같은 함수를 쓸 수 있다.
compute_recreate_set() {
  local baseline="$1"
  {
    list_services_needing_recreate
    list_services_with_stale_compose_context
    if deploy_runtime_config_changed "${baseline}" "${PWD}"; then
      echo "[deploy] main-model runtime configuration changed -> main-llm-vllm" >&2
      echo "main-llm-vllm"
    fi
    if deploy_compose_config_changed "${baseline}" "${PWD}" "${COMPOSE_FILE}"; then
        echo "[deploy] compose file changed -> reconverging all services" >&2
        compose_run config --services 2>/dev/null
    fi
  } | awk 'NF && !seen[$0]++'
}

pull_preflight_image() {
  local label="$1"
  local image="$2"
  echo "[deploy] preflight: verifying ${label} image..."
  if ! docker pull "${image}"; then
    echo "[deploy] ERROR: cannot pull ${label}: ${image}" >&2
    if [[ "${DEPLOY_MODE}" == "full" ]]; then
      echo "[deploy]   For full deploy, confirm build-vllm-derived succeeded in a DEPLOY_MODE=full pipeline." >&2
      echo "[deploy]   Or set RISK_VLLM_IMAGE_TO_DEPLOY" >&2
      echo "[deploy]   to image refs that already exist in the registry." >&2
    else
      echo "[deploy]   Ensure the build-platform CI job completed successfully." >&2
    fi
    exit 1
  fi
  echo "[deploy] ${label} image verified: ${image}"
}

# ── preflight: .env를 건드리기 전에 이미지를 pull할 수 있는지 확인 ───────────────
pull_preflight_image "platform" "${PLATFORM_IMAGE_TO_DEPLOY}"

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  # 새 artifact가 없으면 기존 .env pin을 사용한다. 일반 full 배포도 strict
  # readiness를 실행하지만, image까지 바꾸지는 않아 모델 fleet을 유지할 수 있다.
  if [[ -z "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
    RISK_VLLM_IMAGE_TO_DEPLOY="$(deploy_env_value RISK_VLLM_IMAGE)"
  fi
  pull_preflight_image "risk-prompt vLLM (vllm-unified)" "${RISK_VLLM_IMAGE_TO_DEPLOY}"

  # 12B 멀티모달 이미지는 (compose 서비스가 아니라) 프로필 단위라서 compose가
  # 절대 pull하지 않고, sidecar의 /containers/create도 자동 pull하지 않는다 — 미리
  # 박스에 있지 않으면 12B로 전환할 때 "No such image"로 실패한다. gate가 닫힌 채로
  # 전환 도중에 하지 말고, 지금 채팅 모델이 아직 서빙 중일 때 박스가 쓸 digest를
  # (새 빌드가 있으면 그것, 없으면 현재 .env pin을) 미리 pull해 둔다.
  if [[ -z "${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
    AUDIO_VLLM_IMAGE_TO_DEPLOY="$(deploy_env_value AUDIO_VLLM_IMAGE)"
  fi
  if [[ -n "${AUDIO_VLLM_IMAGE_TO_DEPLOY}" ]]; then
    pull_preflight_image "12B main-LLM vLLM (vllm-unified)" "${AUDIO_VLLM_IMAGE_TO_DEPLOY}"
  fi
fi

# 이미지 참조를 수정하기 전에 .env를 백업
ENV_BACKUP_PATH="${DEPLOY_PATH}/.env.bak.$(date +%Y%m%d%H%M%S)"
ENV_BACKUP="${ENV_BACKUP_PATH}"
cp "${COMPOSE_ENV_FILE}" "${ENV_BACKUP}"
ENV_BACKUP_CREATED=1
echo "[deploy] .env backed up: ${ENV_BACKUP_PATH}"

SERVICES_MUTATED=0
LINKS_MUTATED=0
RUNTIME_STATE_MUTATED=0
RUNTIME_STATE_ORIGINAL_EXISTS=0
RUNTIME_STATE_BACKUP=""

restore_release_links() {
  local restore_failed=0
  local temporary

  if [[ "${LINKS_MUTATED}" != "1" ]]; then
    return 0
  fi

  if [[ -n "${PREVIOUS_CURRENT_LINK}" ]]; then
    temporary="${DEPLOY_PATH}/.current.restore.$$"
    rm -f "${temporary}"
    if ln -s "${PREVIOUS_CURRENT_LINK}" "${temporary}" &&
      mv -Tf "${temporary}" "${DEPLOY_PATH}/current"; then
      echo "[deploy] restored current -> ${PREVIOUS_CURRENT_LINK}" >&2
    else
      rm -f "${temporary}"
      echo "[deploy] ERROR: failed to restore current release link" >&2
      restore_failed=1
    fi
  elif ! rm -f "${DEPLOY_PATH}/current"; then
    echo "[deploy] ERROR: failed to remove newly-created current release link" >&2
    restore_failed=1
  fi

  if [[ -n "${PREVIOUS_RUNTIME_LINK}" ]]; then
    temporary="${DEPLOY_PATH}/.runtime-current.restore.$$"
    rm -f "${temporary}"
    if ln -s "${PREVIOUS_RUNTIME_LINK}" "${temporary}" &&
      mv -Tf "${temporary}" "${DEPLOY_PATH}/runtime-current"; then
      echo "[deploy] restored runtime-current -> ${PREVIOUS_RUNTIME_LINK}" >&2
    else
      rm -f "${temporary}"
      echo "[deploy] ERROR: failed to restore runtime-current release link" >&2
      restore_failed=1
    fi
  elif ! rm -f "${DEPLOY_PATH}/runtime-current"; then
    echo "[deploy] ERROR: failed to remove newly-created runtime release link" >&2
    restore_failed=1
  fi

  return "${restore_failed}"
}

restore_previous_release() {
  local restore_failed=0
  local context_ready=0

  if [[ -n "${ENV_BACKUP:-}" && -f "${ENV_BACKUP}" ]]; then
    if cp "${ENV_BACKUP}" "${COMPOSE_ENV_FILE}"; then
      echo "[deploy] restored .env from ${ENV_BACKUP_PATH}" >&2
    else
      echo "[deploy] ERROR: failed to restore .env from ${ENV_BACKUP_PATH}" >&2
      restore_failed=1
    fi
  else
    echo "[deploy] ERROR: cannot restore .env; backup is missing" >&2
    restore_failed=1
  fi

  if [[ "${RUNTIME_STATE_MUTATED:-0}" == "1" ]]; then
    local runtime_state_path="${DEPLOY_PATH}/.runtime/gateway/runtime-state.json"
    if [[ "${RUNTIME_STATE_ORIGINAL_EXISTS:-0}" == "1" ]]; then
      if [[ -n "${RUNTIME_STATE_BACKUP:-}" && -f "${RUNTIME_STATE_BACKUP}" ]] &&
        cp "${RUNTIME_STATE_BACKUP}" "${runtime_state_path}"; then
        echo "[deploy] restored gateway runtime state from ${RUNTIME_STATE_BACKUP}" >&2
      else
        echo "[deploy] ERROR: failed to restore gateway runtime state" >&2
        restore_failed=1
      fi
    elif rm -f "${runtime_state_path}"; then
      echo "[deploy] removed newly-created gateway runtime state" >&2
    else
      echo "[deploy] ERROR: failed to remove newly-created gateway runtime state" >&2
      restore_failed=1
    fi
  fi

  if [[ -z "${PREVIOUS_RELEASE}" || ! -d "${PREVIOUS_RELEASE}" ]]; then
    echo "[deploy] ERROR: previous release source is unavailable for rollback" >&2
    restore_failed=1
  else
    cd "${PREVIOUS_RELEASE}"
    deploy_export_compose_env
    echo "[deploy] compose environment exported from ${COMPOSE_ENV_FILE}"
    RESTORING_RELEASE=1
    if ! configure_release_context "${PREVIOUS_RELEASE}"; then
      echo "[deploy] ERROR: failed to configure previous release context" >&2
      restore_failed=1
    else
      context_ready=1
    fi
    if ! make sync-runtime-secrets; then
      echo "[deploy] ERROR: failed to resync runtime secrets from restored .env" >&2
      restore_failed=1
    fi
    if [[ "${SERVICES_MUTATED}" == "1" && "${context_ready}" == "1" ]]; then
      echo "[deploy] restoring services from the previous release..." >&2
      if ! compose_run config >/dev/null; then
        echo "[deploy] ERROR: restored compose config is invalid" >&2
        restore_failed=1
      elif [[ "${DEPLOY_MODE}" == "full" ]]; then
        # 대칭적 rollback: 지금 이전 release와 달라진 서비스만 되돌린다 — forward
        # 배포가 썼던 것과 동일한 이미지-ID + 설정-내용 집합을, 실패한 candidate
        # (RELEASE_PATH)를 baseline으로 계산한다. 건드리지 않은 vLLM 모델은 절대
        # cold-restart하지 않는다.
        local _rollback_services
        mapfile -t _rollback_services < <(compute_recreate_set "${RELEASE_PATH}")
        if [[ ${#_rollback_services[@]} -eq 0 ]]; then
          echo "[deploy] no service differs from the previous release; already converged" >&2
        elif ! compose_run up -d --no-deps --remove-orphans "${_rollback_services[@]}"; then
          echo "[deploy] ERROR: failed to restore changed services: ${_rollback_services[*]}" >&2
          restore_failed=1
        else
          echo "[deploy] restored services: ${_rollback_services[*]}" >&2
        fi
      else
        if ! compose_run up -d --no-deps admin-sidecar; then
          echo "[deploy] ERROR: failed to restore the previous admin-sidecar" >&2
          restore_failed=1
        fi
        if ! compose_run up -d --no-deps gateway risk-adapter prometheus grafana; then
          echo "[deploy] ERROR: failed to restore previous app/observability services" >&2
          restore_failed=1
        fi
      fi
    fi
  fi

  if ! restore_release_links; then
    restore_failed=1
  fi

  if [[ "${restore_failed}" == "0" ]]; then
    echo "[deploy] previous release, .env, services, and release links restored" >&2
    return 0
  fi

  echo "[deploy] ERROR: automatic restore was incomplete; manual recovery is required" >&2
  return 1
}

fail_after_env_backup() {
  local message="$*"
  trap - ERR
  restore_previous_release || true
  rm -rf "${RELEASE_PATH}"
  echo "[deploy] ERROR: ${message}" >&2
  if [[ -n "${ENV_BACKUP_PATH:-}" ]]; then
    echo "[deploy] .env backup: ${ENV_BACKUP_PATH}" >&2
  fi
  exit 1
}

unexpected_failure_after_env_backup() {
  local code=$?
  trap - ERR
  restore_previous_release || true
  rm -rf "${RELEASE_PATH}"
  echo "[deploy] ERROR: deploy failed after .env backup was created." >&2
  if [[ -n "${ENV_BACKUP_PATH:-}" ]]; then
    echo "[deploy] .env backup: ${ENV_BACKUP_PATH}" >&2
  fi
  exit "${code}"
}
trap unexpected_failure_after_env_backup ERR

# .env에 PLATFORM_IMAGE 갱신
deploy_set_env_value PLATFORM_IMAGE "${PLATFORM_IMAGE_TO_DEPLOY}"
echo "[deploy] PLATFORM_IMAGE set to ${PLATFORM_IMAGE_TO_DEPLOY}"
deploy_set_env_value DEPLOY_RELEASE_ID "${RELEASE_ID}"
echo "[deploy] DEPLOY_RELEASE_ID set to ${RELEASE_ID}"

# 필요 시 RISK_VLLM_IMAGE 갱신
if [[ -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  deploy_set_env_value RISK_VLLM_IMAGE "${RISK_VLLM_IMAGE_TO_DEPLOY}"
  echo "[deploy] RISK_VLLM_IMAGE set to ${RISK_VLLM_IMAGE_TO_DEPLOY}"

  # VLLM_IMAGE/EMBEDDING_KO_VLLM_IMAGE도 같은 vllm-unified 이미지를 쓰므로 같이 갱신한다.
  deploy_set_env_value VLLM_IMAGE "${RISK_VLLM_IMAGE_TO_DEPLOY}"
  echo "[deploy] VLLM_IMAGE set to ${RISK_VLLM_IMAGE_TO_DEPLOY}"
  deploy_set_env_value EMBEDDING_KO_VLLM_IMAGE "${RISK_VLLM_IMAGE_TO_DEPLOY}"
  echo "[deploy] EMBEDDING_KO_VLLM_IMAGE set to ${RISK_VLLM_IMAGE_TO_DEPLOY}"
fi

# 필요 시 AUDIO_VLLM_IMAGE 갱신 — 12B 프로필이 ${AUDIO_VLLM_IMAGE}로 pin하는
# (configs/main_model_profiles.yaml) derived 멀티모달 런타임이다. 빌드 job이 불변
# digest를 만들어내며, 새 빌드가 없으면 기존 .env 값(=현재 pin)이 그대로 유지되므로
# 일상적인 배포에서는 수동으로 repin할 일이 없다.
if [[ -n "${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  deploy_set_env_value AUDIO_VLLM_IMAGE "${AUDIO_VLLM_IMAGE_TO_DEPLOY}"
  echo "[deploy] AUDIO_VLLM_IMAGE set to ${AUDIO_VLLM_IMAGE_TO_DEPLOY}"
fi

# 마지막 배포 이후 template에 추가된 새 키를 동기화(기존 값은 보존)
echo "[deploy] syncing .env template keys..."
if ! make sync-env; then
  fail_after_env_backup "sync-env failed"
fi

if [[ -n "${AUTH_MODE:-}" ]]; then
  echo "[deploy] applying auth profile: ${AUTH_MODE}"
  if ! make auth-apply MODE="${AUTH_MODE}"; then
    fail_after_env_backup "auth profile apply failed"
  fi
fi

# 현재 .env에서 Prometheus bearer token 동기화(rsync 제외 대상)
echo "[deploy] syncing runtime secrets..."
if ! make sync-runtime-secrets; then
  fail_after_env_backup "runtime secret sync failed"
fi

# Docker Compose는 shell 환경변수를 --env-file보다 우선시킨다.
# 변경된 원격 .env를 export해서, 프로세스 env 값이 필수 compose 변수를 가리지 못하게 한다.
deploy_export_compose_env
echo "[deploy] compose environment exported from ${COMPOSE_ENV_FILE}"

# 동기화된 .env는 stale한 크로스 변수 불변식(예: 타임아웃 값을 올렸는데
# REQUEST_TIMEOUT_SECONDS가 MAIN_LLM_TIMEOUT_SECONDS보다 낮게 남아있는 경우)을
# 갖고 있을 수 있는데, 이건 gateway 프로세스가 실제로 부팅되어
# settings.load_settings() 내부의 validate_timeout_budget()을 거칠 때만 실패로
# 드러난다. 이걸 확인하지 않고 넘어가면, compose가 서비스를 재생성하고 아래
# 600초 /health 대기가 타임아웃난 뒤에야 문제가 드러난다. 방금 동기화된 .env에
# 대해 여기서 동일한 app-creation 경로를 실행해두면, 어떤 컨테이너도 건드리기 전에
# 미리 잡아낼 수 있다.
echo "[deploy] validating gateway settings against synced .env..."
# APP_CONFIG_ROOT는 그 자체로 .env 키가 아니다 — compose가 env_file이 아니라
# gateway 서비스의 `environment:` 블록(ops/compose/full-stack.private-network.yaml)을
# 통해 주입한다. 이게 없으면 GatewayClients가 Path(__file__).parents[3]로 폴백하는데,
# 이게 설치된 이미지 안에서는 /app이 아니라 site-packages 아래로 resolve되어
# load_runtime_topology()가 configs/model_serving.yaml에서 (본질과 무관한 이유로)
# 404를 낸다. 이 검증이 진짜 설정 문제일 때만 실패하도록 명시적으로 값을 지정한다.
if ! docker run --rm --env-file "${DEPLOY_PATH}/.env" -e APP_CONFIG_ROOT=/app \
  --entrypoint python "${PLATFORM_IMAGE_TO_DEPLOY}" \
  -c "from ai_model_serving.apps.gateway import create_gateway_app; create_gateway_app()"; then
  fail_after_env_backup "gateway settings failed to load from synced .env — check timeout/limit invariants (REQUEST_TIMEOUT_SECONDS, MAIN_LLM_TIMEOUT_SECONDS, RISK_ADAPTER_TIMEOUT_SECONDS) before retrying"
fi

if ! configure_release_context "${RELEASE_PATH}"; then
  fail_after_env_backup "candidate release context is invalid"
fi
if ! resolve_deferred_runtimes; then
  fail_after_env_backup "invalid DEPLOY_DEFERRED_RUNTIMES=${DEPLOY_DEFERRED_RUNTIMES}"
fi
if ! apply_deferred_runtime_state; then
  fail_after_env_backup "failed to apply deferred runtime desired state"
fi
if [[ "${DEPLOY_MODE}" == "full" ]]; then
  echo "[deploy] main-model boot profile: ${MAIN_MODEL_BOOT_PROFILE}"
fi

echo "[deploy] validating compose config with ${COMPOSE_ENV_FILE}..."
if ! COMPOSE_PROJECT_EFFECTIVE="$(
  compose_run config --format json |
    "${_PYTHON_BIN}" -c 'import json, sys; print(json.load(sys.stdin)["name"])'
)"; then
  fail_after_env_backup "compose config interpolation failed after .env update"
fi
if [[ -z "${COMPOSE_PROJECT_EFFECTIVE}" ]]; then
  fail_after_env_backup "effective Compose project name is empty"
fi

# 후보 release의 compose config 검증이 끝난 뒤, 실제 컨테이너를 변경하기 전에
# current/runtime-current를 원자적으로 전환한다. 이후 모든 Compose 명령은
# release 절대경로가 아니라 안정적인 current 경로에서 실행한다. 그래야 같은
# Compose project의 컨테이너가 release마다 서로 다른 working_dir label을 남기지
# 않고, rollback도 previous current link를 복원한 뒤 같은 context에서 수행된다.
echo "[deploy] activating release ${RELEASE_ID} before Compose convergence..."
CURRENT_LINK_TMP="${DEPLOY_PATH}/.current.${RELEASE_ID}.$$"
LINKS_MUTATED=1
ln -s "releases/${RELEASE_ID}" "${CURRENT_LINK_TMP}"
mv -Tf "${CURRENT_LINK_TMP}" "${DEPLOY_PATH}/current"
echo "[deploy] current -> releases/${RELEASE_ID}"
if [[ "${DEPLOY_MODE}" == "full" ]]; then
  RUNTIME_LINK_TMP="${DEPLOY_PATH}/.runtime-current.${RELEASE_ID}.$$"
  ln -s "releases/${RELEASE_ID}" "${RUNTIME_LINK_TMP}"
  mv -Tf "${RUNTIME_LINK_TMP}" "${DEPLOY_PATH}/runtime-current"
  RUNTIME_RELEASE="${RELEASE_PATH}"
  echo "[deploy] runtime-current -> releases/${RELEASE_ID}"
fi

if ! configure_release_context "${DEPLOY_PATH}/current"; then
  fail_after_env_backup "activated release context is invalid"
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  HF_CACHE_HOST="$(
    "${_PYTHON_BIN}" scripts/models/resolve_hf_cache_dir.py \
      --env-file "${COMPOSE_ENV_FILE}" \
      --compose-file "${COMPOSE_FILE}"
  )"
  if ! mkdir -p "${HF_CACHE_HOST}" || [[ ! -w "${HF_CACHE_HOST}" ]]; then
    fail_after_env_backup "Hugging Face cache directory is not writable: ${HF_CACHE_HOST}"
  fi
  echo "[deploy] preparing main-model cache for ${MAIN_MODEL_BOOT_PROFILE}..."
  if ! docker run --rm \
    --user 0:0 \
    --entrypoint python \
    -e HF_TOKEN \
    -e HUGGING_FACE_HUB_TOKEN \
    -e HF_HOME=/root/.cache/huggingface \
    -v "${HF_CACHE_HOST}:/root/.cache/huggingface" \
    "${PLATFORM_IMAGE_TO_DEPLOY}" \
    -m ai_model_serving.main_model.cache_cli \
    --profile "${MAIN_MODEL_BOOT_PROFILE}"; then
    fail_after_env_backup "main-model cache prepare failed for ${MAIN_MODEL_BOOT_PROFILE}"
  fi
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  echo "[deploy] full deploy: pulling all compose images..."
  if ! compose_run pull; then
    fail_after_env_backup "image pull failed during full deploy. If vLLM-derived images are new, confirm build-vllm-derived succeeded or set an existing RISK_VLLM_IMAGE_TO_DEPLOY ref."
  fi
  # 이미지 또는 resolve된 설정이 실제로 바뀐 서비스만 수렴시킨다.
  # 안 바뀐 vLLM 모델은 계속 서빙 상태를 유지하므로, readiness gate는 실제로
  # 교체된 그 서비스 하나와만 경합한다 — fleet 전체 순차 cold start가 아니다.
  echo "[deploy] full deploy: computing changed services..."
  mapfile -t CHANGED_SERVICES < <(compute_recreate_set "${PREVIOUS_RELEASE}")

  if [[ ${#CHANGED_SERVICES[@]} -eq 0 ]]; then
    echo "[deploy] full deploy: all services already converged; nothing to recreate"
  else
    ACTIVE_CHANGED_SERVICES=()
    DEFERRED_CHANGED_SERVICES=()
    for service in "${CHANGED_SERVICES[@]}"; do
      if is_deferred_service "${service}"; then
        DEFERRED_CHANGED_SERVICES+=("${service}")
      else
        ACTIVE_CHANGED_SERVICES+=("${service}")
      fi
    done
    echo "[deploy] full deploy: recreating changed services: ${ACTIVE_CHANGED_SERVICES[*]:-(none)}"
    SERVICES_MUTATED=1
    # --no-deps는 필수다: 이게 없으면 `up -d gateway`가 gateway의 depends_on
    # 그래프를 끌고 오는데, 공유 .env가 바뀌어 모든 서비스의 config-hash가 바뀌었기
    # 때문에 결국 Compose가 vLLM fleet 전체를 재생성해버린다. --no-deps를 쓰면
    # 목록에 있는(진짜로 바뀐) 서비스만 건드린다.
    if [[ ${#ACTIVE_CHANGED_SERVICES[@]} -gt 0 ]] &&
      ! compose_run up -d --no-deps --remove-orphans "${ACTIVE_CHANGED_SERVICES[@]}"; then
      fail_after_env_backup "compose up failed for changed services: ${ACTIVE_CHANGED_SERVICES[*]}"
    fi
    if [[ ${#DEFERRED_CHANGED_SERVICES[@]} -gt 0 ]]; then
      echo "[deploy] full deploy: creating deferred runtime containers without starting: ${DEFERRED_CHANGED_SERVICES[*]}"
      if ! compose_run create --force-recreate "${DEFERRED_CHANGED_SERVICES[@]}"; then
        fail_after_env_backup "compose create failed for deferred services: ${DEFERRED_CHANGED_SERVICES[*]}"
      fi
    fi
  fi
else
  # application/control-plane 이미지만 pull한다. Gateway와 Admin Sidecar는
  # 하나의 관리 API를 구현하므로 반드시 같은 revision으로 배포해야 한다.
  echo "[deploy] rolling deploy: pulling app/control-plane images..."
  if ! compose_run pull gateway admin-sidecar risk-adapter; then
    fail_after_env_backup "rolling deploy image pull failed for gateway/admin-sidecar/risk-adapter"
  fi

  # vLLM은 건드리지 않는다. 새 Gateway가 구버전 control-plane 구현을 바라보는
  # 일이 없도록 sidecar를 먼저 올린다.
  echo "[deploy] rolling deploy: restarting app/control-plane services..."
  SERVICES_MUTATED=1
  if ! compose_run up -d --no-deps admin-sidecar; then
    fail_after_env_backup "rolling deploy restart failed for admin-sidecar"
  fi
  if ! compose_run up -d --no-deps gateway risk-adapter; then
    fail_after_env_backup "rolling deploy restart failed for gateway/risk-adapter"
  fi
fi

# bind-mount 설정 서비스는 이미지가 바뀌지 않아도 설정 파일 변경 시 재생성이
# 필요하다. 컨테이너는 생성 시점 release의 bind mount를 유지하므로 `current`만
# 바꾸면 이전 설정을 계속 서빙한다. 배포 모드와 무관하게 각 서비스의 설정 경로가
# 바뀌었거나 release context가 오래됐을 때, 해당 서비스만 재생성한다.
CONFIG_SERVICES_TO_REFRESH=()
CONFIG_SERVICE_STATE_FILES=()
CONFIG_SERVICE_FINGERPRINTS=()
_config_state_dir="${DEPLOY_PATH}/.runtime/deploy-state/bind-mounted-config"
mkdir -p "${_config_state_dir}"
_has_previous_release=0
if [[ -n "${PREVIOUS_RELEASE:-}" && -d "${PREVIOUS_RELEASE}" ]]; then
  _has_previous_release=1
fi
_expected_working_dir="${DEPLOY_PATH}/current/ops/compose"
for _config_service_spec in "${BIND_MOUNTED_CONFIG_SERVICE_SPECS[@]}"; do
  _config_service="${_config_service_spec%%:*}"
  _config_paths="${_config_service_spec#*:}"
  _config_service_changed=0
  read -r -a _config_path_array <<< "${_config_paths}"
  if [[ ${_has_previous_release} -eq 1 ]]; then
    for _config_relative_path in "${_config_path_array[@]}"; do
      if ! diff -rq \
        "${PREVIOUS_RELEASE}/${_config_relative_path}" \
        "${RELEASE_PATH}/${_config_relative_path}" >/dev/null 2>&1; then
        _config_service_changed=1
        break
      fi
    done
  fi

  _config_fingerprint="$(bind_mounted_config_fingerprint "${RELEASE_PATH}" "${_config_path_array[@]}")"
  _config_state_file="${_config_state_dir}/${_config_service}.sha256"
  _applied_fingerprint="$(cat "${_config_state_file}" 2>/dev/null || true)"

  # 최초 full 배포는 앞선 compose up이 컨테이너를 새 release 설정으로 만들었으므로
  # 여기서 다시 재시작하지 않고, 성공 완료 시 적용 fingerprint만 초기화한다.
  if [[ ${_has_previous_release} -eq 0 ]]; then
    CONFIG_SERVICE_STATE_FILES+=("${_config_state_file}")
    CONFIG_SERVICE_FINGERPRINTS+=("${_config_fingerprint}")
    continue
  fi

  _config_service_stale=0
  if [[ "${_applied_fingerprint}" != "${_config_fingerprint}" ]]; then
    _config_service_stale=1
    echo "[deploy] ${_config_service} bind-mounted config is not applied to the running service"
  fi
  _container_id="$(compose_run ps -q "${_config_service}" 2>/dev/null || true)"
  if [[ -z "${_container_id}" ]]; then
    _config_service_stale=1
    echo "[deploy] ${_config_service} is not running in the target compose project"
  else
    _running_working_dir="$(
      docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
        "${_container_id}" 2>/dev/null || true
    )"
    if [[ "${_running_working_dir}" != "${_expected_working_dir}" ]]; then
      _config_service_stale=1
      echo "[deploy] ${_config_service} uses stale release context: ${_running_working_dir:-unknown}"
    fi
  fi

  if [[ ${_config_service_changed} -eq 1 || ${_config_service_stale} -eq 1 ]]; then
    CONFIG_SERVICES_TO_REFRESH+=("${_config_service}")
    CONFIG_SERVICE_STATE_FILES+=("${_config_state_file}")
    CONFIG_SERVICE_FINGERPRINTS+=("${_config_fingerprint}")
  fi
done
if [[ ${#CONFIG_SERVICES_TO_REFRESH[@]} -gt 0 ]]; then
  echo "[deploy] bind-mounted config requires refresh — force-recreating: ${CONFIG_SERVICES_TO_REFRESH[*]}"
  # 새 collector로 전환할 때 이전 collector가 orphan으로 남으면 같은 docker
  # json-file을 계속 전송해 legacy stream과 새 stream이 함께 쌓인다. Compose
  # 정의를 source of truth로 삼아 이 refresh 경로에서도 orphan을 정리한다.
  if ! compose_run up -d --no-deps --force-recreate --remove-orphans "${CONFIG_SERVICES_TO_REFRESH[@]}"; then
    fail_after_env_backup "deploy restart failed for bind-mounted config services: ${CONFIG_SERVICES_TO_REFRESH[*]}"
  fi
fi

if [[ "${RUN_READY_SMOKE}" == "1" ]]; then
  echo "[deploy] waiting for gateway /health (up to 600s)..."
  GATEWAY_PORT="${GATEWAY_PORT:-$(deploy_env_value GATEWAY_PORT)}"
  GATEWAY_PORT="${GATEWAY_PORT:-9400}"
  GATEWAY_PROBE_HOST="${GATEWAY_BIND_ADDR:-$(deploy_env_value GATEWAY_BIND_ADDR)}"
  if [[ -z "${GATEWAY_PROBE_HOST}" || "${GATEWAY_PROBE_HOST}" == "0.0.0.0" ]]; then
    GATEWAY_PROBE_HOST="localhost"
  fi
  HEALTH_URL="${GATEWAY_HEALTH_URL:-http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT}/health}"
  for i in $(seq 1 60); do
    # 연결 또는 응답이 멈춰도 한 probe가 무한정 대기하면 안 된다. 이 제한이 있어야
    # 아래 60회 × 10초 재시도 안내가 실제 최대 대기 시간과 일치한다.
    if curl -sf --connect-timeout 3 --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
      echo "[deploy] gateway /health OK"
      break
    fi
    if [[ "$i" == "60" ]]; then
      fail_after_env_backup "gateway /health not ready after 600s"
    fi
    echo "[deploy] waiting... ${i}/60 (${HEALTH_URL})"
    sleep 10
  done
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  echo "[deploy] full deploy: running make ready-full..."
  if ! SMOKE_SKIP_RUNTIMES="$(
    IFS=,
    echo "${DEFERRED_RUNTIME_KEYS[*]}"
  )" make ready-full; then
    make compose-diagnostics || true
    fail_after_env_backup "full runtime readiness failed"
  fi
fi

trap - ERR

mapfile -t RELEASE_DIRS < <(
  find "${DEPLOY_PATH}/releases" -mindepth 1 -maxdepth 1 -type d \
    -printf '%T@ %p\n' |
    sort -nr |
    cut -d' ' -f2-
)
for ((index=RELEASES_TO_KEEP; index<${#RELEASE_DIRS[@]}; index++)); do
  old_release="${RELEASE_DIRS[$index]}"
  if [[ "${old_release}" != "${RELEASE_PATH}" && "${old_release}" != "${RUNTIME_RELEASE}" ]]; then
    if rm -rf "${old_release}"; then
      echo "[deploy] pruned old release: ${old_release}"
    else
      echo "[deploy] WARNING: failed to prune old release: ${old_release}" >&2
    fi
  fi
done
if ! rm -f "${ENV_BACKUP}"; then
  echo "[deploy] WARNING: failed to remove .env backup: ${ENV_BACKUP}" >&2
fi
if [[ -n "${RUNTIME_STATE_BACKUP:-}" ]] && ! rm -f "${RUNTIME_STATE_BACKUP}"; then
  echo "[deploy] WARNING: failed to remove runtime state backup: ${RUNTIME_STATE_BACKUP}" >&2
fi

# 성공한 배포만 실제 적용 fingerprint를 기록한다. 다음 release는 이 상태와 비교해
# bind-mounted 설정이 파일에는 반영됐지만 실행 중 컨테이너에는 미적용인 경우를
# 감지하고 해당 서비스만 재생성한다.
for _config_state_index in "${!CONFIG_SERVICE_STATE_FILES[@]}"; do
  printf '%s\n' "${CONFIG_SERVICE_FINGERPRINTS[${_config_state_index}]}" \
    > "${CONFIG_SERVICE_STATE_FILES[${_config_state_index}]}"
done

echo "[deploy] done"
REMOTE
