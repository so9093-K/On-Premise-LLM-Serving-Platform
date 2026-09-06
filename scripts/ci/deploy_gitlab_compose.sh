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
#                              (예: main_only, retrieval_ready). 생략 시 파일의
#                              default_profile을 사용한다.
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

# tests/는 두 배포 경로 모두에서 포함한다. bootstrap(make first-run)이 `make test`를
# 배포 전 게이트로 부르므로, 테스트가 빠진 배포본에서는 문서화된 진입점이
# no tests collected(exit 5)로 중단된다 -- 게이트를 부르면서 게이트 입력을 빼는 셈이다.
#
# 예전에는 여기서 제외하고 package_release.sh와 정책을 맞췄는데, 그 배제 근거(크기·
# 공격 표면)를 재보니 셋 다 성립하지 않았다: 압축 후 111KB(전체 +4%), 앱이 import하지
# 않고 .dockerignore가 컨테이너 유입을 막는다. 두 경로의 정책은 여전히 같아야 하고,
# 지금은 "포함"으로 같다.
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
  PYTHONDONTWRITEBYTECODE=1 \
  bash -s <<'REMOTE'
set -euo pipefail

# gateway 컨테이너는 이 디렉터리를 bind-mount해서 이미지의 non-root appuser 권한으로
# runtime-state.json을 쓴다. 이걸 (대개 root인) 배포 사용자 권한으로 mkdir하면
# appuser가 쓸 수 없어서 gateway 컨테이너가 PermissionError로 기동에 실패한다.
# 대신 platform 이미지 내부에서 생성하면 그 이미지가 실제로 실행되는 UID로
# 항상 소유권이 잡힌다.
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
source "${RELEASE_PATH}/scripts/lib/gateway_runtime_state.sh"

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
    "configs/deploy_profiles.yaml" \
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
  mapfile -t _unified_image_source < <(vllm_unified_image_source_paths)
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
export DEPLOY_ACTIVE_RUNTIMES=""  # 롤백 전용 신호. 요청자 입력은 받지 않는다.
DEFERRED_RUNTIME_KEYS=()
DEFERRED_RUNTIME_SERVICES=()
DEFERRED_RUNTIME_WAS_RUNNING=()
DEFERRED_RUNTIME_WAS_RUNNING_KEYS=()
RESTORE_FAILURES=()
DEPLOY_RUNTIME_PROFILE_EFFECTIVE=""
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
  # exposure mode -> overlay 매핑은 resolve_exposure_mode.py가 단일 기준이다.
  # 예전에는 resolver가 없는 구버전 release로 롤백하는 경우를 위해 여기에 축소
  # 복사본을 두고 있었다. 롤백은 항상 직전 release 하나만 대상으로 하고
  # (PREVIOUS_RELEASE = current가 가리키던 곳), resolver는 그보다 훨씬 오래
  # 존재해왔다. 두 모드만 아는 복사본으로 조용히 추측하느니 소리내서 실패한다.
  if [[ ! -f "scripts/compose/resolve_exposure_mode.py" ]]; then
    echo "[deploy] ERROR: exposure mode resolver is missing in ${release_path}" >&2
    return 1
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
      # gateway_runtime_state.sh와 같은 종류의 문제: admin-sidecar 컨테이너는
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
      --project-name "${COMPOSE_PROJECT_NAME:-ai-model-serving-platform}" \
      "${compose_args[@]}" \
      --env-file "${COMPOSE_ENV_FILE}" \
      "$@"
}

resolve_deferred_runtimes() {
  DEFERRED_RUNTIME_KEYS=()
  DEFERRED_RUNTIME_SERVICES=()
  DEPLOY_RUNTIME_PROFILE_EFFECTIVE=""
  [[ "${DEPLOY_MODE}" == "full" ]] || return 0
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
  DEPLOY_RUNTIME_PROFILE_EFFECTIVE="${_resolved_lines[2]:-}"
  if [[ ${#DEFERRED_RUNTIME_KEYS[@]} -gt 0 ]]; then
    echo "[deploy] runtime profile ${DEPLOY_RUNTIME_PROFILE_EFFECTIVE:-direct}: deferred ${DEFERRED_RUNTIME_KEYS[*]} (${DEFERRED_RUNTIME_SERVICES[*]})"
  elif [[ -n "${DEPLOY_RUNTIME_PROFILE_EFFECTIVE:-}" ]]; then
    echo "[deploy] runtime profile ${DEPLOY_RUNTIME_PROFILE_EFFECTIVE}: no deferred runtimes"
  fi
}

# `|| true`로 실패를 삼키면 "그런 컨테이너가 없다"와 "Docker/Compose 조회가
# 실패했다"가 똑같이 빈 문자열이 된다. 후자를 전자로 오인하면 원래 돌고 있던
# 런타임을 안 돌고 있었다고 판단해, 배포 실패 시 복구가 잘못된 상태로 끝난다.
# 조회 실패는 삼키지 않고 배포를 세운다.
capture_deferred_runtime_state() {
  DEFERRED_RUNTIME_WAS_RUNNING=()
  DEFERRED_RUNTIME_WAS_RUNNING_KEYS=()
  local idx service container_id running
  for idx in "${!DEFERRED_RUNTIME_SERVICES[@]}"; do
    service="${DEFERRED_RUNTIME_SERVICES[idx]}"
    if ! container_id="$(compose_run ps --all -q "${service}" 2>/dev/null)"; then
      echo "[deploy] ERROR: compose ps failed while inspecting deferred runtime: ${service}" >&2
      return 1
    fi
    # 조회는 성공했고 결과가 비었다 -- 컨테이너가 아직 없는 정상 상태다.
    [[ -n "${container_id}" ]] || continue
    if ! running="$(docker inspect -f '{{.State.Running}}' "${container_id}" 2>/dev/null)"; then
      echo "[deploy] ERROR: docker inspect failed for ${service} (${container_id})" >&2
      return 1
    fi
    if [[ "${running}" == "true" ]]; then
      DEFERRED_RUNTIME_WAS_RUNNING+=("${service}")
      # runtime key는 service와 같은 인덱스다(resolve_deferred_runtimes 참고).
      DEFERRED_RUNTIME_WAS_RUNNING_KEYS+=("${DEFERRED_RUNTIME_KEYS[idx]}")
    fi
  done
}

# deferred 런타임은 "컨테이너는 만들되 시작하지 않는다". `docker compose create`는
# --no-deps를 지원하지 않아(unknown flag) 배포가 통째로 실패한다. `up --no-start`가
# 같은 의미이면서 --no-deps를 받는다 -- 이게 없으면 risk-prompt-vllm의 depends_on을
# 따라 embedding·main-llm까지 force-recreate되어 GPU 모델이 전부 다시 뜬다.
enforce_deferred_runtime_state() {
  [[ ${#DEFERRED_RUNTIME_SERVICES[@]} -gt 0 ]] || return 0
  local service container_id running
  for service in "${DEFERRED_RUNTIME_SERVICES[@]}"; do
    container_id="$(compose_run ps --all -q "${service}" 2>/dev/null || true)"
    if [[ -z "${container_id}" ]]; then
      echo "[deploy] creating deferred runtime without starting: ${service}"
      SERVICES_MUTATED=1
      compose_run up --no-deps --no-start "${service}" || return 1
      continue
    fi
    running="$(docker inspect -f '{{.State.Running}}' "${container_id}" 2>/dev/null || true)"
    if [[ "${running}" == "true" ]]; then
      echo "[deploy] stopping deferred runtime: ${service}"
      SERVICES_MUTATED=1
      compose_run stop "${service}" || return 1
    fi
  done
}

is_deferred_service() {
  local service="$1"
  local deferred
  for deferred in "${DEFERRED_RUNTIME_SERVICES[@]}"; do
    [[ "${service}" == "${deferred}" ]] && return 0
  done
  return 1
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
#
# 이 라벨의 실제 소비자는 compose_context_assert_mutation_safe다. 그쪽은 양쪽을
# readlink -f로 해석한 뒤 비교하므로, 여기서도 같은 기준을 써야 한다. 문자열로
# 비교하면 가리키는 디렉터리가 같은데도(롤백 직후 current가 이전 release를
# 가리키는 경우) 어긋난 것으로 보여, 멀쩡한 서비스를 재생성하게 된다.
# 해석이 실패하는 경로(=삭제된 release)는 원문 그대로 비교해 어긋난 것으로 남긴다.
_resolved_compose_context() {
  local path="$1"
  readlink -f "${path}" 2>/dev/null || printf '%s' "${path}"
}

list_services_with_stale_compose_context() {
  local expected_context service container_id actual_context
  expected_context="$(_resolved_compose_context "${DEPLOY_PATH}/current/ops/compose")"
  while read -r service; do
    [[ -n "${service}" ]] || continue
    container_id="$(compose_run ps -q "${service}" 2>/dev/null || true)"
    [[ -n "${container_id}" ]] || continue
    actual_context="$(
      docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
        "${container_id}" 2>/dev/null || true
    )"
    [[ -n "${actual_context}" ]] || continue
    actual_context="$(_resolved_compose_context "${actual_context}")"
    if [[ "${actual_context}" != "${expected_context}" ]]; then
      echo "[deploy] ${service} uses stale Compose context: ${actual_context:-unknown}" >&2
      echo "${service}"
    fi
  done < <(compose_run config --services)
}

# 내용이 실제로 바뀌어 (재)생성해야 할 서비스 집합을 중복 없이 출력한다.
# compose context 라벨 수렴은 여기 넣지 않는다 -- 그건 내용 변경이 아니라서
# compose의 config-hash 비교로는 절대 반영되지 않고, force-recreate가 필요한
# 별도 단계다. 다음을 합친다:
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
# compose 파일 변경을 서비스 단위로 좁힌다.
#
# 파일 전체를 cmp하면 어느 서비스가 바뀌었는지 알 수 없어 전부 재생성하게 되고,
# 직렬로 뜨는 GPU 모델까지 다시 태운다. 렌더된 정의를 비교하면 anchor와 변수
# 치환이 펼쳐진 뒤라 영향을 실제로 받는 서비스에서만 차이가 드러난다.
#
# 렌더에 실패하면 좁히지 않는다 -- 안전한 쪽은 전부 재생성이다.
_render_compose_services() {
  COMPOSE_SERVICE_ENV_FILE="${COMPOSE_ENV_FILE}" \
    docker compose \
      --project-name "${COMPOSE_PROJECT_NAME:-ai-model-serving-platform}" \
      -f "$1" \
      --env-file "${COMPOSE_ENV_FILE}" \
      config --format json
}

list_services_with_changed_compose_definition() {
  local baseline="$1"
  local before after
  before="$(mktemp)" || return 1
  after="$(mktemp)" || {
    rm -f "${before}"
    return 1
  }
  if ! _render_compose_services "${baseline}/${COMPOSE_FILE}" >"${before}" 2>/dev/null ||
    ! _render_compose_services "${PWD}/${COMPOSE_FILE}" >"${after}" 2>/dev/null; then
    rm -f "${before}" "${after}"
    echo "[deploy] WARNING: cannot render both compose revisions; reconverging all services" >&2
    compose_run config --services 2>/dev/null
    return 0
  fi
  if ! "${_PYTHON_BIN}" scripts/compose/compose_service_diff.py \
    --before "${before}" --after "${after}" \
    --strip-before "${baseline}" --strip-after "${PWD}"; then
    rm -f "${before}" "${after}"
    echo "[deploy] WARNING: cannot compare rendered compose definitions; reconverging all services" >&2
    compose_run config --services 2>/dev/null
    return 0
  fi
  rm -f "${before}" "${after}"
}

compute_recreate_set() {
  local baseline="$1"
  {
    list_services_needing_recreate
    if deploy_runtime_config_changed "${baseline}" "${PWD}"; then
      echo "[deploy] main-model runtime configuration changed -> main-llm-vllm" >&2
      echo "main-llm-vllm"
    fi
    if deploy_compose_config_changed "${baseline}" "${PWD}" "${COMPOSE_FILE}"; then
        echo "[deploy] compose file changed -> comparing rendered service definitions" >&2
        list_services_with_changed_compose_definition "${baseline}"
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

# 이미지를 확보한 뒤에야 그 이미지에게 runtime uid를 물을 수 있다.
if ! ensure_gateway_runtime_dir "${DEPLOY_PATH}/${GATEWAY_RUNTIME_DIR_RELPATH}" "${PLATFORM_IMAGE_TO_DEPLOY}"; then
  echo "[deploy] ERROR: gateway runtime state directory is not usable" >&2
  exit 1
fi

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

# 롤백 중 실패한 단계를 기록한다. 마지막에 "manual recovery is required"만
# 남기면 운영자가 무엇을 손봐야 하는지 로그를 거슬러 올라가 찾아야 한다.
restore_failure() {
  echo "[deploy] ERROR: $*" >&2
  RESTORE_FAILURES+=("$*")
  restore_failed=1
}

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
      restore_failure "failed to restore current release link"
    fi
  elif ! rm -f "${DEPLOY_PATH}/current"; then
    restore_failure "failed to remove newly-created current release link"
  fi

  if [[ -n "${PREVIOUS_RUNTIME_LINK}" ]]; then
    temporary="${DEPLOY_PATH}/.runtime-current.restore.$$"
    rm -f "${temporary}"
    if ln -s "${PREVIOUS_RUNTIME_LINK}" "${temporary}" &&
      mv -Tf "${temporary}" "${DEPLOY_PATH}/runtime-current"; then
      echo "[deploy] restored runtime-current -> ${PREVIOUS_RUNTIME_LINK}" >&2
    else
      rm -f "${temporary}"
      restore_failure "failed to restore runtime-current release link"
    fi
  elif ! rm -f "${DEPLOY_PATH}/runtime-current"; then
    restore_failure "failed to remove newly-created runtime release link"
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
      restore_failure "failed to restore .env from ${ENV_BACKUP_PATH}"
    fi
  else
    restore_failure "cannot restore .env; backup is missing"
  fi


  if [[ -z "${PREVIOUS_RELEASE}" || ! -d "${PREVIOUS_RELEASE}" ]]; then
    restore_failure "previous release source is unavailable for rollback"
  else
    cd "${PREVIOUS_RELEASE}"
    deploy_export_compose_env
    echo "[deploy] compose environment exported from ${COMPOSE_ENV_FILE}"
    RESTORING_RELEASE=1
    if ! configure_release_context "${PREVIOUS_RELEASE}"; then
      restore_failure "failed to configure previous release context"
    else
      context_ready=1
    fi
    if ! "${_PYTHON_BIN}" scripts/config/setup_env.py --sync-runtime-secrets --env-file "${COMPOSE_ENV_FILE}"; then
      restore_failure "failed to resync runtime secrets from restored .env"
    fi
    if [[ "${SERVICES_MUTATED}" == "1" && "${context_ready}" == "1" ]]; then
      echo "[deploy] restoring services from the previous release..." >&2
      # 이번 배포의 deferred 지시는 넘기지 않고, 배포 직전에 돌고 있던 런타임만
      # desired state로 되돌린다.
      export DEPLOY_DEFERRED_RUNTIMES=""
      export_runtime_restore_directive "${DEFERRED_RUNTIME_WAS_RUNNING_KEYS[@]}"
      if ! compose_run config >/dev/null; then
        restore_failure "restored compose config is invalid"
      elif [[ "${DEPLOY_MODE}" == "full" ]]; then
        # 대칭적 rollback: 지금 이전 release와 달라진 서비스만 되돌린다 — forward
        # 배포가 썼던 것과 동일한 이미지-ID + 설정-내용 집합을, 실패한 candidate
        # (RELEASE_PATH)를 baseline으로 계산한다. 건드리지 않은 vLLM 모델은 절대
        # cold-restart하지 않는다.
        local _rollback_services _rollback_active_services _rollback_deferred_services
        mapfile -t _rollback_services < <(compute_recreate_set "${RELEASE_PATH}")
        _rollback_active_services=()
        _rollback_deferred_services=()
        local _rollback_service
        for _rollback_service in "${_rollback_services[@]}"; do
          if is_deferred_service "${_rollback_service}"; then
            _rollback_deferred_services+=("${_rollback_service}")
          else
            _rollback_active_services+=("${_rollback_service}")
          fi
        done
        if [[ ${#_rollback_services[@]} -eq 0 ]]; then
          echo "[deploy] no service differs from the previous release; already converged" >&2
        fi
        if [[ ${#_rollback_active_services[@]} -gt 0 ]]; then
          if compose_run up -d --no-deps --remove-orphans "${_rollback_active_services[@]}"; then
            echo "[deploy] restored active services: ${_rollback_active_services[*]}" >&2
          else
            restore_failure "failed to restore active services: ${_rollback_active_services[*]}"
          fi
        fi
        if [[ ${#_rollback_deferred_services[@]} -gt 0 ]]; then
          if compose_run up --no-deps --no-start --force-recreate "${_rollback_deferred_services[@]}"; then
            echo "[deploy] restored stopped runtime containers: ${_rollback_deferred_services[*]}" >&2
          else
            restore_failure "failed to restore stopped runtime containers: ${_rollback_deferred_services[*]}"
          fi
        fi
        if [[ ${#DEFERRED_RUNTIME_WAS_RUNNING[@]} -gt 0 ]]; then
          if compose_run up -d --no-deps "${DEFERRED_RUNTIME_WAS_RUNNING[@]}"; then
            echo "[deploy] restored previously running runtimes: ${DEFERRED_RUNTIME_WAS_RUNNING[*]}" >&2
          else
            restore_failure "failed to restart previously running runtimes: ${DEFERRED_RUNTIME_WAS_RUNNING[*]}"
          fi
        fi
      else
        if ! compose_run up -d --no-deps admin-sidecar; then
          restore_failure "failed to restore the previous admin-sidecar"
        fi
        if ! compose_run up -d --no-deps gateway risk-adapter prometheus grafana; then
          restore_failure "failed to restore previous app/observability services"
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
  local _failure
  for _failure in "${RESTORE_FAILURES[@]}"; do
    echo "[deploy]   미복원: ${_failure}" >&2
  done
  return 1
}

fail_after_env_backup() {
  local message="$*"
  trap - ERR
  if restore_previous_release; then
    rm -rf "${RELEASE_PATH}"
  else
    echo "[deploy] ERROR: candidate release retained because restoration was incomplete: ${RELEASE_PATH}" >&2
  fi
  echo "[deploy] ERROR: ${message}" >&2
  if [[ -n "${ENV_BACKUP_PATH:-}" ]]; then
    echo "[deploy] .env backup: ${ENV_BACKUP_PATH}" >&2
  fi
  exit 1
}

unexpected_failure_after_env_backup() {
  local code=$?
  trap - ERR
  if restore_previous_release; then
    rm -rf "${RELEASE_PATH}"
  else
    echo "[deploy] ERROR: candidate release retained because restoration was incomplete: ${RELEASE_PATH}" >&2
  fi
  echo "[deploy] ERROR: deploy failed after .env backup was created." >&2
  if [[ -n "${ENV_BACKUP_PATH:-}" ]]; then
    echo "[deploy] .env backup: ${ENV_BACKUP_PATH}" >&2
  fi
  exit "${code}"
}
trap unexpected_failure_after_env_backup ERR

interrupted_after_env_backup() {
  local signal="$1"
  local code="$2"
  trap - ERR INT TERM HUP
  if restore_previous_release; then
    rm -rf "${RELEASE_PATH}"
  else
    echo "[deploy] ERROR: candidate release retained because restoration was incomplete: ${RELEASE_PATH}" >&2
  fi
  echo "[deploy] ERROR: deploy interrupted by ${signal}; previous release restoration attempted." >&2
  if [[ -n "${ENV_BACKUP_PATH:-}" ]]; then
    echo "[deploy] .env backup: ${ENV_BACKUP_PATH}" >&2
  fi
  exit "${code}"
}
trap 'interrupted_after_env_backup INT 130' INT
trap 'interrupted_after_env_backup TERM 143' TERM
trap 'interrupted_after_env_backup HUP 129' HUP

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
if ! make sync-env ENV_FILE="${COMPOSE_ENV_FILE}"; then
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
if ! "${_PYTHON_BIN}" scripts/config/setup_env.py --sync-runtime-secrets --env-file "${COMPOSE_ENV_FILE}"; then
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
# load_runtime_topology()가 configs/runtime_topology.yaml을 찾다가
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
if ! capture_deferred_runtime_state; then
  fail_after_env_backup "failed to capture deferred runtime state"
fi
# 배포는 Gateway의 runtime-state.json을 직접 쓰지 않는다. 그 파일이 있는
# .runtime/gateway는 컨테이너 안의 non-root 사용자가 쓰는 bind-mount인데, 배포
# 사용자와 컨테이너가 함께 쓰려면 양쪽 uid가 모두 쓸 수 있어야 한다. 이미지의 uid와
# 호스트 uid 사이에는 아무 관계가 없어 그걸 보장할 방법이 없고, 실제로 배포의 쓰기가
# Permission denied로 실패한 뒤 그 실패가 조용히 넘어가 deferred 지시가 반영되지
# 않은 채 배포가 성공으로 끝난 적이 있다. 그래서 지시만 env로 넘긴다.
#
# 요청자가 준 원문이 아니라 해석된 목록을 넘긴다 -- 원문은 프로필 이름이나 이
# 타깃에서 비활성인 런타임을 담을 수 있다. release id는 .env가 이미 갖고 있으므로
# 여기서 다시 정하지 않는다(빈 값).
if ! export_deferred_runtime_directive "" "${DEFERRED_RUNTIME_KEYS[@]}"; then
  fail_after_env_backup "failed to resolve deferred runtime directive"
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

# deferred 런타임을 다루는 명령은 평소 배포에서 실행되지 않는다 -- 그 서비스가
# 변경 집합에 들어갈 때만 처음 실행된다. 실제로 존재하지 않는 플래그
# (`compose create --no-deps`)가 그렇게 오래 숨어 있다가 배포와 rollback을 함께
# 무너뜨렸다. 컨테이너를 건드리기 전에 같은 명령을 dry-run으로 한 번 태운다.
preflight_deferred_runtime_commands() {
  [[ ${#DEFERRED_RUNTIME_SERVICES[@]} -gt 0 ]] || return 0
  local service
  for service in "${DEFERRED_RUNTIME_SERVICES[@]}"; do
    if ! compose_run up --no-deps --no-start --force-recreate "${service}" --dry-run >/dev/null; then
      echo "[deploy] ERROR: deferred runtime command is not executable for ${service}" >&2
      return 1
    fi
  done
  echo "[deploy] deferred runtime commands verified (dry-run)"
}

if ! preflight_deferred_runtime_commands; then
  fail_after_env_backup "deferred runtime command preflight failed"
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
      if ! compose_run up --no-deps --no-start --force-recreate "${DEFERRED_CHANGED_SERVICES[@]}"; then
        fail_after_env_backup "compose create failed for deferred services: ${DEFERRED_CHANGED_SERVICES[*]}"
      fi
    fi
  fi

  # compose context 라벨은 compose 자신의 config-hash 비교 대상이 아니라서 위의
  # `up -d`로는 절대 갱신되지 않는다. 라벨이 어긋난 컨테이너가 하나라도 있으면
  # compose_context_assert_mutation_safe가 이 project에 대한 로컬 운영 명령
  # (compose-up/compose-restart/stop)을 전부 거부하므로, 이 부분집합만 따로
  # force-recreate해서 안정 경로로 수렴시킨다. 위에서 방금 재생성된 서비스는
  # 이미 올바른 라벨을 갖고 있어 여기 걸리지 않는다.
  mapfile -t STALE_CONTEXT_SERVICES < <(list_services_with_stale_compose_context)
  if [[ ${#STALE_CONTEXT_SERVICES[@]} -gt 0 ]]; then
    STALE_ACTIVE_SERVICES=()
    STALE_DEFERRED_SERVICES=()
    for service in "${STALE_CONTEXT_SERVICES[@]}"; do
      if is_deferred_service "${service}"; then
        STALE_DEFERRED_SERVICES+=("${service}")
      else
        STALE_ACTIVE_SERVICES+=("${service}")
      fi
    done
    SERVICES_MUTATED=1
    if [[ ${#STALE_ACTIVE_SERVICES[@]} -gt 0 ]]; then
      echo "[deploy] full deploy: converging stale Compose context: ${STALE_ACTIVE_SERVICES[*]}"
      if ! compose_run up -d --no-deps --force-recreate "${STALE_ACTIVE_SERVICES[@]}"; then
        fail_after_env_backup "failed to converge stale Compose context: ${STALE_ACTIVE_SERVICES[*]}"
      fi
    fi
    if [[ ${#STALE_DEFERRED_SERVICES[@]} -gt 0 ]]; then
      # deferred runtime은 시작하지 않는다. up -d로 올리면 VRAM을 잡는다.
      echo "[deploy] full deploy: converging stale Compose context without starting: ${STALE_DEFERRED_SERVICES[*]}"
      if ! compose_run up --no-deps --no-start --force-recreate "${STALE_DEFERRED_SERVICES[@]}"; then
        fail_after_env_backup "failed to converge stale Compose context for deferred services: ${STALE_DEFERRED_SERVICES[*]}"
      fi
    fi
  fi
  if ! enforce_deferred_runtime_state; then
    fail_after_env_backup "failed to keep deferred runtimes stopped"
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
if ! _bind_mounted_config_raw="$(
  bind_mounted_config_service_specs "${RELEASE_PATH}" "${COMPOSE_FILE}"
)"; then
  fail_after_env_backup "failed to derive bind-mounted config services from ${COMPOSE_FILE}"
fi
BIND_MOUNTED_CONFIG_SERVICE_SPECS=()
if [[ -n "${_bind_mounted_config_raw}" ]]; then
  mapfile -t BIND_MOUNTED_CONFIG_SERVICE_SPECS <<<"${_bind_mounted_config_raw}"
fi

# 파생 집합에 없는 상태 파일은 회수한다. 목록을 손으로 적던 시절, compose에서
# 사라진 서비스(promtail)의 fingerprint가 계속 남아 있었다. 생성만 있고 회수가
# 없으면 이 디렉터리는 배포할수록 과거만 쌓인다.
_derived_config_services=()
for _config_service_spec in "${BIND_MOUNTED_CONFIG_SERVICE_SPECS[@]}"; do
  _derived_config_services+=("${_config_service_spec%%:*}")
done
for _orphan_state_file in "${_config_state_dir}"/*.sha256; do
  [[ -e "${_orphan_state_file}" ]] || continue
  _orphan_service="$(basename "${_orphan_state_file}" .sha256)"
  _orphan_still_derived=0
  for _derived_config_service in "${_derived_config_services[@]}"; do
    if [[ "${_orphan_service}" == "${_derived_config_service}" ]]; then
      _orphan_still_derived=1
      break
    fi
  done
  if [[ ${_orphan_still_derived} -eq 0 ]]; then
    if rm -f "${_orphan_state_file}"; then
      echo "[deploy] removed deploy state for a service that no longer bind-mounts release config: ${_orphan_service}"
    else
      echo "[deploy] WARNING: failed to remove stale deploy state: ${_orphan_state_file}" >&2
    fi
  fi
done

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
    # 컨테이너가 실제로 묶인 release의 설정과 이번 release의 설정을 비교한다.
    # 바로 앞 release와의 diff(_config_service_changed)만으로는, 컨테이너가 여러
    # release 뒤에 묶여 있고 변경이 그 사이에 있었던 경우를 놓친다.
    _bound_release="$(container_bound_release "${_container_id}" "${DEPLOY_PATH}" || true)"
    if [[ -z "${_bound_release}" ]]; then
      _config_service_stale=1
      echo "[deploy] ${_config_service} bound release could not be resolved — recreating to be safe"
    elif [[ ! -d "${_bound_release}" ]]; then
      _config_service_stale=1
      echo "[deploy] ${_config_service} is bound to a pruned release: ${_bound_release}"
    elif [[ "$(bind_mounted_config_fingerprint "${_bound_release}" "${_config_path_array[@]}")" != "${_config_fingerprint}" ]]; then
      _config_service_stale=1
      echo "[deploy] ${_config_service} serves config from an older release: ${_bound_release}"
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
  if [[ -z "${GATEWAY_PORT}" ]]; then
    GATEWAY_PORT="$("${_PYTHON_BIN}" - <<'PY'
from pathlib import Path
import yaml

print(int(yaml.safe_load(Path("configs/services.yaml").read_text(encoding="utf-8"))["services"]["gateway"]["default_host_port"]))
PY
)"
  fi
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
# bind mount는 컨테이너 시작 시점의 실제 경로에 고정된다. 아직 그 release를 마운트한
# 컨테이너가 도는 중에 디렉터리를 지우면 마운트가 죽고(`//deleted`), 서비스는 설정
# 파일을 잃은 채 계속 돈다 -- 재시작 전까지 드러나지도 않는다. 실제로 관측 스택이
# 이렇게 망가진 적이 있다(grafana provisioning, loki/alloy config).
mapfile -t RELEASES_IN_USE < <(releases_in_use_by_running_containers "${DEPLOY_PATH}")
for ((index=RELEASES_TO_KEEP; index<${#RELEASE_DIRS[@]}; index++)); do
  old_release="${RELEASE_DIRS[$index]}"
  if [[ "${old_release}" == "${RELEASE_PATH}" || "${old_release}" == "${RUNTIME_RELEASE}" ]]; then
    continue
  fi
  release_in_use=0
  for in_use_release in "${RELEASES_IN_USE[@]}"; do
    if [[ "${old_release}" == "${in_use_release}" ]]; then
      release_in_use=1
      break
    fi
  done
  if [[ ${release_in_use} -eq 1 ]]; then
    echo "[deploy] keeping release still bind-mounted by a running container: ${old_release}"
    continue
  fi
  if rm -rf "${old_release}"; then
    echo "[deploy] pruned old release: ${old_release}"
  else
    echo "[deploy] WARNING: failed to prune old release: ${old_release}" >&2
  fi
done
if ! rm -f "${ENV_BACKUP}"; then
  echo "[deploy] WARNING: failed to remove .env backup: ${ENV_BACKUP}" >&2
fi

# 실패한 배포는 성공 경로의 정리를 밟지 못해 백업을 남긴다. release는 여기서
# 회수되지만 백업은 회수되는 곳이 없어 무한히 쌓인다. 이 배포가 만든 이름 규칙
# (.env.bak.<타임스탬프>)만 대상으로, 최근 것 몇 개를 남기고 지운다.
mapfile -t _stale_env_backups < <(
  find "${DEPLOY_PATH}" -maxdepth 1 -name '.env.bak.[0-9]*' -printf '%f\n' 2>/dev/null |
    sort -r | tail -n +4
)
for _stale_backup in "${_stale_env_backups[@]}"; do
  if rm -f "${DEPLOY_PATH}/${_stale_backup}"; then
    echo "[deploy] pruned stale .env backup: ${_stale_backup}"
  fi
done

# 성공한 배포만 실제 적용 fingerprint를 기록한다. 다음 release는 이 상태와 비교해
# bind-mounted 설정이 파일에는 반영됐지만 실행 중 컨테이너에는 미적용인 경우를
# 감지하고 해당 서비스만 재생성한다.
for _config_state_index in "${!CONFIG_SERVICE_STATE_FILES[@]}"; do
  printf '%s\n' "${CONFIG_SERVICE_FINGERPRINTS[${_config_state_index}]}" \
    > "${CONFIG_SERVICE_STATE_FILES[${_config_state_index}]}"
done

echo "[deploy] done"
REMOTE
