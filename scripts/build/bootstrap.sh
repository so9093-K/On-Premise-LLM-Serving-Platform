#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "[bootstrap] docker CLI is required because bootstrap builds the platform and risk vLLM images." >&2
  exit 2
fi

if ! docker info >/dev/null 2>&1; then
  echo "[bootstrap] cannot access the Docker daemon." >&2
  echo "[bootstrap] Fix Docker permissions first, then rerun make first-run." >&2
  exit 2
fi

# Select a supported system interpreter before deleting an active/stale .venv.
# Prefer an explicit PYTHON_BIN, then the recommended production minor.
SYSTEM_PYTHON=""
python_candidates=()
if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_candidates+=("${PYTHON_BIN}")
fi
for command_name in python3.12 python3.13 python3.14; do
  candidate="$(PATH="/usr/bin:/usr/local/bin:/bin:/usr/sbin:$PATH" command -v "$command_name" 2>/dev/null || true)"
  [[ -n "$candidate" ]] && python_candidates+=("$candidate")
done
for candidate in "${python_candidates[@]}"; do
  case "$candidate" in
    "$ROOT/.venv/"*) continue ;;
  esac
  if [[ -x "$candidate" ]] && "$candidate" -c \
    'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] < (3, 15) else 1)'; then
    SYSTEM_PYTHON="$candidate"
    break
  fi
done
if [[ -z "$SYSTEM_PYTHON" ]]; then
  echo "[bootstrap] Python >=3.12,<3.15 not found. Install Python 3.12, 3.13, or 3.14 and retry." >&2
  exit 1
fi

echo "[bootstrap] removing stale .venv (if any)"
rm -rf .venv
echo "[bootstrap] creating .venv with $SYSTEM_PYTHON"
"$SYSTEM_PYTHON" -m venv .venv
VENV_PYTHON="$ROOT/.venv/bin/python"

echo "[bootstrap] installing dependencies"
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install --requirement requirements.lock -q
"$VENV_PYTHON" -m pip install --no-deps -e ".[contract]" -q

echo "[bootstrap] initializing .env"
# Read the current auth/exposure modes before init-env-compose-force resets them.
# This lets bootstrap preserve non-default modes across re-runs without requiring
# the caller to pass them every time.
_prior_auth_mode=""
if [[ -z "${AUTH_MODE:-}" && -f .env ]]; then
  _prior_auth_mode="$(grep -E '^AUTH_MODE=' .env | cut -d= -f2- || true)"
fi
_prior_exposure_mode=""
_prior_exposure_audience=""
if [[ -z "${EXPOSURE_MODE:-}" && -f .env ]]; then
  _prior_exposure_mode="$(grep -E '^EXPOSURE_MODE=' .env | cut -d= -f2- || true)"
  _prior_exposure_audience="$(grep -E '^EXPOSURE_AUDIENCE=' .env | cut -d= -f2- || true)"
fi

if [[ -f .env ]]; then
  PYTHON_BIN="$VENV_PYTHON" make init-env-compose-force
else
  PYTHON_BIN="$VENV_PYTHON" make init-env-compose
fi

# Apply auth mode after .env re-init:
#   1. Explicit AUTH_MODE env var takes priority.
#   2. Mode preserved from the previous .env is restored when not overridden.
#   3. No mode means keep whatever setup_env generated (local_open by default).
#   Note: private_network is not assumed as a "compose default" — it is treated
#   like any other named profile and applied explicitly if set.
_apply_mode="${AUTH_MODE:-${_prior_auth_mode}}"
if [[ -n "$_apply_mode" && "$_apply_mode" != "custom" ]]; then
  echo "[bootstrap] applying AUTH_MODE=$_apply_mode"
  "$VENV_PYTHON" scripts/auth/auth_apply.py --mode "$_apply_mode" --yes
fi

# Apply exposure mode after .env re-init:
#   1. Explicit EXPOSURE_MODE env var takes priority.
#   2. Mode preserved from the previous .env is restored when not overridden.
#   3. Empty means keep whatever setup_env generated
#      (local_open => master_open/private_lan).
_apply_exposure="${EXPOSURE_MODE:-${_prior_exposure_mode}}"
if [[ -n "$_apply_exposure" ]]; then
  _effective_audience="${EXPOSURE_AUDIENCE:-${_prior_exposure_audience}}"
  echo "[bootstrap] applying EXPOSURE_MODE=$_apply_exposure"
  if [[ -n "$_effective_audience" ]]; then
    "$VENV_PYTHON" scripts/auth/exposure_apply.py --mode "$_apply_exposure" --audience "$_effective_audience" --yes
  else
    "$VENV_PYTHON" scripts/auth/exposure_apply.py --mode "$_apply_exposure" --yes
  fi
fi

# Inject HF_TOKEN whenever the caller passes one — always override, not only when empty.
if [[ -n "${HF_TOKEN:-}" ]]; then
  echo "[bootstrap] injecting HF_TOKEN from environment into .env"
  sed -i "s|^HF_TOKEN=.*|HF_TOKEN=${HF_TOKEN}|" .env
  sed -i "s|^HUGGING_FACE_HUB_TOKEN=.*|HUGGING_FACE_HUB_TOKEN=${HF_TOKEN}|" .env
fi

# Warn if HF_TOKEN is still empty — compose-up will fail at model pull.
resolved_token="$(grep -E '^HF_TOKEN=' .env | cut -d= -f2- || true)"
if [[ -z "$resolved_token" ]]; then
  echo ""
  echo "[bootstrap] WARNING: HF_TOKEN is not set in .env." >&2
  echo "  google/embeddinggemma-300m requires a Hugging Face token with Gemma license acceptance." >&2
  echo "  Set it before 'make compose-up':" >&2
  echo "    HF_TOKEN=hf_xxx make first-run   (re-run to inject)" >&2
  echo "  or edit .env directly:  HF_TOKEN=hf_xxx" >&2
  echo ""
fi

echo "[bootstrap] validating contracts"
PYTHON_BIN="$VENV_PYTHON" make validate

echo "[bootstrap] running tests"
PYTHON_BIN="$VENV_PYTHON" make test

echo "[bootstrap] building platform docker image"
PYTHON_BIN="$VENV_PYTHON" make build-image

if [[ "${SKIP_RISK_VLLM_IMAGE_BUILD:-0}" == "1" ]]; then
  echo "[bootstrap] skipping risk vLLM image build because SKIP_RISK_VLLM_IMAGE_BUILD=1" >&2
elif [[ "${SKIP_RISK_VLLM_IMAGE_BUILD:-0}" == "auto" && -f .env ]]; then
  source scripts/lib/load_env.sh
  load_local_env .env
  risk_image="${RISK_VLLM_IMAGE:-ai-model-serving-risk-vllm-kanana:$(cat VERSION)}"
  if docker image inspect "$risk_image" >/dev/null 2>&1; then
    echo "[bootstrap] risk vLLM image already exists: ${risk_image}"
  else
    echo "[bootstrap] building risk vLLM docker image"
    PYTHON_BIN="$VENV_PYTHON" make rebuild-risk-vllm
  fi
else
  echo "[bootstrap] building risk vLLM docker image"
  PYTHON_BIN="$VENV_PYTHON" make rebuild-risk-vllm
fi

if [[ "${SKIP_RISK_VLLM_CONFIG_CHECK:-0}" == "1" ]]; then
  echo "[bootstrap] skipping risk vLLM config check because SKIP_RISK_VLLM_CONFIG_CHECK=1" >&2
else
  echo "[bootstrap] checking Kanana configs inside risk vLLM image"
  PYTHON_BIN="$VENV_PYTHON" make risk-vllm-config-check
fi

COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
ENV_FILE=.env
source scripts/lib/compose_context.sh
compose_context_init "$ROOT"
if compose_context_run ps --status running --quiet 2>/dev/null | grep -q .; then
  echo "[bootstrap] 실행 중인 스택에서 gateway/admin-sidecar/risk-adapter를 재시작합니다"
  _exposure_mode="$("$VENV_PYTHON" scripts/env/env_get.py --env-file .env EXPOSURE_MODE --default private_network)"
  _exposure_override="$(
    "$VENV_PYTHON" scripts/compose/resolve_exposure_mode.py \
      "$_exposure_mode" --print-override-file
  )"
  _boot_override="$(mktemp "${TMPDIR:-/tmp}/main-model-boot.XXXXXX.yaml")"
  trap 'rm -f "$_boot_override"' EXIT
  "$VENV_PYTHON" scripts/models/render_main_model_boot_override.py \
    --env-file .env \
    --output "$_boot_override" >/dev/null
  _compose_args=("${COMPOSE_CONTEXT_FILE_ARGS[@]}")
  if [[ -n "$_exposure_override" ]]; then
    _compose_args+=(-f "$_exposure_override")
  fi
  _compose_args+=(-f "$_boot_override")
  docker compose "${_compose_args[@]}" --env-file "$ENV_FILE_ABS" config >/dev/null
  docker compose "${_compose_args[@]}" --env-file "$ENV_FILE_ABS" up -d --no-deps admin-sidecar
  docker compose "${_compose_args[@]}" --env-file "$ENV_FILE_ABS" up -d --no-deps gateway risk-adapter
  rm -f "$_boot_override"
  trap - EXIT
  echo "[bootstrap] gateway/admin-sidecar/risk-adapter 재시작 완료"
fi

if [[ -f .env ]]; then
  infisical_client_id="$(grep -E '^INFISICAL_CLIENT_ID=' .env | cut -d= -f2- || true)"
  if [[ -n "$infisical_client_id" ]]; then
    echo "[bootstrap] Infisical에 갱신된 시크릿을 동기화합니다"
    "$VENV_PYTHON" scripts/config/infisical_sync.py push || echo "[bootstrap] Infisical 동기화 실패 (비필수 — 스택은 정상 기동됩니다)" >&2
  fi
fi

echo ""
echo "[bootstrap] done."
if [[ -z "$resolved_token" ]]; then
  echo "  ACTION REQUIRED: add HF_TOKEN to .env before compose-up (see warning above)"
fi
echo "  source .venv/bin/activate"
echo "  make compose-up"
echo "  make ready-full"
