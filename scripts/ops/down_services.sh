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
  # Compose file uses :? for required vars; pass --env-file explicitly so
  # missing-var errors are avoided when .env has the values.
  # If compose down still fails (e.g. .env missing required image vars),
  # fall back to stopping containers by project label.
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
