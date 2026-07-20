#!/usr/bin/env bash
set -euo pipefail

# 서비스 중지의 canonical entrypoint. compose 호환 alias는 scripts/ops/down_services.sh이다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/down_services.sh" "$@"
