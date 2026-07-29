#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

echo "[build] running static validation"
PYTHON_BIN="$PYTHON_BIN" make validate

echo "[build] running deterministic tests"
PYTHON_BIN="$PYTHON_BIN" make test

echo "[build] packaging release"
# make validate가 같은 source tree의 계약 검증을 이미 완료했다. package 단독 실행은
# 자체 검증을 유지하되, 이 통합 경로에서는 같은 검사를 반복하지 않는다.
PACKAGE_SKIP_VALIDATION=1 PYTHON_BIN="$PYTHON_BIN" bash scripts/build/package_release.sh

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
