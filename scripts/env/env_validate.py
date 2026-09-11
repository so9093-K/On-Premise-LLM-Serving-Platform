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

from ai_model_serving.settings_parts.env import DEFAULT_ENV_FILENAME  # noqa: E402
from scripts.lib.env_cli import print_env_error, resolve_path  # noqa: E402
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402
from ai_model_serving.access_profile import access_profile_mismatches  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one env file using the project strict dotenv subset.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILENAME)
    args = parser.parse_args()

    env_path = resolve_path(args.env_file)
    try:
        values = load_strict_env_file(env_path)
        access_profile = values.get("ACCESS_PROFILE", "").strip()
        if access_profile:
            mismatches = access_profile_mismatches(access_profile, values, ROOT)
            if mismatches:
                raise RuntimeError(
                    f"ACCESS_PROFILE={access_profile!r} does not match resolved policy: "
                    + "; ".join(mismatches)
                )
    except (OSError, RuntimeError, ValueError) as exc:
        print_env_error(env_path, str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
