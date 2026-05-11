#!/usr/bin/env bash
set -euo pipefail

# Canonical service startup entrypoint. The compose-compatible alias is scripts/ops/up_services.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/up_services.sh" "$@"
