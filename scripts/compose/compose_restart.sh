#!/usr/bin/env bash
set -euo pipefail

# 실행 중인 compose 스택에서 특정 서비스만 안전하게 재기동합니다.
#
# `full-stack.private-network.yaml`의 gateway/risk-adapter/vLLM 서비스들은
# env_file(.env 전체)을 공유합니다. .env 값을 바꾼 뒤 `docker compose up -d
# <service>`를 그냥 실행하면, 그 서비스가 depends_on으로 물고 있는 다른
# 서비스들도 설정 해시가 바뀐 것으로 감지돼 함께 재생성됩니다(예: gateway를
# 재기동하려다 GPU에 모델이 올라간 main-llm-vllm까지 재로딩됨). 기본값은
# `--no-deps`로 대상 서비스만 건드리고, cascade가 실제로 필요할 때만
# WITH_DEPS=1로 켭니다.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"

SERVICES=("$@")
if [[ ${#SERVICES[@]} -eq 0 && -n "${SERVICE:-}" ]]; then
  # shellcheck disable=SC2206
  SERVICES=(${SERVICE})
fi
if [[ ${#SERVICES[@]} -eq 0 ]]; then
  echo "[compose-restart] 재기동할 서비스가 없습니다." >&2
  echo "[compose-restart] 사용법: SERVICE=\"gateway\" make compose-restart" >&2
  echo "[compose-restart]        또는 bash scripts/compose/compose_restart.sh gateway [service2 ...]" >&2
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[compose-restart] $ENV_FILE 파일이 없습니다." >&2
  exit 2
fi

source scripts/lib/compose_context.sh
compose_context_init "$ROOT"

_env_value() {
  local key="$1"
  "$PYTHON_BIN" scripts/env/env_get.py --env-file "$ENV_FILE" "$key"
}

EXPOSURE_MODE_EFFECTIVE="${EXPOSURE_MODE:-$(_env_value EXPOSURE_MODE)}"
EXPOSURE_MODE_EFFECTIVE="${EXPOSURE_MODE_EFFECTIVE:-master_open}"
CANONICAL_MODE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE_EFFECTIVE")"
COMPOSE_OVERRIDE="$("$PYTHON_BIN" scripts/compose/resolve_exposure_mode.py "$EXPOSURE_MODE_EFFECTIVE" --print-override-file)"

if [[ -n "$COMPOSE_OVERRIDE" && ! -f "$COMPOSE_OVERRIDE" ]]; then
  echo "[compose-restart] compose override file not found: $COMPOSE_OVERRIDE" >&2
  echo "[compose-restart] Run 'python scripts/compose/render_exposure_overrides.py' to generate it." >&2
  exit 2
fi

# main-llm-vllm이 대상이거나 WITH_DEPS로 의존 그래프를 함께 갱신할 수 있으니
# compose-up과 동일하게 persisted boot profile override를 항상 반영합니다.
MAIN_MODEL_BOOT_OVERRIDE="$(mktemp "${TMPDIR:-/tmp}/main-model-boot.XXXXXX.yaml")"
trap 'rm -f "$MAIN_MODEL_BOOT_OVERRIDE"' EXIT
"$PYTHON_BIN" scripts/models/render_main_model_boot_override.py \
  --catalog configs/main_model_profiles.yaml \
  --state .runtime/main-model/main-model-state.json \
  --env-file "$ENV_FILE" \
  --output "$MAIN_MODEL_BOOT_OVERRIDE" >/dev/null

COMPOSE_ARGS=("${COMPOSE_CONTEXT_FILE_ARGS[@]}")
if [[ -n "$COMPOSE_OVERRIDE" ]]; then
  COMPOSE_ARGS+=(-f "$COMPOSE_OVERRIDE")
fi
COMPOSE_ARGS+=(-f "$MAIN_MODEL_BOOT_OVERRIDE")

docker compose "${COMPOSE_ARGS[@]}" --env-file "$ENV_FILE_ABS" config >/dev/null

DEPS_FLAG=(--no-deps)
if [[ "${WITH_DEPS:-0}" == "1" ]]; then
  DEPS_FLAG=()
  echo "[compose-restart] WITH_DEPS=1 — ${SERVICES[*]}가 물고 있는 depends_on 서비스도 설정이 바뀌었으면 함께 재생성됩니다." >&2
else
  echo "[compose-restart] --no-deps 기본 적용 — ${SERVICES[*]}만 재기동합니다(의존 서비스는 건드리지 않음)." >&2
fi

echo "[compose-restart] restarting: ${SERVICES[*]} (EXPOSURE_MODE=$CANONICAL_MODE)"
docker compose "${COMPOSE_ARGS[@]}" --env-file "$ENV_FILE_ABS" up -d "${DEPS_FLAG[@]}" "${SERVICES[@]}"
