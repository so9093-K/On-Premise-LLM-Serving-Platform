#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

"$PYTHON_BIN" scripts/build/check_python.py --context validate >/dev/null
"$PYTHON_BIN" scripts/validation/release_check.py
