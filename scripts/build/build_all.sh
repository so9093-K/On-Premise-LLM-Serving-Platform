#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

echo "[build] validating project"
"$PYTHON_BIN" scripts/build/check_python.py --context validate >/dev/null
"$PYTHON_BIN" scripts/validation/validate_contracts.py

echo "[build] running deterministic tests"
PYTHON_BIN="$PYTHON_BIN" make test-full

if ! command -v docker >/dev/null 2>&1; then
  echo "[build] docker CLI is required because make build includes the platform image." >&2
  echo "[build] Use 'make package' when only the release ZIP is needed." >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "[build] cannot access the Docker daemon." >&2
  exit 2
fi
bash scripts/build/build_platform_image.sh

echo "[build] packaging release"
bash scripts/build/package_release.sh
