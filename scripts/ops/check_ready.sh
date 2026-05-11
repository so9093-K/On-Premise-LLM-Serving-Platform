#!/usr/bin/env bash
set -euo pipefail

# Verbose alias for readiness verification.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/ready_check.sh" "$@"
