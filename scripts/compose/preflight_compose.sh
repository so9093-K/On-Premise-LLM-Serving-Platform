#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python || true)}"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "[preflight] fail: Python not found. Install Python >=3.12,<3.15 before compose preflight." >&2
  exit 1
fi

"$PYTHON_BIN" scripts/build/check_python.py --context preflight-compose >/dev/null
exec env ENV_FILE="$ENV_FILE" COMPOSE_FILE="$COMPOSE_FILE" \
  "$PYTHON_BIN" scripts/compose/preflight_compose.py "$@"
