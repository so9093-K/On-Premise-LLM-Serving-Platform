#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
ENV_FILE="${ENV_FILE:-.env}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
source scripts/lib/compose_context.sh
compose_context_init "$ROOT"

if [[ -f "$COMPOSE_FILE_ABS" ]]; then
  echo "[down] stopping compose stack: ${COMPOSE_FILE_ABS} (project=${COMPOSE_PROJECT_NAME_EFFECTIVE})"
  # compose 파일은 필수 변수에 :?를 사용하므로, .env에 값이 있을 때 missing-var
  # 에러가 나지 않도록 --env-file을 명시적으로 넘긴다.
  # compose down이 그래도 실패하면(예: .env에 필수 image 변수가 없는 경우),
  # project label 기준으로 컨테이너를 중지하는 방식으로 fallback한다.
  if ! compose_context_run down --remove-orphans 2>/dev/null; then
    echo "[down] compose down failed (missing env vars); stopping containers by project label"
    project="$COMPOSE_PROJECT_NAME_EFFECTIVE"
    containers="$(docker ps -q --filter "label=com.docker.compose.project=${project}" 2>/dev/null || true)"
    if [[ -n "$containers" ]]; then
      echo "$containers" | xargs docker stop 2>/dev/null || true
      echo "$containers" | xargs docker rm 2>/dev/null || true
    fi
    docker network ls --filter "label=com.docker.compose.project=${project}" -q 2>/dev/null \
      | xargs -r docker network rm 2>/dev/null || true
  fi
fi

for name in gateway risk_adapter; do
  pid_file="run/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "[down] stopping ${name} pid ${pid}"
      kill "$pid"
    fi
    rm -f "$pid_file"
  fi
done
