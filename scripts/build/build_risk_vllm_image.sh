#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/risk_vllm_image.sh
source scripts/lib/load_env.sh
load_local_env .env
risk_vllm_resolve_images .env

VERSION="$(cat VERSION)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
IMAGE="$RISK_VLLM_IMAGE_RESOLVED"
BASE_IMAGE="$RISK_VLLM_BASE_IMAGE_RESOLVED"
TRANSFORMERS_MIN_VERSION="${RISK_VLLM_TRANSFORMERS_MIN_VERSION:-${RISK_VLLM_TRANSFORMERS_VERSION:-4.52.4}}"
MIN_TRANSFORMERS_VERSION="4.52.4"

version_lt() {
  local left="$1"
  local right="$2"
  "$PYTHON_BIN" - "$left" "$right" <<'PY'
from __future__ import annotations
import sys

def parse(value: str) -> tuple[int, int, int]:
    parts = value.strip().split(".")
    if not 2 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(2)
    nums = [int(part) for part in parts]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

raise SystemExit(0 if parse(sys.argv[1]) < parse(sys.argv[2]) else 1)
PY
}

set +e
version_lt "$TRANSFORMERS_MIN_VERSION" "$MIN_TRANSFORMERS_VERSION"
version_cmp_rc=$?
set -e
if [[ "$version_cmp_rc" == "2" ]]; then
  echo "[risk-vllm-image] invalid RISK_VLLM_TRANSFORMERS_MIN_VERSION=${TRANSFORMERS_MIN_VERSION}; using ${MIN_TRANSFORMERS_VERSION}" >&2
  TRANSFORMERS_MIN_VERSION="$MIN_TRANSFORMERS_VERSION"
elif [[ "$version_cmp_rc" == "0" ]]; then
  echo "[risk-vllm-image] requested transformers minimum=${TRANSFORMERS_MIN_VERSION} is below the Kanana minimum ${MIN_TRANSFORMERS_VERSION}; using ${MIN_TRANSFORMERS_VERSION}" >&2
  TRANSFORMERS_MIN_VERSION="$MIN_TRANSFORMERS_VERSION"
fi

echo "[risk-vllm-image] building ${IMAGE}"
echo "[risk-vllm-image] base=${BASE_IMAGE} transformers_min=${TRANSFORMERS_MIN_VERSION}"
docker build \
  --file ops/docker/Dockerfile.risk-vllm-kanana \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "TRANSFORMERS_MIN_VERSION=${TRANSFORMERS_MIN_VERSION}" \
  --tag "$IMAGE" \
  .
echo "[risk-vllm-image] built ${IMAGE}"
