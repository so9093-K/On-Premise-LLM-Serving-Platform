#!/usr/bin/env bash
# CI deployment script: 111(runner) → 175(GPU runtime)
#
# Required environment variables (set as GitLab CI/CD variables):
#   PLATFORM_IMAGE_TO_DEPLOY   full image ref to deploy (e.g. registry.../platform:sha)
#   DEPLOY_HOST                175 server IP or hostname
#   DEPLOY_USER                SSH user on 175
#   DEPLOY_PATH                deployment root on 175 (e.g. /opt/acl-ai-gateway)
#   CI_REGISTRY                GitLab Container Registry host
#   REGISTRY_DEPLOY_USER / REGISTRY_DEPLOY_PASSWORD
#                              preferred read_registry deploy token credentials
#                              (falls back to CI_REGISTRY_USER / CI_REGISTRY_PASSWORD)
#
# Optional:
#   RISK_VLLM_IMAGE_TO_DEPLOY         full runtime deploy override for RISK_VLLM_IMAGE;
#                                     allowed only when DEPLOY_MODE=full
#   RISK_VLLM_IMAGE_SHA               default risk-vllm-kanana image when DEPLOY_MODE=full
#   DEPLOY_COMPOSE_FILE               compose file relative to DEPLOY_PATH
#                              default: ops/compose/full-stack.private-network.yaml
#   DEPLOY_MODE                auto-detected (rolling unless vLLM image changes or
#                              runtime-sensitive files change); can be forced to full
#   GATEWAY_HEALTH_URL         explicit post-deploy health URL.
#                              Default is derived from 175 .env:
#                              GATEWAY_BIND_ADDR/GATEWAY_PORT, with 0.0.0.0 -> localhost.
#   RUN_READY_SMOKE            1 (default) or 0 — run gateway /health check after deploy
#   RUN_READY_FULL_SMOKE       compatibility variable. Full deploy requires 1
#                              and always runs make ready-full after /health.
#   PRUNE_DANGLING_IMAGES      1 (default) or 0 — prune dangling images after a successful deploy
#   DEPLOY_RELEASE_ID          immutable release directory name; defaults to CI_COMMIT_SHA
#   RELEASES_TO_KEEP           successful release directories to retain (default: 5)
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
PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES:-1}"
RELEASES_TO_KEEP="${RELEASES_TO_KEEP:-5}"
RELEASE_ID="${DEPLOY_RELEASE_ID:-${CI_COMMIT_SHA:-}}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

# Auto-detect deploy mode. Full deploy is required when a vLLM image override is
# provided; otherwise default to rolling (platform-only restart, no vLLM downtime).
# DEPLOY_MODE can still be set explicitly to force full when needed.
_vllm_image_override="${RISK_VLLM_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_SHA:-}}"
if [[ -z "${DEPLOY_MODE:-}" ]]; then
  if [[ -n "${_vllm_image_override}" ]]; then
    DEPLOY_MODE="full"
    echo "[deploy] auto mode: full (vLLM image override provided)"
  else
    DEPLOY_MODE="rolling"
    echo "[deploy] auto mode: rolling (platform-only change)"
  fi
fi

if [[ -z "${RELEASE_ID}" ]]; then
  RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
if [[ ! "${RELEASE_ID}" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
  echo "[deploy] ERROR: DEPLOY_RELEASE_ID must contain only A-Za-z0-9._- and be <=128 chars." >&2
  exit 2
fi
if [[ ! "${RELEASES_TO_KEEP}" =~ ^[1-9][0-9]*$ ]]; then
  echo "[deploy] ERROR: RELEASES_TO_KEEP must be a positive integer." >&2
  exit 2
fi
RELEASE_PATH="${DEPLOY_PATH}/releases/${RELEASE_ID}"

case "${DEPLOY_MODE}" in
  rolling|full) ;;
  *)
    echo "[deploy] ERROR: DEPLOY_MODE must be rolling or full, got: ${DEPLOY_MODE}" >&2
    exit 2
    ;;
esac

echo "[deploy] target: ${SSH_TARGET}:${DEPLOY_PATH}"
echo "[deploy] platform image: ${PLATFORM_IMAGE_TO_DEPLOY}"
echo "[deploy] compose file: ${COMPOSE_FILE}"
echo "[deploy] mode: ${DEPLOY_MODE}"
echo "[deploy] release: ${RELEASE_ID}"

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_SHA:-}}"
  if [[ -z "${RISK_VLLM_IMAGE_TO_DEPLOY}" ]]; then
    echo "[deploy] ERROR: full deploy requires RISK_VLLM_IMAGE_TO_DEPLOY or RISK_VLLM_IMAGE_SHA." >&2
    exit 2
  fi
fi

# ── 1. stage immutable release files ────────────────────────────────────────
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

# ── 2. remote: validate candidate, deploy, then atomically switch current ──
ssh "${SSH_TARGET}" \
  PLATFORM_IMAGE_TO_DEPLOY="${PLATFORM_IMAGE_TO_DEPLOY}" \
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-}" \
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
  PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES}" \
  AUTH_MODE="${AUTH_MODE:-}" \
  bash -s <<'REMOTE'
set -euo pipefail

if [[ ! -d "${RELEASE_PATH}" ]]; then
  echo "[deploy] ERROR: staged release not found: ${RELEASE_PATH}" >&2
  exit 1
fi
if [[ ! -f "${DEPLOY_PATH}/.env" ]]; then
  echo "[deploy] ERROR: shared .env not found at ${DEPLOY_PATH}/.env" >&2
  echo "[deploy] Run bootstrap on the deployment root before the first CI deployment." >&2
  rm -rf "${RELEASE_PATH}"
  exit 1
fi
mkdir -p "${DEPLOY_PATH}/.runtime" "${DEPLOY_PATH}/ops/compose/model_cache"

PREVIOUS_RELEASE=""
LEGACY_RELEASE_CREATED=""
if [[ -L "${DEPLOY_PATH}/current" ]]; then
  if ! PREVIOUS_RELEASE="$(readlink -f "${DEPLOY_PATH}/current")" ||
    [[ ! -d "${PREVIOUS_RELEASE}" ]]; then
    echo "[deploy] ERROR: current release link is broken" >&2
    rm -rf "${RELEASE_PATH}"
    exit 1
  fi
elif [[ -f "${DEPLOY_PATH}/Makefile" && -f "${DEPLOY_PATH}/${COMPOSE_FILE}" ]]; then
  LEGACY_RELEASE_CREATED="${DEPLOY_PATH}/releases/legacy-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  echo "[deploy] legacy live tree detected; snapshotting it to ${LEGACY_RELEASE_CREATED}"
  mkdir "${LEGACY_RELEASE_CREATED}"
  if ! rsync -a --delete \
    --exclude "/releases/" \
    --exclude "/current" \
    --exclude "/runtime-current" \
    --exclude "/.env" \
    --exclude "/.env.bak.*" \
    --exclude "/.runtime/" \
    --exclude "/ops/compose/model_cache/" \
    "${DEPLOY_PATH}/" "${LEGACY_RELEASE_CREATED}/"; then
    rm -rf "${LEGACY_RELEASE_CREATED}" "${RELEASE_PATH}"
    echo "[deploy] ERROR: failed to snapshot legacy live tree" >&2
    exit 1
  fi
  ln -s "${DEPLOY_PATH}/.env" "${LEGACY_RELEASE_CREATED}/.env"
  ln -s "${DEPLOY_PATH}/.runtime" "${LEGACY_RELEASE_CREATED}/.runtime"
  mkdir -p "${LEGACY_RELEASE_CREATED}/ops/compose"
  ln -s "${DEPLOY_PATH}/ops/compose/model_cache" \
    "${LEGACY_RELEASE_CREATED}/ops/compose/model_cache"
  ln -s "releases/$(basename "${LEGACY_RELEASE_CREATED}")" \
    "${DEPLOY_PATH}/.current.legacy.$$"
  mv -Tf "${DEPLOY_PATH}/.current.legacy.$$" "${DEPLOY_PATH}/current"
  ln -s "releases/$(basename "${LEGACY_RELEASE_CREATED}")" \
    "${DEPLOY_PATH}/.runtime-current.legacy.$$"
  mv -Tf "${DEPLOY_PATH}/.runtime-current.legacy.$$" \
    "${DEPLOY_PATH}/runtime-current"
  PREVIOUS_RELEASE="${LEGACY_RELEASE_CREATED}"
  echo "[deploy] legacy snapshot activated as the initial release-directory baseline"
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

cleanup_staged_candidate() {
  rm -rf "${RELEASE_PATH}"
}
trap cleanup_staged_candidate EXIT

cd "${RELEASE_PATH}"
COMPOSE_ENV_FILE="${DEPLOY_PATH}/.env"

if [[ "${DEPLOY_MODE}" == "rolling" ]]; then
  if [[ -z "${PREVIOUS_RELEASE}" || ! -d "${PREVIOUS_RELEASE}" ]]; then
    echo "[deploy] ERROR: first release-directory deployment requires DEPLOY_MODE=full." >&2
    rm -rf "${RELEASE_PATH}"
    exit 2
  fi
  runtime_sensitive_files=(
    "ops/compose/full-stack.private-network.yaml"
    "configs/main_model_profiles.yaml"
    "configs/gemma4_chat_template.jinja"
  )
  _changed_sensitive=()
  for relative_path in "${runtime_sensitive_files[@]}"; do
    if ! cmp -s \
      "${PREVIOUS_RELEASE}/${relative_path}" \
      "${RELEASE_PATH}/${relative_path}"; then
      _changed_sensitive+=("${relative_path}")
    fi
  done
  if [[ ${#_changed_sensitive[@]} -gt 0 ]]; then
    echo "[deploy] runtime-sensitive files changed — auto-upgrading to full deploy:"
    for _f in "${_changed_sensitive[@]}"; do echo "[deploy]   ${_f}"; done
    DEPLOY_MODE="full"
    if [[ -z "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
      RISK_VLLM_IMAGE_TO_DEPLOY="$(get_env_value RISK_VLLM_IMAGE)"
      echo "[deploy] keeping current RISK_VLLM_IMAGE: ${RISK_VLLM_IMAGE_TO_DEPLOY}"
    fi
  fi
fi

# registry login with read-only deploy token
echo "${REGISTRY_PASSWORD}" | \
  docker login "${CI_REGISTRY}" -u "${REGISTRY_USER}" --password-stdin

get_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' \
    "${COMPOSE_ENV_FILE}"
}

COMPOSE_EXPORTED_KEYS=()
export_compose_env_from_file() {
  local key value
  if ((${#COMPOSE_EXPORTED_KEYS[@]})); then
    unset "${COMPOSE_EXPORTED_KEYS[@]}"
  fi
  COMPOSE_EXPORTED_KEYS=()
  while IFS='=' read -r key value; do
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "${key}=${value}"
    COMPOSE_EXPORTED_KEYS+=("${key}")
  done < "${COMPOSE_ENV_FILE}"
  echo "[deploy] compose environment exported from ${COMPOSE_ENV_FILE}"
}

_PYTHON_BIN="$(command -v python3.12 || command -v python3 || command -v python)"
_exposure_mode=""
COMPOSE_OVERRIDE=""
MAIN_MODEL_BOOT_OVERRIDE=""
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
      echo "[deploy] ERROR: main-model state file exists but is not readable by the deploy user." >&2
      echo "[deploy]   Fix: sudo chmod o+r ${_state_file}" >&2
      echo "[deploy]   This happens when the admin-sidecar container wrote the file as a different user." >&2
      return 1
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

# ── preflight: verify images can be pulled before touching .env ───────────────
pull_preflight_image "platform" "${PLATFORM_IMAGE_TO_DEPLOY}"

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  pull_preflight_image "risk-vllm-kanana" "${RISK_VLLM_IMAGE_TO_DEPLOY}"
fi

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" "${COMPOSE_ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${COMPOSE_ENV_FILE}"
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> "${COMPOSE_ENV_FILE}"
  fi
}

# back up .env before modifying image refs
ENV_BACKUP_PATH="${DEPLOY_PATH}/.env.bak.$(date +%Y%m%d%H%M%S)"
ENV_BACKUP="${ENV_BACKUP_PATH}"
cp "${COMPOSE_ENV_FILE}" "${ENV_BACKUP}"
ENV_BACKUP_CREATED=1
echo "[deploy] .env backed up: ${ENV_BACKUP_PATH}"

SERVICES_MUTATED=0
LINKS_MUTATED=0

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

  if [[ -z "${PREVIOUS_RELEASE}" || ! -d "${PREVIOUS_RELEASE}" ]]; then
    echo "[deploy] ERROR: previous release source is unavailable for rollback" >&2
    restore_failed=1
  else
    cd "${PREVIOUS_RELEASE}"
    export_compose_env_from_file
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
        if ! compose_run up -d --remove-orphans; then
          echo "[deploy] ERROR: failed to restore the previous full stack" >&2
          restore_failed=1
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

# update PLATFORM_IMAGE in .env
set_env_value PLATFORM_IMAGE "${PLATFORM_IMAGE_TO_DEPLOY}"
echo "[deploy] PLATFORM_IMAGE set to ${PLATFORM_IMAGE_TO_DEPLOY}"
set_env_value DEPLOY_RELEASE_ID "${RELEASE_ID}"
echo "[deploy] DEPLOY_RELEASE_ID set to ${RELEASE_ID}"

# optionally update RISK_VLLM_IMAGE
if [[ -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  set_env_value RISK_VLLM_IMAGE "${RISK_VLLM_IMAGE_TO_DEPLOY}"
  echo "[deploy] RISK_VLLM_IMAGE set to ${RISK_VLLM_IMAGE_TO_DEPLOY}"
fi

# sync any new template keys added since last deploy (preserves existing values)
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

# sync Prometheus bearer token from current .env (excluded from rsync)
echo "[deploy] syncing runtime secrets..."
if ! make sync-runtime-secrets; then
  fail_after_env_backup "runtime secret sync failed"
fi

# Docker Compose gives shell environment variables precedence over --env-file.
# Export the mutated remote .env so process env values cannot shadow required compose variables.
export_compose_env_from_file

if ! configure_release_context "${RELEASE_PATH}"; then
  fail_after_env_backup "candidate release context is invalid"
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
    -m ai_model_serving.model_cache_cli \
    --profile "${MAIN_MODEL_BOOT_PROFILE}"; then
    fail_after_env_backup "main-model cache prepare failed for ${MAIN_MODEL_BOOT_PROFILE}"
  fi
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  echo "[deploy] full deploy: pulling all compose images..."
  if ! compose_run pull; then
    fail_after_env_backup "image pull failed during full deploy. If vLLM-derived images are new, confirm build-vllm-derived succeeded or set an existing RISK_VLLM_IMAGE_TO_DEPLOY ref."
  fi
  echo "[deploy] full deploy: starting stack..."
  SERVICES_MUTATED=1
  if ! compose_run up -d --remove-orphans; then
    fail_after_env_backup "full stack compose up failed"
  fi
else
  # Pull the application/control-plane images only. Gateway and Admin Sidecar
  # implement one management API and must be deployed at the same revision.
  echo "[deploy] rolling deploy: pulling app/control-plane images..."
  if ! compose_run pull gateway admin-sidecar risk-adapter; then
    fail_after_env_backup "rolling deploy image pull failed for gateway/admin-sidecar/risk-adapter"
  fi

  # Keep vLLM intact. Bring the sidecar up first so the new Gateway never
  # targets an older control-plane implementation.
  echo "[deploy] rolling deploy: restarting app/control-plane services..."
  SERVICES_MUTATED=1
  if ! compose_run up -d --no-deps admin-sidecar; then
    fail_after_env_backup "rolling deploy restart failed for admin-sidecar"
  fi
  if ! compose_run up -d --no-deps gateway risk-adapter; then
    fail_after_env_backup "rolling deploy restart failed for gateway/risk-adapter"
  fi
fi

if [[ "${RUN_READY_SMOKE}" == "1" ]]; then
  echo "[deploy] waiting for gateway /health (up to 600s)..."
  GATEWAY_PORT="${GATEWAY_PORT:-$(get_env_value GATEWAY_PORT)}"
  GATEWAY_PORT="${GATEWAY_PORT:-9400}"
  GATEWAY_PROBE_HOST="${GATEWAY_BIND_ADDR:-$(get_env_value GATEWAY_BIND_ADDR)}"
  if [[ -z "${GATEWAY_PROBE_HOST}" || "${GATEWAY_PROBE_HOST}" == "0.0.0.0" ]]; then
    GATEWAY_PROBE_HOST="localhost"
  fi
  HEALTH_URL="${GATEWAY_HEALTH_URL:-http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT}/health}"
  for i in $(seq 1 60); do
    if curl -sf "${HEALTH_URL}" >/dev/null 2>&1; then
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
  if ! make ready-full; then
    make compose-diagnostics || true
    fail_after_env_backup "full runtime readiness failed"
  fi
fi

echo "[deploy] activating release ${RELEASE_ID}..."
CURRENT_LINK_TMP="${DEPLOY_PATH}/.current.${RELEASE_ID}.$$"
RUNTIME_LINK_TMP=""
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

if [[ "${PRUNE_DANGLING_IMAGES}" == "1" ]]; then
  echo "[deploy] pruning dangling docker images..."
  if ! docker image prune -f --filter dangling=true >/dev/null; then
    echo "[deploy] WARNING: dangling docker image prune failed after successful deploy" >&2
  fi
fi

echo "[deploy] done"
REMOTE
