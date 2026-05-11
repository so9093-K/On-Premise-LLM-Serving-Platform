#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible strict readiness gate. Prefer make ready-local for app-only health
# and make ready-full for full vLLM readiness.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/ready_full.sh" "$@"
