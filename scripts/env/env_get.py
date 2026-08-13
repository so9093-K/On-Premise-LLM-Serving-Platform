#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _print_env_error(path: Path, message: str) -> None:
    print(f"[env] invalid env file: {path}", file=sys.stderr)
    print("", file=sys.stderr)
    for line in message.splitlines():
        print(f"  {line}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix: keep strict KEY=VALUE lines with no duplicates, quotes, inline comments, or export syntax.", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read one env value using the project strict dotenv subset.")
    parser.add_argument("key")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--default", default="")
    args = parser.parse_args()

    if args.key in os.environ:
        print(os.environ[args.key])
        return 0
    env_path = _path(args.env_file)
    try:
        values = load_strict_env_file(env_path)
    except RuntimeError as exc:
        _print_env_error(env_path, str(exc))
        return 2
    print(values.get(args.key, args.default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
