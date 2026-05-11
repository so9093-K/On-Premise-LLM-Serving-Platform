#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import sys

MIN_VERSION = (3, 12)
MAX_EXCLUSIVE = (3, 15)
RECOMMENDED_MINOR = (3, 12)
SUPPORTED_LABEL = "Python >=3.12,<3.15"


def version_label(info: tuple[int, int, int] | None = None) -> str:
    if info is None:
        return platform.python_version()
    return ".".join(str(part) for part in info)


def is_supported(version: tuple[int, int]) -> bool:
    return MIN_VERSION <= version < MAX_EXCLUSIVE


def main() -> None:
    parser = argparse.ArgumentParser(description="프로젝트 CPython 버전 정책을 확인합니다.")
    parser.add_argument(
        "--strict-recommended",
        action="store_true",
        help="ALLOW_NON_RECOMMENDED_PYTHON=1이 아니면 권장 production minor version을 요구합니다.",
    )
    parser.add_argument(
        "--context",
        default="command",
        help="오류 메시지에 표시할 사람이 읽기 쉬운 command context입니다.",
    )
    args = parser.parse_args()

    current_minor = sys.version_info[:2]
    current_full = sys.version_info[:3]
    if not is_supported(current_minor):
        print(
            f"[python] {args.context} requires {SUPPORTED_LABEL}; current interpreter is {version_label(current_full)}.",
            file=sys.stderr,
        )
        print(
            "[python] Use Python 3.12, 3.13, or 3.14. Production full-stack GPU/vLLM validation is per minor version.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if args.strict_recommended and current_minor != RECOMMENDED_MINOR and os.getenv("ALLOW_NON_RECOMMENDED_PYTHON") != "1":
        print(
            f"[python] {args.context} is validated for the recommended production runtime Python 3.12.x; current interpreter is {version_label(current_full)}.",
            file=sys.stderr,
        )
        print(
            "[python] Set ALLOW_NON_RECOMMENDED_PYTHON=1 only after validating dependencies for this Python minor.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    recommendation = "recommended production minor" if current_minor == RECOMMENDED_MINOR else "supported app/control-plane minor"
    print(f"[python] {version_label(current_full)} ok ({recommendation}; {SUPPORTED_LABEL})")


if __name__ == "__main__":
    main()
