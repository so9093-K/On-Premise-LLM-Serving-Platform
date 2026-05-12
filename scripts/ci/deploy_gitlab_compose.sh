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
#   RISK_VLLM_IMAGE_TO_DEPLOY  override RISK_VLLM_IMAGE in 175 .env
#   DEPLOY_COMPOSE_FILE        compose file relative to DEPLOY_PATH
#                              default: ops/compose/full-stack.private-network.yaml
#   DEPLOY_MODE                rolling (default) or full
#   GATEWAY_HEALTH_URL         explicit post-deploy health URL.
#                              Default is derived from 175 .env:
#                              GATEWAY_BIND_ADDR/GATEWAY_PORT, with 0.0.0.0 -> localhost.
#   RUN_READY_SMOKE            1 (default) or 0 — run /health check after deploy
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
  --exclude "models/" \
  --exclude "logs/" \
  --exclude "dist/" \
  --exclude "build/" \
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
  GATEWAY_HEALTH_URL="${GATEWAY_HEALTH_URL:-}" \
  RUN_READY_SMOKE="${RUN_READY_SMOKE}" \
  AUTH_MODE="${AUTH_MODE:-}" \
  bash -s <<'REMOTE'
set -euo pipefail

cd "${DEPLOY_PATH}"

if [[ ! -f .env ]]; then
  echo "[deploy] ERROR: .env not found at ${DEPLOY_PATH}/.env" >&2
  echo "[deploy] Run bootstrap on 175 first to generate .env" >&2
  exit 1
fi

# registry login with read-only deploy token
echo "${REGISTRY_PASSWORD}" | \
  docker login "${CI_REGISTRY}" -u "${REGISTRY_USER}" --password-stdin

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> .env
  fi
}

get_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env
}

# update PLATFORM_IMAGE in .env
set_env_value PLATFORM_IMAGE "${PLATFORM_IMAGE_TO_DEPLOY}"
echo "[deploy] PLATFORM_IMAGE set to ${PLATFORM_IMAGE_TO_DEPLOY}"

# optionally update RISK_VLLM_IMAGE
if [[ -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
  set_env_value RISK_VLLM_IMAGE "${RISK_VLLM_IMAGE_TO_DEPLOY}"
  echo "[deploy] RISK_VLLM_IMAGE set to ${RISK_VLLM_IMAGE_TO_DEPLOY}"
fi

if [[ -n "${AUTH_MODE:-}" ]]; then
  echo "[deploy] applying auth profile: ${AUTH_MODE}"
  make auth-apply MODE="${AUTH_MODE}"
fi

if [[ "${DEPLOY_MODE}" == "full" ]]; then
  echo "[deploy] full deploy: pulling all compose images..."
  docker compose -f "${COMPOSE_FILE}" --env-file .env pull
  echo "[deploy] full deploy: starting stack..."
  docker compose -f "${COMPOSE_FILE}" --env-file .env up -d --remove-orphans
else
  # pull gateway + risk-adapter images only (vLLM images are large; pull separately when needed)
  echo "[deploy] rolling deploy: pulling gateway and risk-adapter images..."
  docker compose -f "${COMPOSE_FILE}" --env-file .env pull gateway risk-adapter

  # rolling restart: gateway + risk-adapter (no vLLM downtime)
  echo "[deploy] rolling deploy: restarting gateway and risk-adapter..."
  docker compose -f "${COMPOSE_FILE}" --env-file .env up -d --no-deps gateway risk-adapter
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
      echo "[deploy] ERROR: gateway /health not ready after 600s" >&2
      exit 1
    fi
    echo "[deploy] waiting... ${i}/60 (${HEALTH_URL})"
    sleep 10
  done
fi

echo "[deploy] done"
REMOTE
