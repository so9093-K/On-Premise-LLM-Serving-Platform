#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

"$PYTHON_BIN" scripts/build/check_python.py --context test >/dev/null
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 APP_ENV=test \
  VLLM_IMAGE="registry.example.com/vllm-unified@sha256:0000000000000000000000000000000000000000000000000000000000000000" \
  AUDIO_VLLM_IMAGE="registry.example.com/vllm-unified@sha256:1111111111111111111111111111111111111111111111111111111111111111" \
  "$PYTHON_BIN" -m pytest -q
