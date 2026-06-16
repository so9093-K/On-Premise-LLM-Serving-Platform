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
#   DEPLOY_MODE                rolling (default) or full
#   GATEWAY_HEALTH_URL         explicit post-deploy health URL.
#                              Default is derived from 175 .env:
#                              GATEWAY_BIND_ADDR/GATEWAY_PORT, with 0.0.0.0 -> localhost.
#   RUN_READY_SMOKE            1 (default) or 0 — run gateway /health check after deploy
#   RUN_READY_FULL_SMOKE       1 or 0 (default) — when DEPLOY_MODE=full, run make ready-full
#                              after /health to verify downstream vLLM readiness and smoke
#   PRUNE_DANGLING_IMAGES      1 (default) or 0 — prune dangling images after a successful deploy
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
DEPLOY_MODE="${DEPLOY_MODE:-rolling}"
RUN_READY_SMOKE="${RUN_READY_SMOKE:-1}"
RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE:-0}"
PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES:-1}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"

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

if [[ "${DEPLOY_MODE}" == "rolling" && -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  echo "[deploy] ERROR: vLLM image overrides are allowed only with DEPLOY_MODE=full." >&2
  echo "[deploy]   Rolling deploy only updates gateway and risk-adapter." >&2
  echo "[deploy]   RISK_VLLM_IMAGE_TO_DEPLOY is invalid for rolling deploy." >&2
  echo "[deploy]   Full runtime deploy requires DEPLOY_MODE=full." >&2
  exit 2
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_SHA:-}}"
  if [[ -z "${RISK_VLLM_IMAGE_TO_DEPLOY}" ]]; then
    echo "[deploy] ERROR: DEPLOY_MODE=full requires RISK_VLLM_IMAGE_TO_DEPLOY or RISK_VLLM_IMAGE_SHA." >&2
    echo "[deploy]   deploy-gpu-175 defaults RISK_VLLM_IMAGE_TO_DEPLOY from RISK_VLLM_IMAGE_SHA." >&2
    exit 2
  fi
fi

# ── 1. sync deployable project files ───────────────────────────────────────
echo "[deploy] syncing deployable project files to ${SSH_TARGET}:${DEPLOY_PATH}/"
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
  "${SSH_TARGET}:${DEPLOY_PATH}/"

# ── 2. remote: login, update .env image refs, pull, up ────────────────────
ssh "${SSH_TARGET}" \
  PLATFORM_IMAGE_TO_DEPLOY="${PLATFORM_IMAGE_TO_DEPLOY}" \
  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-}" \
  CI_REGISTRY="${CI_REGISTRY}" \
  REGISTRY_USER="${REGISTRY_USER}" \
  REGISTRY_PASSWORD="${REGISTRY_PASSWORD}" \
  DEPLOY_PATH="${DEPLOY_PATH}" \
  COMPOSE_FILE="${COMPOSE_FILE}" \
  DEPLOY_MODE="${DEPLOY_MODE}" \
  HF_CACHE_DIR="${HF_CACHE_DIR:-}" \
  HF_TOKEN="${HF_TOKEN:-}" \
  HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN:-}" \
  GATEWAY_HEALTH_URL="${GATEWAY_HEALTH_URL:-}" \
  RUN_READY_SMOKE="${RUN_READY_SMOKE}" \
  RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE}" \
  PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES}" \
  AUTH_MODE="${AUTH_MODE:-}" \
  bash -s <<'REMOTE'
set -euo pipefail

cd "${DEPLOY_PATH}"
COMPOSE_ENV_FILE="${DEPLOY_PATH}/.env"

if [[ ! -f .env ]]; then
  echo "[deploy] ERROR: .env not found at ${DEPLOY_PATH}/.env" >&2
  echo "[deploy] Run bootstrap on 175 first to generate .env" >&2
  exit 1
fi

# registry login with read-only deploy token
echo "${REGISTRY_PASSWORD}" | \
  docker login "${CI_REGISTRY}" -u "${REGISTRY_USER}" --password-stdin

if [[ "${DEPLOY_MODE}" == "rolling" && -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  echo "[deploy] ERROR: vLLM image overrides are allowed only with DEPLOY_MODE=full." >&2
  echo "[deploy]   Rolling deploy only updates gateway and risk-adapter." >&2
  echo "[deploy]   RISK_VLLM_IMAGE_TO_DEPLOY is invalid for rolling deploy." >&2
  echo "[deploy]   Full runtime deploy requires DEPLOY_MODE=full." >&2
  exit 2
fi

get_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env
}

export_compose_env_from_file() {
  local key value
  while IFS='=' read -r key value; do
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "${key}=${value}"
  done < .env
  echo "[deploy] compose environment exported from ${COMPOSE_ENV_FILE}"
}

compose_file_dir_host() {
  if [[ "${COMPOSE_FILE}" = /* ]]; then
    dirname "${COMPOSE_FILE}"
  else
    dirname "${DEPLOY_PATH}/${COMPOSE_FILE}"
  fi
}

resolve_compose_relative_path() {
  local raw="$1"
  local base_dir
  base_dir="$(compose_file_dir_host)"

  if [[ "${raw}" = /* ]]; then
    printf '%s\n' "${raw}"
  else
    raw="${raw#./}"
    printf '%s/%s\n' "${base_dir}" "${raw}"
  fi
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
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> .env
  fi
}

# back up .env before modifying image refs
ENV_BACKUP_PATH=""
ENV_BACKUP=".env.bak.$(date +%Y%m%d%H%M%S)"
cp .env "${ENV_BACKUP}"
ENV_BACKUP_PATH="${DEPLOY_PATH}/${ENV_BACKUP}"
echo "[deploy] .env backed up: ${ENV_BACKUP_PATH}"

fail_after_env_backup() {
  echo "[deploy] ERROR: $*" >&2
  if [[ -n "${ENV_BACKUP_PATH:-}" ]]; then
    echo "[deploy] .env backup: ${ENV_BACKUP_PATH}" >&2
  fi
  exit 1
}

unexpected_failure_after_env_backup() {
  local code=$?
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

echo "[deploy] validating compose config with ${COMPOSE_ENV_FILE}..."
if ! docker compose -f "${COMPOSE_FILE}" --env-file "${COMPOSE_ENV_FILE}" config >/dev/null; then
  fail_after_env_backup "compose config interpolation failed after .env update"
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  echo "[deploy] full deploy: pulling all compose images..."
  if ! docker compose -f "${COMPOSE_FILE}" --env-file "${COMPOSE_ENV_FILE}" pull; then
    fail_after_env_backup "image pull failed during full deploy. If vLLM-derived images are new, confirm build-vllm-derived succeeded or set an existing RISK_VLLM_IMAGE_TO_DEPLOY ref."
  fi
  echo "[deploy] full deploy: starting stack..."
  if ! docker compose -f "${COMPOSE_FILE}" --env-file "${COMPOSE_ENV_FILE}" up -d --remove-orphans; then
    fail_after_env_backup "full stack compose up failed"
  fi
else
  # pull gateway + risk-adapter images only (vLLM images are large; pull separately when needed)
  echo "[deploy] rolling deploy: pulling gateway and risk-adapter images..."
  if ! docker compose -f "${COMPOSE_FILE}" --env-file "${COMPOSE_ENV_FILE}" pull gateway risk-adapter; then
    fail_after_env_backup "rolling deploy image pull failed for gateway/risk-adapter"
  fi

  # rolling restart: gateway + risk-adapter (no vLLM downtime)
  echo "[deploy] rolling deploy: restarting gateway and risk-adapter..."
  if ! docker compose -f "${COMPOSE_FILE}" --env-file "${COMPOSE_ENV_FILE}" up -d --no-deps gateway risk-adapter; then
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

if [[ "${DEPLOY_MODE}" == "full" && "${RUN_READY_FULL_SMOKE}" == "1" ]]; then
  echo "[deploy] full deploy: running make ready-full..."
  if ! make ready-full; then
    make compose-diagnostics || true
    fail_after_env_backup "full runtime readiness failed"
  fi
fi

if [[ "${PRUNE_DANGLING_IMAGES}" == "1" ]]; then
  echo "[deploy] pruning dangling docker images..."
  if ! docker image prune -f --filter dangling=true >/dev/null; then
    fail_after_env_backup "dangling docker image prune failed"
  fi
fi

echo "[deploy] done"
REMOTE
