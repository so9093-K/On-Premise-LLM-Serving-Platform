#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
source scripts/lib/load_env.sh
load_local_env .env
VERSION="$(cat VERSION)"
IMAGE="${PLATFORM_IMAGE:-ai-model-serving-platform:${VERSION}}"
echo "[image] building platform image ${IMAGE}"
docker build -t "$IMAGE" .
echo "[image] built ${IMAGE}"
