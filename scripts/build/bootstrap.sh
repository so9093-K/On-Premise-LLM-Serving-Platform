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

echo "[bootstrap] removing stale .venv (if any)"
rm -rf .venv

# .venv 삭제 후 시스템 python3.12를 찾는다.
# 활성화된 venv가 PATH에 있으면 command -v가 삭제된 venv 경로를 반환할 수 있으므로
# 시스템 경로를 명시적으로 우선 탐색한다.
PYTHON312=""
for p in /usr/bin/python3.12 /usr/local/bin/python3.12; do
  if [[ -x "$p" ]]; then
    PYTHON312="$p"
    break
  fi
done
if [[ -z "$PYTHON312" ]]; then
  PYTHON312="$(PATH="/usr/bin:/usr/local/bin:/bin:/usr/sbin:$PATH" command -v python3.12 2>/dev/null || true)"
fi
if [[ -z "$PYTHON312" ]]; then
  echo "[bootstrap] python3.12 not found. Install python3.12 and retry." >&2
  exit 1
fi

echo "[bootstrap] creating .venv with $PYTHON312"
"$PYTHON312" -m venv .venv
VENV_PYTHON="$ROOT/.venv/bin/python"

echo "[bootstrap] installing dependencies"
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install --requirement requirements.lock -q
"$VENV_PYTHON" -m pip install --no-deps -e ".[contract]" -q

echo "[bootstrap] initializing .env"
# Read the current auth mode before init-env-compose-force resets it.
# This lets bootstrap preserve a non-default mode (e.g. local_open) across re-runs
# without requiring the caller to pass AUTH_MODE every time.
_prior_auth_mode=""
if [[ -z "${AUTH_MODE:-}" && -f .env ]]; then
  _prior_auth_mode="$(grep -E '^AUTH_MODE=' .env | cut -d= -f2- || true)"
fi

if [[ -f .env ]]; then
  PYTHON_BIN="$VENV_PYTHON" make init-env-compose-force
else
  PYTHON_BIN="$VENV_PYTHON" make init-env-compose
fi

# Apply auth mode after .env re-init:
#   1. Explicit AUTH_MODE env var takes priority.
#   2. Mode preserved from the previous .env is restored when not overridden.
#   3. private_network is the compose default — no re-apply needed.
_apply_mode="${AUTH_MODE:-${_prior_auth_mode}}"
if [[ -n "$_apply_mode" && "$_apply_mode" != "custom" && "$_apply_mode" != "private_network" ]]; then
  echo "[bootstrap] applying AUTH_MODE=$_apply_mode"
  "$VENV_PYTHON" scripts/auth/auth_apply.py --mode "$_apply_mode" --yes
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

COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.example.yaml}"
if docker compose -f "$COMPOSE_FILE" --env-file .env ps --status running --quiet 2>/dev/null | grep -q .; then
  echo "[bootstrap] 실행 중인 스택에서 gateway/risk-adapter를 재시작합니다 (토큰 갱신 반영)"
  docker compose -f "$COMPOSE_FILE" --env-file .env up -d --no-deps gateway risk-adapter
  echo "[bootstrap] gateway/risk-adapter 재시작 완료"
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
echo "  make ready"
