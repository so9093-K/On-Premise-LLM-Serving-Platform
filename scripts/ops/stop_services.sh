#!/usr/bin/env bash
set -euo pipefail

# Canonical service stop entrypoint. The compose-compatible alias is scripts/ops/down_services.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/down_services.sh" "$@"
