#!/usr/bin/env python3
"""pytest를 프로젝트 표준 환경(PYTHONDONTWRITEBYTECODE, APP_ENV=test 등)으로 실행하는
얇은 wrapper. `make test`(scripts/validation/run_test.sh)가 이걸 호출한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _check_python() -> None:
    if not ((3, 12) <= sys.version_info[:2] < (3, 15)):
        raise SystemExit(f"Python >=3.12,<3.15 is required for tests; got {sys.version.split()[0]}")


def main() -> int:
    _check_python()
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    os.environ["APP_ENV"] = "test"
    for path in (str(ROOT / "src"), str(ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import pytest

    args = sys.argv[1:] or ["-q"]
    # `python scripts/validation/run_tests.py -q`처럼 pytest option만 전달하면 pytest가
    # repository root 전체를 탐색해 dist/staging artifact까지 훑을 수 있다.
    # 이 wrapper의 계약은 항상 project test suite만 실행하는 것이므로,
    # 명시적인 path가 없으면 tests/를 자동으로 붙인다.
    has_explicit_path = any(not arg.startswith("-") for arg in args)
    if not has_explicit_path:
        args.append("tests")
    return int(pytest.main(args))


if __name__ == "__main__":
    raise SystemExit(main())
