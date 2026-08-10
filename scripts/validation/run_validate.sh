#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"

"$PYTHON_BIN" scripts/build/check_python.py --context validate >/dev/null

echo "==> 계약 검증"
"$PYTHON_BIN" scripts/validation/validate_contracts.py
echo "==> shell 스크립트 구문 검증"
"$PYTHON_BIN" scripts/validation/validate_shell_syntax.py
echo "==> exposure profiles 구조 검증"
"$PYTHON_BIN" scripts/validation/validate_exposure_profiles.py --strict
echo "==> compose override drift check"
"$PYTHON_BIN" scripts/compose/render_exposure_overrides.py --check
echo "==> env contract 검증"
"$PYTHON_BIN" scripts/validation/validate_env_contract.py --strict
echo "==> runtime asset drift check"
"$PYTHON_BIN" scripts/render_runtime_assets.py --check
echo "==> OpenAPI snapshot diff"
"$PYTHON_BIN" scripts/validation/openapi_snapshot_diff.py
echo "==> auth profile 생성값 sanity"
"$PYTHON_BIN" scripts/auth/auth_profile_sanity.py
echo "정적 검증 완료"
