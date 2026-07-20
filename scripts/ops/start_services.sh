#!/usr/bin/env bash
set -euo pipefail

# 서비스 시작의 canonical entrypoint. compose 호환 alias는 scripts/ops/up_services.sh이다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/up_services.sh" "$@"
