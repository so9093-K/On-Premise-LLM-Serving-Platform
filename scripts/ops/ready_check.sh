#!/usr/bin/env bash
set -euo pipefail

# 하위 호환을 위한 strict readiness gate. app-only health 확인은 make ready-local을,
# 전체 vLLM readiness 확인은 make ready-full을 사용하는 것을 권장한다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/ready_full.sh" "$@"
