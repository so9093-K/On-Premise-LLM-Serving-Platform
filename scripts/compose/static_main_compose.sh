#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/gateway_runtime_state.sh

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
ENV_FILE="${ENV_FILE:-.env}"
DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET:-linux-nvidia-static}"
ENV_FILE_ABS="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$ENV_FILE")"
STATIC_COMPOSE_PROJECT_NAME="${STATIC_COMPOSE_PROJECT_NAME:-ai-model-serving-static}"
GATEWAY_RUNTIME_ENV_FILE="${GATEWAY_RUNTIME_ENV_FILE:-$ROOT/.runtime/env/${DEPLOYMENT_TARGET}-gateway.env}"
GATEWAY_RUNTIME_ENV_FILE="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$GATEWAY_RUNTIME_ENV_FILE")"

if [[ "${1:-}" == "down" && -f "$GATEWAY_RUNTIME_ENV_FILE" ]]; then
  # down은 이미 생성된 project를 정리하는 명령이다. 현재 source env에 static
  # profile이 없어도 중지를 막지 않도록 기존 projection을 그대로 사용한다.
  :
else
  "$PYTHON_BIN" scripts/env/env_validate.py --env-file "$ENV_FILE_ABS"
  "$PYTHON_BIN" scripts/config/render_service_env.py \
    --target "$DEPLOYMENT_TARGET" \
    --source-env "$ENV_FILE_ABS" \
    --output "$GATEWAY_RUNTIME_ENV_FILE"
fi

# The source env is only Compose interpolation input.  The generated projection is
# the sole env_file received by the Gateway container.
export COMPOSE_PROJECT_NAME="$STATIC_COMPOSE_PROJECT_NAME"
export DEPLOYMENT_TARGET
export GATEWAY_RUNTIME_ENV_FILE
# Compose 파일 목록은 configs/deployment_targets.yaml이 소유한다. 노출 진단도 같은
# 목록을 읽으므로 여기에 target 이름을 다시 적지 않는다.
COMPOSE_FILES=()
COMPOSE_FILE_PATHS=()
while IFS= read -r compose_file; do
  [[ -n "$compose_file" ]] || continue
  COMPOSE_FILES+=(-f "$compose_file")
  COMPOSE_FILE_PATHS+=("$compose_file")
done < <("$PYTHON_BIN" -c '
import sys
sys.path.insert(0, "src")
from pathlib import Path
from ai_model_serving.deployment_target import load_deployment_target
target = load_deployment_target(Path("configs/deployment_targets.yaml"), sys.argv[1])
if target.control_mode != "static":
    raise SystemExit(f"DEPLOYMENT_TARGET {target.target_id!r} is not a static target")
print("\n".join(target.compose_files))
' "$DEPLOYMENT_TARGET")

if [[ ${#COMPOSE_FILES[@]} -eq 0 ]]; then
  echo "[static-compose] $DEPLOYMENT_TARGET 의 compose_files를 읽지 못했습니다." >&2
  exit 2
fi

# 어떤 secret 파일이 필요한지는 Compose 정의가 소유한다. secret 이름이나 경로를
# 여기 다시 적으면 Compose가 바뀔 때 조용히 어긋난다.
#
# `down`은 secret 내용을 읽지 않고 파일이 없어도 성공하므로 정지 경로는 건드리지
# 않는다 -- 여기서 placeholder를 만들면 그 값이 남아 다음 기동의 bearer token으로
# 쓰이고 scrape가 조용히 401이 된다.
if [[ "${1:-}" != "down" ]]; then
  REQUIRED_SECRET_FILES=()
  while IFS= read -r secret_file; do
    [[ -n "$secret_file" ]] || continue
    REQUIRED_SECRET_FILES+=("$secret_file")
  done < <("$PYTHON_BIN" -c '
import sys
from pathlib import Path
import yaml
# Compose는 상대 경로를 첫 compose 파일의 디렉터리 기준으로 해석한다.
base = Path(sys.argv[1]).parent
for name in sys.argv[1:]:
    document = yaml.safe_load(Path(name).read_text(encoding="utf-8")) or {}
    for spec in (document.get("secrets") or {}).values():
        source = isinstance(spec, dict) and spec.get("file")
        if source:
            print((base / source).resolve())
' "${COMPOSE_FILE_PATHS[@]}" 2>/dev/null | sort -u)

  for secret_file in "${REQUIRED_SECRET_FILES[@]}"; do
    if [[ ! -s "$secret_file" ]]; then
      echo "[static-compose] $secret_file 이 없거나 비어 있습니다. .env는 유지하고 runtime secret만 복구합니다."
      "$PYTHON_BIN" scripts/config/setup_env.py --sync-runtime-secrets --output "$ENV_FILE_ABS" || true
    fi
    if [[ ! -s "$secret_file" ]]; then
      echo "[static-compose] $secret_file 복구에 실패했습니다. $ENV_FILE_ABS 의 ADMIN_API_KEY 또는 ADMIN_API_KEYS를 확인하세요." >&2
      exit 2
    fi
  done
fi

if [[ "${1:-}" == "up" ]]; then
  PLATFORM_IMAGE_EFFECTIVE="${PLATFORM_IMAGE:-$("$PYTHON_BIN" scripts/env/env_get.py --env-file "$ENV_FILE_ABS" PLATFORM_IMAGE)}"
  if ! ensure_platform_runtime_dir "$REQUEST_EVENT_LOG_DIR_RELPATH" "$PLATFORM_IMAGE_EFFECTIVE"; then
    echo "[static-compose] request event log directory is not usable" >&2
    exit 2
  fi
fi
exec docker compose \
  --project-name "$STATIC_COMPOSE_PROJECT_NAME" \
  "${COMPOSE_FILES[@]}" \
  --env-file "$ENV_FILE_ABS" \
  "$@"
