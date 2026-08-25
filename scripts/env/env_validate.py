#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.env_cli import print_env_error, resolve_path  # noqa: E402
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one env file using the project strict dotenv subset.")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    env_path = resolve_path(args.env_file)
    try:
        load_strict_env_file(env_path)
    except RuntimeError as exc:
        print_env_error(env_path, str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
