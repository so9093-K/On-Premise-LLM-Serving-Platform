#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

echo "[build] running static validation"
PYTHON_BIN="$PYTHON_BIN" make validate

echo "[build] running deterministic tests"
PYTHON_BIN="$PYTHON_BIN" make test

# Docker 사전 조건과 image provenance 출력은 단독 build-image와 같은 경로인
# build_platform_image.sh가 소유한다.
bash scripts/build/build_platform_image.sh
