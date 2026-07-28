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

# 활성/stale .venv를 삭제하기 전에 지원되는 system interpreter를 선택한다.
# 명시적인 PYTHON_BIN을 우선하고, 그 다음 권장 production minor 버전을 사용한다.
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
# init-env-compose-force가 초기화하기 전에 현재 auth/exposure mode를 읽어둔다.
# 이렇게 하면 재실행 시마다 호출자가 매번 값을 넘기지 않아도, bootstrap이
# 기본값이 아닌 mode를 유지할 수 있다.
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

# .env 재초기화 이후 auth mode를 적용한다:
#   1. 명시적인 AUTH_MODE 환경변수가 최우선이다.
#   2. override되지 않으면 이전 .env에서 보존된 mode를 복원한다.
#   3. mode가 없으면 setup_env가 생성한 값을 그대로 유지한다(기본값 local_open).
#   참고: private_network는 "compose 기본값"으로 가정하지 않는다 — 다른 named
#   profile과 동일하게 취급되며, 설정된 경우에만 명시적으로 적용된다.
_apply_mode="${AUTH_MODE:-${_prior_auth_mode}}"
if [[ -n "$_apply_mode" && "$_apply_mode" != "custom" ]]; then
  echo "[bootstrap] applying AUTH_MODE=$_apply_mode"
  "$VENV_PYTHON" scripts/auth/auth_apply.py --mode "$_apply_mode" --yes
fi

# .env 재초기화 이후 exposure mode를 적용한다:
#   1. 명시적인 EXPOSURE_MODE 환경변수가 최우선이다.
#   2. override되지 않으면 이전 .env에서 보존된 mode를 복원한다.
#   3. 비어있으면 setup_env가 생성한 값을 그대로 유지한다
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

# 호출자가 HF_TOKEN을 넘길 때마다 주입한다 — 비어있을 때만이 아니라 항상 override한다.
if [[ -n "${HF_TOKEN:-}" ]]; then
  echo "[bootstrap] injecting HF_TOKEN from environment into .env"
  sed -i "s|^HF_TOKEN=.*|HF_TOKEN=${HF_TOKEN}|" .env
  sed -i "s|^HUGGING_FACE_HUB_TOKEN=.*|HUGGING_FACE_HUB_TOKEN=${HF_TOKEN}|" .env
fi

# HF_TOKEN이 여전히 비어있으면 경고한다 — compose-up이 model pull 단계에서 실패한다.
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
  risk_image="${RISK_VLLM_IMAGE:-ai-model-serving-vllm-unified:$(cat VERSION)}"
  if docker image inspect "$risk_image" >/dev/null 2>&1; then
    echo "[bootstrap] risk vLLM image already exists: ${risk_image}"
  else
    echo "[bootstrap] building risk vLLM docker image"
    PYTHON_BIN="$VENV_PYTHON" make rebuild-vllm-unified
  fi
else
  echo "[bootstrap] building risk vLLM docker image"
  PYTHON_BIN="$VENV_PYTHON" make rebuild-vllm-unified
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

echo ""
echo "[bootstrap] done."
if [[ -z "$resolved_token" ]]; then
  echo "  ACTION REQUIRED: add HF_TOKEN to .env before compose-up (see warning above)"
fi
echo "  source .venv/bin/activate"
echo "  make compose-up"
echo "  make ready-full"
