#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

echo "[build] validating contracts"
"$PYTHON_BIN" scripts/validation/validate_contracts.py

echo "[build] running unit and contract tests"
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PYTHON_BIN" scripts/validation/run_tests.py

if command -v docker >/dev/null 2>&1; then
  bash scripts/build/build_platform_image.sh
else
  echo "[build] docker not found; platform image build skipped" >&2
fi

echo "[build] packaging release"
bash scripts/build/package_release.sh
